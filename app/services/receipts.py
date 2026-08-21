from __future__ import annotations

import base64
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Receipt, ReceiptStatus, User
from app.services.master_sync import (
    MasterSyncError,
    enqueue_outbox_event,
    master_sync_enabled,
)

MAX_PROOF_IMAGE_BYTES = 3 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


class ReceiptError(ValueError):
    pass


def detected_image_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_proof_image(data: bytes, supplied_content_type: str | None) -> str:
    if not data:
        raise ReceiptError("Proof image is required.")
    if len(data) > MAX_PROOF_IMAGE_BYTES:
        raise ReceiptError("Proof image must be 3 MB or smaller.")
    detected = detected_image_type(data)
    if detected not in ALLOWED_IMAGE_TYPES:
        raise ReceiptError("Proof must be a JPEG, PNG, or WebP image.")
    supplied = (supplied_content_type or "").split(";", 1)[0].strip().lower()
    if supplied and supplied not in ALLOWED_IMAGE_TYPES:
        raise ReceiptError("Proof must be a JPEG, PNG, or WebP image.")
    return detected


def create_receipt(
    db: Session,
    *,
    user: User,
    receipt_date,
    proof_image: bytes,
    proof_content_type: str | None,
    utr_number: str = "",
) -> Receipt:
    content_type = validate_proof_image(proof_image, proof_content_type)
    normalized_utr = "".join(utr_number.split()).upper()
    if len(normalized_utr) > 80:
        raise ReceiptError("UTR number must be 80 characters or fewer.")

    receipt = Receipt(
        receipt_date=receipt_date,
        proof_image=proof_image,
        proof_content_type=content_type,
        utr_number=normalized_utr or None,
        status=ReceiptStatus.PENDING.value,
        created_by_id=user.id,
    )
    db.add(receipt)
    db.flush()
    try:
        if master_sync_enabled():
            enqueue_outbox_event(
                db,
                event_type="RECEIPT_SUBMITTED",
                aggregate_type="RECEIPT",
                aggregate_id=receipt.receipt_uuid,
                payload={
                    "receipt_id": receipt.receipt_uuid,
                    "receipt_date": receipt.receipt_date.isoformat(),
                    "proof_content_type": receipt.proof_content_type,
                    "proof_image_base64": base64.b64encode(proof_image).decode("ascii"),
                    "utr_number": receipt.utr_number,
                    "actor": user.username,
                    "items": [],
                },
            )
    except MasterSyncError as exc:
        raise ReceiptError(str(exc)) from exc
    db.commit()
    db.refresh(receipt)
    return receipt


def apply_receipt_review_command(db: Session, payload: dict) -> Receipt:
    receipt_id = str(payload.get("receipt_id") or "").strip()
    status = str(payload.get("status") or "").strip().upper()
    remarks = " ".join(str(payload.get("rejection_remarks") or "").split())
    if not receipt_id:
        raise MasterSyncError("Receipt review command is missing receipt_id.")
    if status not in {ReceiptStatus.APPROVED.value, ReceiptStatus.DENIED.value}:
        raise MasterSyncError("Receipt review command has an invalid status.")
    if status == ReceiptStatus.DENIED.value and not remarks:
        raise MasterSyncError("A denied receipt requires rejection remarks.")
    if len(remarks) > 1000:
        raise MasterSyncError("Rejection remarks must be 1,000 characters or fewer.")

    receipt = db.scalar(select(Receipt).where(Receipt.receipt_uuid == receipt_id))
    if receipt is None:
        raise MasterSyncError(f"Receipt {receipt_id} was not found on this Lite node.")
    if receipt.status != ReceiptStatus.PENDING.value and receipt.status != status:
        raise MasterSyncError(
            "Receipt has already received a different review decision."
        )

    reviewed_at = payload.get("reviewed_at")
    try:
        parsed_reviewed_at = datetime.fromisoformat(str(reviewed_at))
    except (TypeError, ValueError) as exc:
        raise MasterSyncError(
            "Receipt review command has an invalid reviewed_at value."
        ) from exc
    receipt.status = status
    receipt.rejection_remarks = (
        remarks if status == ReceiptStatus.DENIED.value else None
    )
    receipt.reviewed_by = str(payload.get("reviewed_by") or "").strip()[:120] or None
    receipt.reviewed_at = parsed_reviewed_at
    return receipt
