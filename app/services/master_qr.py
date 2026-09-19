"""Apply QR allocations received through the authenticated Master command inbox."""

from __future__ import annotations

import math
import re
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Product, Serial, SerialStatus, WarehouseLevel
from app.services.master_sync import MasterSyncError, configured_franchise_code


_MASTER_SERIAL_PATTERN = re.compile(r"SQR-[0-9A-F]{32}\Z")
_MAX_QRS_PER_COMMAND = 500


def _text(value: Any, name: str, limit: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise MasterSyncError(f"Master QR {name} must be text.")
    if (not value and not allow_empty) or len(value) > limit or value != value.strip():
        raise MasterSyncError(f"Master QR {name} is missing, too long, or has surrounding spaces.")
    return value


def _optional_text(value: Any, name: str, limit: int) -> str | None:
    if value is None or value == "":
        return None
    return _text(value, name, limit)


def _rate(value: Any, name: str, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MasterSyncError(f"Master QR {name} must be a number.")
    number = float(value)
    if not math.isfinite(number) or number < 0 or (maximum is not None and number > maximum):
        raise MasterSyncError(f"Master QR {name} is out of range.")
    return number


def _identity_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _date(value: Any, name: str) -> date | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise MasterSyncError(f"Master QR {name} must be an ISO date.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise MasterSyncError(f"Master QR {name} must be an ISO date.") from exc
    if parsed.isoformat() != value:
        raise MasterSyncError(f"Master QR {name} must be an ISO date.")
    return parsed


def apply_qr_allocated_command(db: Session, payload: dict[str, Any]) -> None:
    """Create all QRs in a command atomically; command replay is handled by the inbox."""

    if type(payload.get("version")) is not int or payload["version"] != 1:
        raise MasterSyncError("Unsupported Master QR allocation version.")
    if payload.get("franchise_code") != configured_franchise_code(required=True):
        raise MasterSyncError("Master QR allocation is addressed to a different franchise.")

    product_data = payload.get("product")
    if not isinstance(product_data, dict):
        raise MasterSyncError("Master QR allocation is missing product details.")
    product_code = _text(product_data.get("product_code"), "product code", 80)
    product_name = _text(product_data.get("product_name"), "product name", 180)
    hsn = _text(product_data.get("hsn"), "HSN", 40, allow_empty=True)
    tally_name = _text(product_data.get("tally_stock_item_name"), "Tally stock item", 180)
    unit = _text(product_data.get("unit", "Pcs"), "unit", 40)
    alternate_tally_name = _optional_text(
        product_data.get("alternate_tally_stock_item_name"), "alternate Tally stock item", 180
    )
    gst_rate = _rate(product_data.get("gst_rate"), "GST rate", 100)
    default_rate = _rate(product_data.get("default_rate", 0), "default rate")
    sales_discount_rate = _rate(product_data.get("sales_discount_rate", 0), "sales discount rate", 100)
    if product_data.get("active", True) is not True:
        raise MasterSyncError("Master cannot allocate QRs to an inactive product.")

    serial_data = payload.get("serials")
    if not isinstance(serial_data, list) or not 1 <= len(serial_data) <= _MAX_QRS_PER_COMMAND:
        raise MasterSyncError("Master QR allocation must contain 1 to 500 serials.")
    parsed_serials: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in serial_data:
        if not isinstance(item, dict):
            raise MasterSyncError("Master QR allocation contains an invalid serial.")
        number = _text(item.get("serial_number"), "serial number", 140)
        if not _MASTER_SERIAL_PATTERN.fullmatch(number):
            raise MasterSyncError("Master QR allocation contains an invalid serial number.")
        if number in seen:
            raise MasterSyncError(f"Master QR allocation repeats serial {number}.")
        seen.add(number)
        if item.get("status") != SerialStatus.GENERATED.value:
            raise MasterSyncError("New Master QRs must start as GENERATED.")
        mfg_date = _date(item.get("mfg_date"), "mfg date")
        expiry_date = _date(item.get("expiry_date"), "expiry date")
        if mfg_date and expiry_date and expiry_date <= mfg_date:
            raise MasterSyncError("Master QR expiry date must be after mfg date.")
        warehouse_level = item.get("warehouse_level", WarehouseLevel.COMPANY_WAREHOUSE.value)
        try:
            level = WarehouseLevel(warehouse_level).value
        except ValueError as exc:
            raise MasterSyncError("Master QR warehouse level is invalid.") from exc
        parsed_serials.append(
            {
                "serial_number": number,
                "status": SerialStatus.GENERATED.value,
                "active": True,
                "product_batch_number": _optional_text(item.get("product_batch_number"), "batch number", 80),
                "mfg_date": mfg_date,
                "expiry_date": expiry_date,
                "warehouse": _optional_text(item.get("warehouse"), "warehouse", 80),
                "warehouse_level": level,
            }
        )

    existing_number = db.scalar(select(Serial.serial_number).where(Serial.serial_number.in_(seen)).limit(1))
    if existing_number is not None:
        raise MasterSyncError(f"Master QR serial {existing_number} already exists on Lite.")

    matching_products = db.scalars(
        select(Product).where(func.upper(Product.product_code) == product_code.upper()).limit(2)
    ).all()
    if len(matching_products) > 1:
        raise MasterSyncError(f"Multiple Lite products match Master code {product_code}.")
    product = matching_products[0] if matching_products else None
    if product is None:
        product = Product(
            product_code=product_code,
            product_name=product_name,
            hsn=hsn,
            gst_rate=gst_rate,
            unit=unit,
            default_rate=default_rate,
            sales_discount_rate=sales_discount_rate,
            tally_stock_item_name=tally_name,
            alternate_tally_stock_item_name=alternate_tally_name,
            active=True,
        )
        db.add(product)
        db.flush()
    else:
        if not product.active:
            raise MasterSyncError(f"Product {product_code} is inactive on Lite.")
        identity_fields = {
            "product_name": product_name,
            "tally_stock_item_name": tally_name,
            "hsn": hsn,
            "unit": unit,
        }
        for name, incoming in identity_fields.items():
            current = getattr(product, name)
            if name == "tally_stock_item_name" and not current:
                product.tally_stock_item_name = incoming
                continue
            if _identity_text(current) != _identity_text(incoming):
                raise MasterSyncError(f"Product {product_code} has a different {name} on Lite.")
        if not math.isclose(float(product.gst_rate), gst_rate, rel_tol=0, abs_tol=0.0001):
            raise MasterSyncError(f"Product {product_code} has a different GST rate on Lite.")

    db.add_all(Serial(product_id=product.id, **item) for item in parsed_serials)
    db.flush()


def apply_qr_replace_command(db: Session, payload: dict[str, Any], command_id: str) -> None:
    """Retire one local QR and queue its Master reservation completion atomically."""

    from app.services.master_sync import enqueue_outbox_event, network_event_item

    if type(payload.get("version")) is not int or payload["version"] != 1:
        raise MasterSyncError("Unsupported Master QR replacement version.")
    if payload.get("franchise_code") != configured_franchise_code(required=True):
        raise MasterSyncError("Master QR replacement is addressed to a different franchise.")
    old_number = _text(payload.get("old_serial_number"), "old serial number", 140)
    new_number = _text(payload.get("new_serial_number"), "new serial number", 140)
    if not _MASTER_SERIAL_PATTERN.fullmatch(new_number) or new_number == old_number:
        raise MasterSyncError("Master QR replacement has an invalid new serial number.")
    expected_status = payload.get("expected_status")
    if not isinstance(expected_status, str) or expected_status not in {
        SerialStatus.GENERATED.value,
        SerialStatus.IN_STOCK.value,
        SerialStatus.RETURNED.value,
        SerialStatus.DAMAGED.value,
    }:
        raise MasterSyncError("Master QR replacement has an invalid expected status.")
    _optional_text(payload.get("reason"), "replacement reason", 500)

    old_serial = db.scalar(select(Serial).where(Serial.serial_number == old_number))
    if old_serial is None or not old_serial.active or old_serial.status != expected_status:
        raise MasterSyncError("Master QR replacement does not match the current Lite serial state.")
    if db.scalar(select(Serial.id).where(Serial.serial_number == new_number).limit(1)) is not None:
        raise MasterSyncError("Master QR replacement serial already exists on Lite.")

    replacement_status = (
        SerialStatus.GENERATED.value
        if expected_status == SerialStatus.GENERATED.value
        else SerialStatus.IN_STOCK.value
    )
    replacement = Serial(
        serial_number=new_number,
        product_id=old_serial.product_id,
        status=replacement_status,
        active=True,
        product_batch_number=old_serial.product_batch_number,
        mfg_date=old_serial.mfg_date,
        expiry_date=old_serial.expiry_date,
        warehouse=old_serial.warehouse,
        warehouse_level=old_serial.warehouse_level,
        location_id=old_serial.location_id,
    )
    db.add(replacement)
    db.flush()
    old_serial.status = SerialStatus.INVALID.value
    old_serial.active = False
    old_serial.replaced_by_id = replacement.id
    db.flush()

    enqueue_outbox_event(
        db,
        event_type="STOCK_SNAPSHOT",
        aggregate_type="QR_REPLACEMENT",
        aggregate_id=command_id,
        payload={
            "reference": command_id,
            "actor": None,
            "reason_code": "QR_REPLACEMENT",
            "items": [network_event_item(old_serial), network_event_item(replacement)],
        },
    )
