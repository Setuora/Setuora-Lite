"""Store franchise receipt vouchers that Master read from central Tally.

Master sends a TALLY_RECEIPTS command with new or changed receipts for this
franchise. Each receipt is kept once by its Tally GUID; its customer ledgers
become debtor ledger choices for sales, which return to Tally through Master.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MasterTallyReceipt
from app.services.master_sync import MasterSyncError, configured_franchise_code

_MAX_RECEIPTS_PER_COMMAND = 500
_MAX_LINES_PER_RECEIPT = 200


def _text(value: Any, name: str, limit: int, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise MasterSyncError(f"Master receipt {name} must be text.")
    value = value.strip()
    if (required and not value) or len(value) > limit:
        raise MasterSyncError(f"Master receipt {name} is missing or too long.")
    return value


def _amount(value: Any, name: str) -> str:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise MasterSyncError(f"Master receipt {name} is not a number.") from None
    if not amount.is_finite():
        raise MasterSyncError(f"Master receipt {name} is not a number.")
    return format(amount, "f")


def _receipt_fields(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise MasterSyncError("Master receipt must be an object.")
    try:
        voucher_date = date.fromisoformat(_text(item.get("voucher_date"), "date", 10, required=True))
    except ValueError:
        raise MasterSyncError("Master receipt date is not valid.") from None
    raw_lines = item.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines or len(raw_lines) > _MAX_LINES_PER_RECEIPT:
        raise MasterSyncError("Master receipt must list its customer ledgers.")
    lines = []
    for line in raw_lines:
        if not isinstance(line, dict):
            raise MasterSyncError("Master receipt line must be an object.")
        lines.append(
            {
                "ledger_name": _text(line.get("ledger_name"), "ledger", 220, required=True),
                "amount": _amount(line.get("amount"), "line amount"),
            }
        )
    return {
        "tally_guid": _text(item.get("tally_guid"), "Tally GUID", 120, required=True),
        "voucher_type": _text(item.get("voucher_type"), "voucher type", 120),
        "voucher_number": _text(item.get("voucher_number"), "voucher number", 80),
        "voucher_date": voucher_date,
        "bank_ledger": _text(item.get("bank_ledger"), "bank ledger", 220),
        "total_amount": _amount(item.get("total_amount", "0"), "total"),
        "narration": _text(item.get("narration"), "narration", 2000),
        "lines_json": json.dumps(lines, ensure_ascii=False),
    }


def apply_tally_receipts_command(db: Session, payload: dict[str, Any]) -> int:
    """Insert or update each receipt by Tally GUID; return how many were stored."""

    if payload.get("franchise_code") != configured_franchise_code(required=True):
        raise MasterSyncError("Master receipts are addressed to a different franchise.")
    receipts = payload.get("receipts")
    if not isinstance(receipts, list) or len(receipts) > _MAX_RECEIPTS_PER_COMMAND:
        raise MasterSyncError("Master receipts command has no valid receipt list.")
    # Validate everything before writing so a bad receipt changes nothing.
    parsed = [_receipt_fields(item) for item in receipts]
    for fields in parsed:
        row = db.scalar(
            select(MasterTallyReceipt).where(MasterTallyReceipt.tally_guid == fields["tally_guid"])
        )
        if row is None:
            db.add(MasterTallyReceipt(**fields))
        else:
            for key, value in fields.items():
                setattr(row, key, value)
    db.flush()
    return len(parsed)


def receipt_ledger_names(db: Session) -> set[str]:
    names: set[str] = set()
    for row in db.scalars(select(MasterTallyReceipt)):
        names.update(
            str(line.get("ledger_name", "")).strip()
            for line in row.lines
            if isinstance(line, dict) and str(line.get("ledger_name", "")).strip()
        )
    return names


def recent_receipts(db: Session, limit: int = 50) -> list[MasterTallyReceipt]:
    return list(
        db.scalars(
            select(MasterTallyReceipt)
            .order_by(MasterTallyReceipt.voucher_date.desc(), MasterTallyReceipt.id.desc())
            .limit(limit)
        )
    )
