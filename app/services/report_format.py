from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any


IST = timezone(timedelta(hours=5, minutes=30))
REPORT_DATE_FORMAT = "%d-%m-%Y"
EXCEL_DATE_FORMAT = "DD-MM-YYYY"


def report_date(value: object) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        return _local_datetime(value).strftime(REPORT_DATE_FORMAT)
    if isinstance(value, date):
        return value.strftime(REPORT_DATE_FORMAT)
    if isinstance(value, str):
        parsed = _parse_date_text(value)
        if parsed:
            return report_date(parsed)
    return str(value)


def report_datetime(value: object, fmt: str = "%d %b %Y %H:%M") -> str:
    """Show a stored UTC timestamp in the business time zone."""
    if isinstance(value, str):
        parsed = _parse_date_text(value)
        value = parsed if isinstance(parsed, datetime) else value
    if not isinstance(value, datetime):
        return report_date(value)
    return _local_datetime(value).strftime(fmt)


def batch_voucher_number(batch: Any, override: str | None = None) -> int | str:
    fallback = getattr(batch, "id", "") or ""
    return numeric_voucher_number(
        override,
        getattr(batch, "tally_voucher_number", None),
        getattr(batch, "batch_number", None),
        fallback=fallback,
    )


def numeric_voucher_number(*values: object, fallback: object = "") -> int | str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        if text.isdecimal():
            return int(text)
        match = re.search(r"(\d+)$", text)
        if match:
            return int(match.group(1))
    if isinstance(fallback, int):
        return fallback
    fallback_text = str(fallback).strip()
    return int(fallback_text) if fallback_text.isdecimal() else fallback_text


def _local_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(IST)


def _parse_date_text(value: str) -> date | datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        pass
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


_LABEL_ACRONYMS = {"qr": "QR", "gst": "GST", "sku": "SKU", "fefo": "FEFO", "hsn": "HSN", "xml": "XML"}


def status_label(value: object) -> str:
    """Show a stored enum value such as PURCHASE_RETURN as a readable label."""
    text = str(getattr(value, "value", value) or "")
    is_enum = text == text.upper() or re.fullmatch(r"[a-z0-9_]+", text) is not None
    if not text or not is_enum or not any(char.isalpha() for char in text):
        return text
    words = text.replace("_", " ").lower().split(" ")
    words = [_LABEL_ACRONYMS.get(word, word) for word in words]
    label = " ".join(words)
    return label[:1].upper() + label[1:]
