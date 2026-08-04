from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import require_user
from app.config import (
    FRANCHISE_CODE_PATTERN,
    FRANCHISE_CODE_PLACEHOLDERS,
    MASTER_API_KEY_PATTERN,
    get_settings,
    master_url_configuration_error,
    save_master_connection_settings,
)
from app.database import get_db
from app.models import (
    Batch,
    BatchStatus,
    MasterInboxCommand,
    MasterOutboxEvent,
    MasterOutboxStatus,
    Role,
    Serial,
    SerialStatus,
)
from app.services.master_sync import (
    MasterSyncError,
    enqueue_initial_inventory_snapshot,
    payload_sha256,
    poll_master_commands,
    push_pending_events,
    verify_master_enrollment_identity,
)
from app.templates import templates


router = APIRouter(prefix="/master-connection")
SYNC_ADMIN_ROLES = {Role.SUPER_ADMIN, Role.ADMIN}
MAX_SYNC_INTERVAL_SECONDS = 3600
MAX_REQUEST_TIMEOUT_SECONDS = 120


def _require_sync_admin(request: Request, db: Session):
    return require_user(request, db, SYNC_ADMIN_ROLES)


@router.get("")
def master_connection_page(request: Request, db: Session = Depends(get_db)):
    user = _require_sync_admin(request, db)
    settings = get_settings()
    status_rows = db.execute(
        select(MasterOutboxEvent.status, func.count(MasterOutboxEvent.id))
        .group_by(MasterOutboxEvent.status)
    ).all()
    counts = {status: count for status, count in status_rows}
    outbox_total = sum(int(count) for count in counts.values())
    initial_inventory_count = (
        db.scalar(
            select(func.count(Serial.id)).where(
                Serial.active.is_(True),
                Serial.status.in_(
                    {
                        SerialStatus.GENERATED.value,
                        SerialStatus.IN_STOCK.value,
                    }
                ),
            )
        )
        or 0
    )
    recent_events = db.scalars(
        select(MasterOutboxEvent)
        .order_by(MasterOutboxEvent.id.desc())
        .limit(25)
    ).all()
    recent_commands = db.scalars(
        select(MasterInboxCommand)
        .order_by(MasterInboxCommand.received_at.desc())
        .limit(10)
    ).all()
    last_sent = db.scalar(
        select(MasterOutboxEvent)
        .where(MasterOutboxEvent.status == MasterOutboxStatus.SENT.value)
        .order_by(MasterOutboxEvent.sent_at.desc())
        .limit(1)
    )
    return templates.TemplateResponse(
        request,
        "master_connection.html",
        {
            "request": request,
            "user": user,
            "sync_enabled": settings.master_sync_enabled,
            "franchise_code": settings.franchise_code or "Not configured",
            "master_url": settings.master_url or "Not configured",
            "api_key_configured": bool(settings.master_api_key),
            "interval_seconds": settings.master_sync_interval_seconds,
            "request_timeout_seconds": settings.master_request_timeout_seconds,
            "identity_locked": outbox_total > 0,
            "configuration_error": settings.master_sync_configuration_error,
            "counts": counts,
            "outbox_total": outbox_total,
            "initial_inventory_count": initial_inventory_count,
            "last_sent": last_sent,
            "recent_events": recent_events,
            "recent_commands": recent_commands,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
        },
    )


def _connection_form_error(message: str) -> RedirectResponse:
    return RedirectResponse(
        f"/master-connection?{urlencode({'error': message})}",
        status_code=303,
    )


