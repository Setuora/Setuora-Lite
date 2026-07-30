"""Local, transactional inter-franchise transfer operations."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import (
    LocalTransfer,
    LocalTransferItem,
    Product,
    Serial,
    SerialStatus,
    TransactionType,
    TransferDirection,
    TransferStatus,
    User,
    WarehouseLevel,
    utc_now,
)
from app.services.inventory import log_inventory_transaction, normalize_serial
from app.services.master_sync import (
    MasterSyncError,
    configured_franchise_code,
    enqueue_outbox_event,
    network_event_item,
    normalize_franchise_code,
)


class TransferError(ValueError):
    pass


OPEN_OUTBOUND_STATUSES = {
    TransferStatus.DRAFT.value,
    TransferStatus.PENDING_MASTER.value,
    TransferStatus.DISPATCHED.value,
    TransferStatus.PARTIALLY_RECEIVED.value,
}
RECEIVABLE_STATUSES = {
    TransferStatus.AWAITING_RECEIPT.value,
    TransferStatus.PARTIALLY_RECEIVED.value,
}


def _finish_write(db: Session, transfer: LocalTransfer, *, commit: bool) -> LocalTransfer:
    if commit:
        db.commit()
        db.refresh(transfer)
    else:
        db.flush()
    return transfer


def _resolve_transfer(
    db: Session,
    transfer: LocalTransfer | str | int,
    *,
    direction: TransferDirection | None = None,
) -> LocalTransfer:
    if isinstance(transfer, LocalTransfer):
        row = transfer
    elif isinstance(transfer, int):
        row = db.get(LocalTransfer, transfer)
    else:
        row = db.scalar(
            select(LocalTransfer).where(LocalTransfer.transfer_uuid == str(transfer))
        )
    if row is None:
        raise TransferError("Transfer not found.")
    if direction is not None and row.direction != direction.value:
        raise TransferError(f"Transfer is not {direction.value.lower()}.")
    return row


def create_outbound_transfer(
    db: Session,
    user: User,
    peer_code: str,
    notes: str | None = None,
    *,
    commit: bool = True,
) -> LocalTransfer:
    destination = normalize_franchise_code(peer_code)
    if not destination:
        raise TransferError("Choose a destination franchise.")
    if len(destination) > 40:
        raise TransferError("Destination franchise code must be 40 characters or fewer.")
    local_code = configured_franchise_code()
    if local_code and destination == local_code:
        raise TransferError("Source and destination franchise must be different.")
    transfer = LocalTransfer(
        transfer_uuid=str(uuid4()),
        direction=TransferDirection.OUTBOUND.value,
        status=TransferStatus.DRAFT.value,
        local_franchise_code=local_code or None,
        peer_code=destination,
        created_by_id=user.id,
        notes=notes.strip() if notes else None,
    )
    db.add(transfer)
    return _finish_write(db, transfer, commit=commit)


def add_outbound_serial(
    db: Session,
    transfer: LocalTransfer | str | int,
    serial_number: str,
    *,
    commit: bool = True,
) -> LocalTransferItem:
    row = _resolve_transfer(db, transfer, direction=TransferDirection.OUTBOUND)
    if row.status != TransferStatus.DRAFT.value:
        raise TransferError("Only draft transfers can be edited.")
    normalized = normalize_serial(serial_number)
    serial = db.scalar(select(Serial).where(Serial.serial_number == normalized))
    if serial is None:
        raise TransferError(f"{normalized} is not registered at this franchise.")
    if not serial.active or serial.status != SerialStatus.IN_STOCK.value:
        raise TransferError(f"{normalized} is not currently in local stock.")

    reserved = db.scalar(
        select(LocalTransferItem)
        .join(LocalTransfer, LocalTransfer.id == LocalTransferItem.transfer_id)
        .where(
            LocalTransferItem.serial_id == serial.id,
            LocalTransfer.direction == TransferDirection.OUTBOUND.value,
            LocalTransfer.status.in_(OPEN_OUTBOUND_STATUSES),
        )
    )
    if reserved is not None:
        if reserved.transfer_id == row.id:
            raise TransferError(f"{normalized} is already in this transfer.")
        raise TransferError(f"{normalized} is already reserved by another transfer.")

    item = LocalTransferItem(
        transfer_id=row.id,
        serial_id=serial.id,
        manifest_serial_number=serial.serial_number,
        product_code=serial.product.product_code,
        product_name=serial.product.product_name,
    )
    db.add(item)
    if commit:
        db.commit()
        db.refresh(item)
    else:
        db.flush()
    return item


def remove_outbound_serial(
    db: Session,
    transfer: LocalTransfer | str | int,
    item_id: int,
    *,
    commit: bool = True,
) -> None:
    row = _resolve_transfer(db, transfer, direction=TransferDirection.OUTBOUND)
    if row.status != TransferStatus.DRAFT.value:
        raise TransferError("Only draft transfers can be edited.")
    item = db.get(LocalTransferItem, item_id)
    if item is None or item.transfer_id != row.id:
        raise TransferError("Transfer item not found.")
    db.delete(item)
    if commit:
        db.commit()
    else:
        db.flush()


def _dispatch_payload(transfer: LocalTransfer, user: User) -> dict[str, Any]:
    return {
        "reference": transfer.transfer_uuid,
        "actor": user.username,
        "destination_franchise_code": transfer.peer_code,
        "transfer_id": transfer.transfer_uuid,
        "items": [
            network_event_item(
                item.serial,
                status=SerialStatus.IN_STOCK.value,
            )
            for item in sorted(transfer.items, key=lambda value: value.manifest_serial_number)
        ],
    }


def dispatch_outbound_transfer(
    db: Session,
    transfer: LocalTransfer | str | int,
    user: User,
    *,
    commit: bool = True,
) -> LocalTransfer:
    row = _resolve_transfer(db, transfer, direction=TransferDirection.OUTBOUND)
    if row.status != TransferStatus.DRAFT.value:
        raise TransferError("Only a draft transfer can be dispatched.")
    if not row.items:
        raise TransferError("Add at least one serial before dispatching.")
    if len(row.items) > 5000:
        raise TransferError("A transfer can contain at most 5000 serials.")

    try:
        with db.begin_nested():
            for item in row.items:
                serial = item.serial
                if serial is None:
                    raise TransferError(
                        f"{item.manifest_serial_number} is missing from local inventory."
                    )
                claimed = db.execute(
                    update(Serial)
                    .where(
                        Serial.id == serial.id,
                        Serial.status == SerialStatus.IN_STOCK.value,
                        Serial.active.is_(True),
                    )
                    .values(status=SerialStatus.IN_TRANSIT.value)
                    .execution_options(synchronize_session=False)
                ).rowcount
                if claimed != 1:
                    raise TransferError(
                        f"{serial.serial_number} is no longer available; review the transfer."
                    )
                serial.status = SerialStatus.IN_TRANSIT.value
                log_inventory_transaction(
                    db,
                    user,
                    TransactionType.TRANSFER_OUT,
                    serial=serial,
                    status_from=SerialStatus.IN_STOCK.value,
                    status_to=SerialStatus.IN_TRANSIT.value,
                    reference_number=row.transfer_uuid,
                    notes=f"Dispatched to franchise {row.peer_code}.",
                )

            row.status = TransferStatus.DISPATCHED.value
            row.dispatched_at = utc_now()
            enqueue_outbox_event(
                db,
                event_type="TRANSFER_DISPATCHED",
                aggregate_type="TRANSFER",
                aggregate_id=row.transfer_uuid,
                payload=_dispatch_payload(row, user),
                occurred_at=row.dispatched_at,
            )
    except MasterSyncError as exc:
        # The nested transaction has already restored every serial and log.
        db.expire_all()
        raise TransferError(str(exc)) from exc
    except Exception:
        # The nested transaction restores every serial if any reservation or
        # event insert fails.  Leave the outer session usable by the caller.
        db.expire_all()
        raise

    return _finish_write(db, row, commit=commit)


def _unwrap_transfer_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    current = payload
    if (
        isinstance(current.get("payload"), dict)
        and "transfer" not in current
        and "transfer_uuid" not in current
    ):
        current = current["payload"]
    transfer_data = current.get("transfer", current)
    if not isinstance(transfer_data, dict):
        raise TransferError("Transfer command is missing transfer details.")
    items = current.get("items", transfer_data.get("items", []))
    if not isinstance(items, list):
        raise TransferError("Transfer manifest must be a list.")
    return transfer_data, items


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise TransferError(f"Invalid transfer item date: {value}.") from exc


def _identity_text(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _validate_product_snapshot(product: Product, snapshot: dict[str, Any]) -> None:
    incoming_name = snapshot.get("product_name") or snapshot.get("name")
    incoming_tally_name = snapshot.get("tally_stock_item_name") or incoming_name
    comparisons = {
        "name": (product.product_name, incoming_name),
        "HSN": (product.hsn, snapshot.get("hsn")),
        "unit": (product.unit, snapshot.get("unit")),
        "Tally stock item": (
            product.tally_stock_item_name,
            incoming_tally_name,
        ),
    }
    mismatches = [
        label
        for label, (current, incoming) in comparisons.items()
        if _identity_text(current) != _identity_text(incoming)
    ]
    try:
        incoming_gst = float(snapshot.get("gst_rate"))
    except (TypeError, ValueError):
        mismatches.append("GST rate")
    else:
        if abs(float(product.gst_rate or 0) - incoming_gst) > 0.0001:
            mismatches.append("GST rate")
    if mismatches:
        fields = ", ".join(mismatches)
        raise TransferError(
            f"Product code {product.product_code} conflicts with local product "
            f"identity ({fields}). Resolve the product mapping before receiving."
        )


def _materialize_product(db: Session, snapshot: dict[str, Any]) -> Product:
    product_code = normalize_serial(
        str(snapshot.get("product_code") or snapshot.get("code") or "")
    )
    if not product_code:
        raise TransferError("Transfer item is missing product_code.")
    product = db.scalar(select(Product).where(Product.product_code == product_code))
    if product is not None:
        _validate_product_snapshot(product, snapshot)
        return product
    product_name = str(
        snapshot.get("product_name") or snapshot.get("name") or product_code
    ).strip()
    product = Product(
        product_code=product_code,
        product_name=product_name,
        nickname=snapshot.get("nickname"),
        category=snapshot.get("category"),
        brand=snapshot.get("brand"),
        hsn=str(snapshot.get("hsn") or "UNSPECIFIED"),
        gst_rate=float(snapshot.get("gst_rate") or 0),
        unit=str(snapshot.get("unit") or "Pcs"),
        default_rate=float(snapshot.get("default_rate") or snapshot.get("rate") or 0),
        sales_discount_rate=float(snapshot.get("sales_discount_rate") or 0),
        tally_stock_item_name=str(
            snapshot.get("tally_stock_item_name") or product_name
        ),
        alternate_tally_stock_item_name=snapshot.get(
            "alternate_tally_stock_item_name"
        ),
        active=bool(snapshot.get("active", True)),
    )
    db.add(product)
    db.flush()
    return product


def _reactivate_returning_serial(
    db: Session,
    serial: Serial,
    product: Product,
    *,
    serial_snapshot: dict[str, Any],
) -> Serial:
    prior_completed_outbound = db.scalar(
        select(LocalTransferItem.id)
        .join(LocalTransfer, LocalTransfer.id == LocalTransferItem.transfer_id)
        .where(
            LocalTransferItem.serial_id == serial.id,
            LocalTransferItem.received.is_(True),
            LocalTransfer.direction == TransferDirection.OUTBOUND.value,
        )
        .limit(1)
    )
    if (
        serial.active
        or serial.status != SerialStatus.IN_TRANSIT.value
        or prior_completed_outbound is None
    ):
        raise TransferError(
            f"{serial.serial_number} already exists at the destination franchise."
        )
    if serial.product_id != product.id:
        raise TransferError(
            f"{serial.serial_number} conflicts with its historical local product."
        )

    warehouse_level = str(
        serial_snapshot.get("warehouse_level")
        or WarehouseLevel.COMPANY_WAREHOUSE.value
    )
    try:
        warehouse_level = WarehouseLevel(warehouse_level).value
    except ValueError:
        warehouse_level = WarehouseLevel.COMPANY_WAREHOUSE.value
    serial.active = True
    serial.status = SerialStatus.IN_TRANSIT.value
    serial.product_batch_number = serial_snapshot.get("product_batch_number")
    serial.mfg_date = _parse_date(serial_snapshot.get("mfg_date"))
    serial.expiry_date = _parse_date(serial_snapshot.get("expiry_date"))
    serial.warehouse = serial_snapshot.get("warehouse")
    serial.warehouse_level = warehouse_level
    serial.location_id = None
    return serial


def apply_transfer_available_command(
    db: Session,
    payload: dict[str, Any],
) -> LocalTransfer:
    transfer_data, manifest = _unwrap_transfer_payload(payload)
    transfer_uuid = str(
        transfer_data.get("transfer_uuid")
        or transfer_data.get("transfer_id")
        or transfer_data.get("id")
        or payload.get("transfer_id")
        or payload.get("transfer_uuid")
        or ""
    ).strip()
    if not transfer_uuid:
        raise TransferError("Transfer command is missing transfer_uuid.")
    existing = db.scalar(
        select(LocalTransfer).where(LocalTransfer.transfer_uuid == transfer_uuid)
    )
    if existing is not None:
        if existing.direction != TransferDirection.INBOUND.value:
            raise TransferError(f"Transfer UUID collision: {transfer_uuid}.")
        return existing

    source_franchise = transfer_data.get("source_franchise")
    source_code = normalize_franchise_code(
        transfer_data.get("source_franchise_code")
        or transfer_data.get("source_code")
        or transfer_data.get("peer_code")
        or (
            source_franchise.get("code")
            if isinstance(source_franchise, dict)
            else source_franchise
        )
    )
    destination_code = normalize_franchise_code(
        transfer_data.get("destination_franchise_code")
        or transfer_data.get("destination_code")
    )
    local_code = configured_franchise_code()
    if not source_code:
        raise TransferError("Transfer command is missing its source franchise.")
    if destination_code and local_code and destination_code != local_code:
        raise TransferError(
            f"Transfer {transfer_uuid} is addressed to another franchise."
        )
    if not manifest:
        raise TransferError("Transfer manifest is empty.")

    transfer = LocalTransfer(
        transfer_uuid=transfer_uuid,
        direction=TransferDirection.INBOUND.value,
        status=TransferStatus.AWAITING_RECEIPT.value,
        local_franchise_code=local_code or destination_code or None,
        peer_code=source_code,
        notes=transfer_data.get("notes") or transfer_data.get("reference"),
    )
    db.add(transfer)
    db.flush()

    seen: set[str] = set()
    for manifest_item in manifest:
        if not isinstance(manifest_item, dict):
            raise TransferError("Transfer manifest contains a malformed item.")
        product_snapshot = manifest_item.get("product") or manifest_item
        serial_snapshot = manifest_item.get("serial") or manifest_item
        if not isinstance(product_snapshot, dict) or not isinstance(serial_snapshot, dict):
            raise TransferError("Transfer item snapshots must be objects.")
        serial_number = normalize_serial(
            str(
                manifest_item.get("manifest_serial_number")
                or serial_snapshot.get("serial_number")
                or manifest_item.get("serial_number")
                or ""
            )
        )
        if not serial_number:
            raise TransferError("Transfer item is missing a serial number.")
        if serial_number in seen:
            raise TransferError(f"Transfer manifest repeats {serial_number}.")
        seen.add(serial_number)

        product = _materialize_product(db, product_snapshot)
        existing_serial = db.scalar(
            select(Serial).where(Serial.serial_number == serial_number)
        )
        if existing_serial is not None:
            serial = _reactivate_returning_serial(
                db,
                existing_serial,
                product,
                serial_snapshot=serial_snapshot,
            )
        else:
            warehouse_level = str(
                serial_snapshot.get("warehouse_level")
                or WarehouseLevel.COMPANY_WAREHOUSE.value
            )
            try:
                warehouse_level = WarehouseLevel(warehouse_level).value
            except ValueError:
                warehouse_level = WarehouseLevel.COMPANY_WAREHOUSE.value
            serial = Serial(
                serial_number=serial_number,
                product_id=product.id,
                status=SerialStatus.IN_TRANSIT.value,
                active=True,
                product_batch_number=serial_snapshot.get("product_batch_number"),
                mfg_date=_parse_date(serial_snapshot.get("mfg_date")),
                expiry_date=_parse_date(serial_snapshot.get("expiry_date")),
                warehouse=serial_snapshot.get("warehouse"),
                warehouse_level=warehouse_level,
                location_id=None,
            )
            db.add(serial)
            db.flush()
        db.add(
            LocalTransferItem(
                transfer_id=transfer.id,
                serial_id=serial.id,
                manifest_serial_number=serial.serial_number,
                product_code=product.product_code,
                product_name=product.product_name,
            )
        )
    db.flush()
    return transfer


def scan_inbound_transfer_item(
    db: Session,
    transfer: LocalTransfer | str | int,
    serial_number: str,
    *,
    commit: bool = True,
) -> LocalTransferItem:
    row = _resolve_transfer(db, transfer, direction=TransferDirection.INBOUND)
    if row.status not in RECEIVABLE_STATUSES:
        raise TransferError("This transfer is not awaiting receipt.")
    normalized = normalize_serial(serial_number)
    item = db.scalar(
        select(LocalTransferItem).where(
            LocalTransferItem.transfer_id == row.id,
            LocalTransferItem.manifest_serial_number == normalized,
        )
    )
    if item is None:
        raise TransferError(f"{normalized} is not part of this transfer manifest.")
    if item.received:
        raise TransferError(f"{normalized} has already been received.")
    if item.scanned:
        raise TransferError(f"{normalized} has already been scanned for receipt.")

    scanned_at = utc_now()
    claimed = db.execute(
        update(LocalTransferItem)
        .where(
            LocalTransferItem.id == item.id,
            LocalTransferItem.scanned.is_(False),
            LocalTransferItem.received.is_(False),
        )
        .values(scanned=True, scanned_at=scanned_at)
        .execution_options(synchronize_session=False)
    ).rowcount
    if claimed != 1:
        db.expire(item)
        raise TransferError(f"{normalized} has already been scanned for receipt.")
    item.scanned = True
    item.scanned_at = scanned_at
    if commit:
        db.commit()
        db.refresh(item)
    else:
        db.flush()
    return item


def _receipt_payload(
    transfer: LocalTransfer,
    received_items: list[LocalTransferItem],
    *,
    receipt_at: datetime,
    user: User,
) -> dict[str, Any]:
    return {
        "reference": transfer.transfer_uuid,
        "actor": user.username,
        "transfer_id": transfer.transfer_uuid,
        "items": [
            network_event_item(
                item.serial,
                status=SerialStatus.IN_STOCK.value,
            )
            for item in received_items
        ],
    }


def finalize_inbound_receipt(
    db: Session,
    transfer: LocalTransfer | str | int,
    user: User,
    *,
    commit: bool = True,
) -> LocalTransfer:
    row = _resolve_transfer(db, transfer, direction=TransferDirection.INBOUND)
    if row.status not in RECEIVABLE_STATUSES:
        raise TransferError("This transfer is not awaiting receipt.")
    receipt_items = [item for item in row.items if item.scanned and not item.received]
    if not receipt_items:
        raise TransferError("Scan at least one unreceived manifest item first.")

    receipt_at = utc_now()
    try:
        with db.begin_nested():
            for item in receipt_items:
                serial = item.serial
                if serial is None:
                    raise TransferError(
                        f"{item.manifest_serial_number} is missing from inbound inventory."
                    )
                claimed = db.execute(
                    update(Serial)
                    .where(
                        Serial.id == serial.id,
                        Serial.status == SerialStatus.IN_TRANSIT.value,
                        Serial.active.is_(True),
                    )
                    .values(status=SerialStatus.IN_STOCK.value)
                    .execution_options(synchronize_session=False)
                ).rowcount
                if claimed != 1:
                    raise TransferError(
                        f"{serial.serial_number} cannot be received from {serial.status}."
                    )
                serial.status = SerialStatus.IN_STOCK.value
                item.received = True
                item.received_at = receipt_at
                log_inventory_transaction(
                    db,
                    user,
                    TransactionType.TRANSFER_IN,
                    serial=serial,
                    status_from=SerialStatus.IN_TRANSIT.value,
                    status_to=SerialStatus.IN_STOCK.value,
                    reference_number=row.transfer_uuid,
                    notes=f"Received from franchise {row.peer_code}.",
                )

            if all(item.received for item in row.items):
                row.status = TransferStatus.RECEIVED.value
                row.received_at = receipt_at
            else:
                row.status = TransferStatus.PARTIALLY_RECEIVED.value
            enqueue_outbox_event(
                db,
                event_type="TRANSFER_RECEIVED",
                aggregate_type="TRANSFER",
                aggregate_id=row.transfer_uuid,
                payload=_receipt_payload(
                    row,
                    receipt_items,
                    receipt_at=receipt_at,
                    user=user,
                ),
                occurred_at=receipt_at,
            )
    except MasterSyncError as exc:
        db.expire_all()
        raise TransferError(str(exc)) from exc
    except Exception:
        db.expire_all()
        raise

    return _finish_write(db, row, commit=commit)


def _received_serials_from_payload(
    transfer_data: dict[str, Any],
    payload: dict[str, Any],
    manifest: list[dict[str, Any]],
) -> tuple[set[str], bool]:
    raw_serials = payload.get("received_serial_numbers")
    if raw_serials is None:
        raw_serials = transfer_data.get("received_serial_numbers")
    if raw_serials is None:
        raw_serials = [
            item.get("manifest_serial_number") or item.get("serial_number")
            for item in manifest
            if isinstance(item, dict) and item.get("received", True)
        ]
    if not isinstance(raw_serials, list):
        raise TransferError("Transfer receipt serials must be a list.")
    serials = {
        normalize_serial(str(value))
        for value in raw_serials
        if str(value or "").strip()
    }
    complete = bool(
        transfer_data.get("complete")
        or payload.get("complete")
        or transfer_data.get("status") == TransferStatus.RECEIVED.value
    )
    return serials, complete


def apply_transfer_receipt_command(
    db: Session,
    payload: dict[str, Any],
) -> LocalTransfer:
    transfer_data, manifest = _unwrap_transfer_payload(payload)
    transfer_uuid = str(
        transfer_data.get("transfer_uuid")
        or transfer_data.get("transfer_id")
        or transfer_data.get("id")
        or payload.get("transfer_id")
        or payload.get("transfer_uuid")
        or ""
    ).strip()
    if not transfer_uuid:
        raise TransferError("Transfer receipt is missing transfer_uuid.")
    transfer = db.scalar(
        select(LocalTransfer).where(LocalTransfer.transfer_uuid == transfer_uuid)
    )
    if transfer is None or transfer.direction != TransferDirection.OUTBOUND.value:
        raise TransferError(f"Outbound transfer {transfer_uuid} was not found.")
    if transfer.status == TransferStatus.DRAFT.value:
        raise TransferError("A draft transfer cannot receive a receipt.")

    received_serials, complete = _received_serials_from_payload(
        transfer_data,
        payload,
        manifest,
    )
    manifest_serials = {item.manifest_serial_number for item in transfer.items}
    if complete and not received_serials:
        received_serials = manifest_serials
    unknown = received_serials - manifest_serials
    if unknown:
        raise TransferError(
            f"Receipt contains serials outside the manifest: {', '.join(sorted(unknown))}."
        )

    receipt_at = utc_now()
    for item in transfer.items:
        if item.manifest_serial_number not in received_serials or item.received:
            continue
        item.scanned = True
        item.scanned_at = item.scanned_at or receipt_at
        item.received = True
        item.received_at = receipt_at
        if item.serial is not None:
            # Keep IN_TRANSIT as the historical terminal status at the source;
            # inactive ensures it can never re-enter local stock workflows.
            item.serial.active = False

    received_count = sum(1 for item in transfer.items if item.received)
    if received_count == len(transfer.items):
        transfer.status = TransferStatus.RECEIVED.value
        transfer.received_at = receipt_at
    elif received_count:
        transfer.status = TransferStatus.PARTIALLY_RECEIVED.value
    db.flush()
    return transfer
