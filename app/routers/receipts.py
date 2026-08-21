from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import require_user
from app.database import get_db
from app.models import Receipt, Role
from app.services.receipts import MAX_PROOF_IMAGE_BYTES, ReceiptError, create_receipt
from app.templates import templates

router = APIRouter(prefix="/receipts")
RECEIPT_ROLES = {Role.SUPER_ADMIN, Role.ADMIN, Role.SALES}


def _require_receipt_user(request: Request, db: Session):
    return require_user(request, db, RECEIPT_ROLES)


@router.get("")
def receipt_list(request: Request, db: Session = Depends(get_db)):
    user = _require_receipt_user(request, db)
    receipts = db.scalars(
        select(Receipt)
        .options(selectinload(Receipt.created_by))
        .order_by(Receipt.created_at.desc())
        .limit(250)
    ).all()
    return templates.TemplateResponse(
        request,
        "receipts.html",
        {
            "request": request,
            "user": user,
            "receipts": receipts,
            "today": date.today().isoformat(),
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("")
async def submit_receipt(
    request: Request,
    receipt_date: date = Form(...),
    proof: UploadFile = File(...),
    utr_number: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _require_receipt_user(request, db)
    image = await proof.read(MAX_PROOF_IMAGE_BYTES + 1)
    await proof.close()
    try:
        create_receipt(
            db,
            user=user,
            receipt_date=receipt_date,
            proof_image=image,
            proof_content_type=proof.content_type,
            utr_number=utr_number,
        )
    except ReceiptError as exc:
        db.rollback()
        return RedirectResponse(
            "/receipts?" + urlencode({"error": str(exc)}), status_code=303
        )
    return RedirectResponse(
        "/receipts?" + urlencode({"message": "Receipt queued for Master review."}),
        status_code=303,
    )


@router.get("/{receipt_id}/proof")
def receipt_proof(receipt_id: int, request: Request, db: Session = Depends(get_db)):
    _require_receipt_user(request, db)
    receipt = db.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found.")
    return Response(
        receipt.proof_image,
        media_type=receipt.proof_content_type,
        headers={
            "Cache-Control": "private, max-age=300",
            "Content-Disposition": f'inline; filename="receipt-{receipt.receipt_uuid}.img"',
            "X-Content-Type-Options": "nosniff",
        },
    )