@router.post("/settings")
def update_master_connection_settings(
    request: Request,
    franchise_code: Annotated[str, Form()],
    master_url: Annotated[str, Form()],
    master_api_key: Annotated[str, Form()] = "",
    master_sync_enabled: Annotated[str | None, Form()] = None,
    master_sync_interval_seconds: Annotated[str, Form()] = "30",
    master_request_timeout_seconds: Annotated[str, Form()] = "15",
    db: Session = Depends(get_db),
):
    _require_sync_admin(request, db)
    current = get_settings()
    normalized_code = franchise_code.strip().upper()
    normalized_url = master_url.strip().rstrip("/")
    credential = master_api_key.strip() or current.master_api_key
    sync_enabled = master_sync_enabled == "true"

    if (
        not normalized_code
        or normalized_code in FRANCHISE_CODE_PLACEHOLDERS
        or len(normalized_code) > 40
        or FRANCHISE_CODE_PATTERN.fullmatch(normalized_code) is None
    ):
        return _connection_form_error(
            "Franchise code must be a permanent code of at most 40 uppercase letters, numbers, and hyphens."
        )

    existing_event = db.scalar(select(MasterOutboxEvent.id).limit(1))
    if existing_event is not None and normalized_code != current.franchise_code:
        return _connection_form_error(
            "Franchise code is locked because this node already has outbox events."
        )

    url_error = master_url_configuration_error(normalized_url)
    if url_error:
        return _connection_form_error(url_error.replace("MASTER_URL", "Master URL"))
    if MASTER_API_KEY_PATTERN.fullmatch(credential) is None:
        return _connection_form_error(
            "Enter the node credential in setuora-node.<id>.<secret> format."
        )

    try:
        interval = int(master_sync_interval_seconds)
        timeout = int(master_request_timeout_seconds)
    except ValueError:
        return _connection_form_error("Sync interval and request timeout must be whole numbers.")
    if not 15 <= interval <= MAX_SYNC_INTERVAL_SECONDS:
        return _connection_form_error(
            f"Sync interval must be between 15 and {MAX_SYNC_INTERVAL_SECONDS} seconds."
        )
    if not 3 <= timeout <= MAX_REQUEST_TIMEOUT_SECONDS:
        return _connection_form_error(
            f"Request timeout must be between 3 and {MAX_REQUEST_TIMEOUT_SECONDS} seconds."
        )

    try:
        save_master_connection_settings(
            {
                "FRANCHISE_CODE": normalized_code,
                "MASTER_API_KEY": credential,
                "MASTER_REQUEST_TIMEOUT_SECONDS": str(timeout),
                "MASTER_SYNC_ENABLED": "true" if sync_enabled else "false",
                "MASTER_SYNC_INTERVAL_SECONDS": str(interval),
                "MASTER_TLS_VERIFY": "true",
                "MASTER_URL": normalized_url,
            }
        )
    except (OSError, ValueError) as exc:
        return _connection_form_error(f"Could not save Master connection settings: {exc}")

    return RedirectResponse(
        "/master-connection?"
        + urlencode(
            {
                "message": (
                    "Master connection settings saved. Use Sync now to verify connectivity."
                )
            }
        ),
        status_code=303,
    )


@router.post("/sync")
def sync_master_now(request: Request, db: Session = Depends(get_db)):
    _require_sync_admin(request, db)
    settings = get_settings()
    if not settings.master_sync_enabled:
        raise HTTPException(status_code=409, detail="Master synchronization is disabled.")
    if settings.master_sync_configuration_error:
        raise HTTPException(
            status_code=409,
            detail=settings.master_sync_configuration_error,
        )
    try:
        sent = push_pending_events(db)
        commands = poll_master_commands(db)
    except MasterSyncError as exc:
        query = urlencode({"error": str(exc)})
        return RedirectResponse(f"/master-connection?{query}", status_code=303)
    query = urlencode(
        {"message": f"Sync complete: {sent} event(s) sent, {commands} command(s) applied."}
    )
    return RedirectResponse(f"/master-connection?{query}", status_code=303)


@router.post("/initialize-inventory")
def initialize_inventory(
    request: Request,
    db: Session = Depends(get_db),
):
    user = _require_sync_admin(request, db)
    try:
        # Verify the credential, permanent node identity, and pristine Master
        # cursor before creating sequence 1 in the local durable outbox.
        verify_master_enrollment_identity()
        event_count, item_count = enqueue_initial_inventory_snapshot(
            db,
            actor=user,
        )
    except MasterSyncError as exc:
        db.rollback()
        query = urlencode({"error": str(exc)})
        return RedirectResponse(f"/master-connection?{query}", status_code=303)
    query = urlencode(
        {
            "message": (
                f"Node initialization queued: {item_count} active QR(s) in "
                f"{event_count} baseline event(s). Sync and verify Master before "
                "resuming franchise operations."
            )
        }
    )
    return RedirectResponse(f"/master-connection?{query}", status_code=303)


@router.post("/events/{event_id}/retry")
def retry_failed_event(
    request: Request,
    event_id: int,
    db: Session = Depends(get_db),
):
    _require_sync_admin(request, db)
    event = db.get(MasterOutboxEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Outbox event not found.")
    if event.status != MasterOutboxStatus.FAILED.value:
        raise HTTPException(status_code=409, detail="Only failed events can be retried.")
    if payload_sha256(event.payload_json) != event.payload_sha256:
        raise HTTPException(
            status_code=409,
            detail="The frozen event payload failed its integrity check.",
        )
    event.status = MasterOutboxStatus.PENDING.value
    event.next_attempt_at = None
    event.sending_at = None
    event.last_error = None
    if event.batch_id is not None:
        batch = db.get(Batch, event.batch_id)
        if batch is not None:
            batch.status = BatchStatus.PENDING_SYNC.value
            batch.last_error = None
    db.commit()
    query = urlencode({"message": f"Event sequence {event.id} queued for retry."})
    return RedirectResponse(f"/master-connection?{query}", status_code=303)
