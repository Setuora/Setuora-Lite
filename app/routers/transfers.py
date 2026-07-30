from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import require_user
from app.database import get_db
from app.models import (
    LocalTransfer,
    LocalTransferItem,
    Role,
    TransferDirection,
    TransferStatus,
)
from app.services.transfer import (
    TransferError,
    add_outbound_serial,
    create_outbound_transfer,
    dispatch_outbound_transfer,
    finalize_inbound_receipt,
    remove_outbound_serial,
    scan_inbound_transfer_item,
)
from app.templates import templates


router = APIRouter(prefix="/transfers")
TRANSFER_ROLES = {Role.SUPER_ADMIN, Role.ADMIN, Role.WAREHOUSE_MANAGER}
RECEIVABLE_STATUSES = {
    TransferStatus.AWAITING_RECEIPT.value,
    TransferStatus.PARTIALLY_RECEIVED.value,
}


def _require_transfer_user(request: Request, db: Session):
    return require_user(request, db, TRANSFER_ROLES)


def _transfer_query():
    return select(LocalTransfer).options(
        selectinload(LocalTransfer.created_by),
        selectinload(LocalTransfer.items).selectinload(LocalTransferItem.serial),
    )


def _get_transfer(db: Session, transfer_id: int) -> LocalTransfer:
    transfer = db.scalar(
        _transfer_query().where(LocalTransfer.id == transfer_id)
    )
    if transfer is None:
        raise HTTPException(status_code=404, detail="Transfer not found.")
    return transfer


def _wants_json(request: Request) -> bool:
    return (
        "application/json" in request.headers.get("accept", "")
        or request.headers.get("x-requested-with") == "fetch"
    )


def _error_response(
    request: Request,
    transfer: LocalTransfer,
    exc: TransferError,
):
    if _wants_json(request):
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    query = urlencode({"error": str(exc)})
    return RedirectResponse(
        f"/transfers/{transfer.id}?{query}",
        status_code=303,
    )


@router.get("")
def transfer_list(request: Request, db: Session = Depends(get_db)):
    user = _require_transfer_user(request, db)
    transfers = db.scalars(
        _transfer_query().order_by(LocalTransfer.created_at.desc()).limit(250)
    ).all()
    return templates.TemplateResponse(
        request,
        "transfers.html",
        {
            "request": request,
            "user": user,
            "transfers": transfers,
            "message": request.query_params.get("message"),
        },
    )


@router.get("/new")
def new_transfer_page(request: Request, db: Session = Depends(get_db)):
    user = _require_transfer_user(request, db)
    return templates.TemplateResponse(
        request,
        "transfer_new.html",
        {
            "request": request,
            "user": user,
            "peer_code": "",
            "notes": "",
            "error": None,
        },
    )


@router.post("")
def create_transfer(
    request: Request,
    peer_code: str = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _require_transfer_user(request, db)
    try:
        transfer = create_outbound_transfer(db, user, peer_code, notes)
    except TransferError as exc:
        db.rollback()
        return templates.TemplateResponse(
            request,
            "transfer_new.html",
            {
                "request": request,
                "user": user,
                "peer_code": peer_code,
                "notes": notes,
                "error": str(exc),
            },
            status_code=400,
        )
    return RedirectResponse(f"/transfers/{transfer.id}", status_code=303)


@router.get("/{transfer_id}")
def transfer_detail(
    request: Request,
    transfer_id: int,
    db: Session = Depends(get_db),
):
    user = _require_transfer_user(request, db)
    transfer = _get_transfer(db, transfer_id)
    scanned_count = sum(1 for item in transfer.items if item.scanned)
    received_count = sum(1 for item in transfer.items if item.received)
    return templates.TemplateResponse(
        request,
        "transfer_detail.html",
        {
            "request": request,
            "user": user,
            "transfer": transfer,
            "is_outbound": transfer.direction == TransferDirection.OUTBOUND.value,
            "is_draft": transfer.status == TransferStatus.DRAFT.value,
            "is_receivable": transfer.status in RECEIVABLE_STATUSES,
            "scanned_count": scanned_count,
            "received_count": received_count,
            "error": request.query_params.get("error"),
            "message": request.query_params.get("message"),
        },
    )


@router.post("/{transfer_id}/items")
def add_transfer_item(
    request: Request,
    transfer_id: int,
    serial_number: str = Form(...),
    db: Session = Depends(get_db),
):
    _require_transfer_user(request, db)
    transfer = _get_transfer(db, transfer_id)
    try:
        item = add_outbound_serial(db, transfer, serial_number)
    except TransferError as exc:
        db.rollback()
        return _error_response(request, transfer, exc)
    if _wants_json(request):
        return JSONResponse(
            {
                "ok": True,
                "serial": item.manifest_serial_number,
                "product": item.product_name,
                "item_count": len(transfer.items),
            }
        )
    return RedirectResponse(f"/transfers/{transfer.id}", status_code=303)


@router.post("/{transfer_id}/items/{item_id}/delete")
def delete_transfer_item(
    request: Request,
    transfer_id: int,
    item_id: int,
    db: Session = Depends(get_db),
):
    _require_transfer_user(request, db)
    transfer = _get_transfer(db, transfer_id)
    try:
        remove_outbound_serial(db, transfer, item_id)
    except TransferError as exc:
        db.rollback()
        return _error_response(request, transfer, exc)
    return RedirectResponse(f"/transfers/{transfer.id}", status_code=303)


@router.post("/{transfer_id}/dispatch")
def dispatch_transfer(
    request: Request,
    transfer_id: int,
    db: Session = Depends(get_db),
):
    user = _require_transfer_user(request, db)
    transfer = _get_transfer(db, transfer_id)
    try:
        dispatch_outbound_transfer(db, transfer, user)
    except TransferError as exc:
        db.rollback()
        return _error_response(request, transfer, exc)
    query = urlencode(
        {"message": "Transfer dispatched and queued for Setuora Master."}
    )
    return RedirectResponse(
        f"/transfers/{transfer.id}?{query}",
        status_code=303,
    )


@router.post("/{transfer_id}/receipt-scans")
def scan_transfer_receipt(
    request: Request,
    transfer_id: int,
    serial_number: str = Form(...),
    db: Session = Depends(get_db),
):
    _require_transfer_user(request, db)
    transfer = _get_transfer(db, transfer_id)
    try:
        item = scan_inbound_transfer_item(db, transfer, serial_number)
    except TransferError as exc:
        db.rollback()
        return _error_response(request, transfer, exc)
    scanned_count = sum(1 for value in transfer.items if value.scanned)
    if _wants_json(request):
        return JSONResponse(
            {
                "ok": True,
                "serial": item.manifest_serial_number,
                "product": item.product_name,
                "item_count": scanned_count,
            }
        )
    return RedirectResponse(f"/transfers/{transfer.id}", status_code=303)


@router.post("/{transfer_id}/receive")
def receive_transfer(
    request: Request,
    transfer_id: int,
    db: Session = Depends(get_db),
):
    user = _require_transfer_user(request, db)
    transfer = _get_transfer(db, transfer_id)
    try:
        finalize_inbound_receipt(db, transfer, user)
    except TransferError as exc:
        db.rollback()
        return _error_response(request, transfer, exc)
    query = urlencode(
        {"message": "Scanned stock received and the receipt queued for Master."}
    )
    return RedirectResponse(
        f"/transfers/{transfer.id}?{query}",
        status_code=303,
    )
