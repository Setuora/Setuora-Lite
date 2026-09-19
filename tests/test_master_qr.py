import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from starlette.requests import Request

from app.models import MasterOutboxEvent, Product, Serial, SerialStatus, User
from app.routers import barcode_assignment, products, replacements
from app.services import inventory, master_sync, replacement
from app.services.inventory import InventoryError
from app.services.master_sync import MasterSyncError


def _lite_settings():
    return SimpleNamespace(
        app_mode="lite",
        franchise_code="BLR-01",
        master_sync_enabled=True,
        master_url="https://master.example",
        master_api_key="test-secret",
    )


def _setup(monkeypatch):
    settings = _lite_settings()
    monkeypatch.setattr(master_sync, "get_settings", lambda: settings)
    monkeypatch.setattr(inventory, "get_settings", lambda: settings)
    monkeypatch.setattr(replacement, "get_settings", lambda: settings)
    monkeypatch.setattr(barcode_assignment, "get_settings", lambda: settings)
    monkeypatch.setattr(products, "get_settings", lambda: settings)
    monkeypatch.setattr(replacements, "get_settings", lambda: settings)


def _payload(*numbers):
    return {
        "version": 1,
        "franchise_code": "BLR-01",
        "product": {
            "product_code": "P-01",
            "product_name": "Example Product",
            "hsn": "",
            "gst_rate": 18,
            "unit": "Pcs",
            "default_rate": 125,
            "sales_discount_rate": 2,
            "tally_stock_item_name": "Example Product",
            "alternate_tally_stock_item_name": "Example Alias",
            "active": True,
        },
        "serials": [
            {
                "serial_number": number,
                "status": "GENERATED",
                "product_batch_number": "B1",
                "mfg_date": "2026-01-01",
                "expiry_date": "2027-01-01",
                "warehouse": "MAIN",
                "warehouse_level": "Company Warehouse",
            }
            for number in numbers
        ],
    }


def _command(command_id, payload, command_type="QR_ALLOCATED"):
    return {"command_id": command_id, "type": command_type, "schema_version": 1, "payload": payload}


def _request(path):
    return Request({"type": "http", "method": "POST", "path": path, "headers": [], "query_string": b""})


def test_allocation_replay_and_offline_redelivery_create_once_without_outbox(db_session, monkeypatch):
    _setup(monkeypatch)
    first = "SQR-" + "A" * 32
    second = "SQR-" + "B" * 32
    command = _command("allocate-1", _payload(first, second))

    master_sync.apply_master_command(db_session, command)
    db_session.commit()
    # The same unacknowledged command is fetched again after a network outage.
    master_sync.apply_master_command(db_session, command)
    db_session.commit()

    serials = db_session.scalars(select(Serial).order_by(Serial.serial_number)).all()
    assert [row.serial_number for row in serials] == [first, second]
    assert all(row.status == SerialStatus.GENERATED.value and row.active for row in serials)
    assert serials[0].product.product_code == "P-01"
    assert serials[0].product.tally_stock_item_name == "Example Product"
    assert serials[0].product_batch_number == "B1"
    assert serials[0].expiry_date.isoformat() == "2027-01-01"
    assert db_session.scalar(select(func.count(MasterOutboxEvent.id))) == 0


def test_allocation_rejects_wrong_franchise_and_conflicting_product_without_partial_writes(db_session, monkeypatch):
    _setup(monkeypatch)
    number = "SQR-" + "C" * 32
    wrong_node = _payload(number)
    wrong_node["franchise_code"] = "OTHER"
    with pytest.raises(MasterSyncError, match="different franchise"):
        master_sync.apply_master_command(db_session, _command("wrong-node", wrong_node))
    db_session.rollback()
    assert db_session.scalar(select(func.count(Serial.id))) == 0

    db_session.add(
        Product(
            product_code="P-01", product_name="Different product", hsn="", gst_rate=18,
            unit="Pcs", tally_stock_item_name="Example Product",
        )
    )
    db_session.commit()
    with pytest.raises(MasterSyncError, match="different product_name"):
        master_sync.apply_master_command(db_session, _command("collision", _payload(number)))
    db_session.rollback()
    assert db_session.scalar(select(func.count(Serial.id))) == 0


def test_allocation_rejects_reused_serial_even_for_same_product(db_session, monkeypatch):
    _setup(monkeypatch)
    number = "SQR-" + "D" * 32
    master_sync.apply_master_command(db_session, _command("first", _payload(number)))
    db_session.commit()
    with pytest.raises(MasterSyncError, match="already exists"):
        master_sync.apply_master_command(db_session, _command("second", _payload(number)))
    db_session.rollback()
    assert db_session.scalar(select(func.count(Serial.id))) == 1


def test_replacement_command_retains_stock_metadata_and_uses_command_reference(db_session, monkeypatch):
    _setup(monkeypatch)
    old_number = "SQR-" + "E" * 32
    new_number = "SQR-" + "F" * 32
    master_sync.apply_master_command(db_session, _command("allocate", _payload(old_number)))
    db_session.commit()
    master_sync.enqueue_initial_inventory_snapshot(db_session, actor=SimpleNamespace(username="admin"))
    db_session.commit()
    old = db_session.scalar(select(Serial).where(Serial.serial_number == old_number))
    old.status = SerialStatus.IN_STOCK.value
    db_session.commit()

    command = _command(
        "reservation-123",
        {
            "version": 1,
            "franchise_code": "BLR-01",
            "old_serial_number": old_number,
            "new_serial_number": new_number,
            "expected_status": "IN_STOCK",
            "reason": "Damaged label",
        },
        "QR_REPLACE",
    )
    master_sync.apply_master_command(db_session, command)
    db_session.commit()
    master_sync.apply_master_command(db_session, command)
    db_session.commit()

    db_session.refresh(old)
    new = db_session.scalar(select(Serial).where(Serial.serial_number == new_number))
    assert old.status == "INVALID" and not old.active and old.replaced_by_id == new.id
    assert new.status == "IN_STOCK" and new.product_batch_number == old.product_batch_number
    assert new.warehouse == old.warehouse and new.expiry_date == old.expiry_date
    events = db_session.scalars(select(MasterOutboxEvent).order_by(MasterOutboxEvent.id)).all()
    assert len(events) == 2  # initial inventory, then the replacement
    assert events[-1].aggregate_id == "reservation-123"
    event = json.loads(events[-1].payload_json)["events"][0]
    assert event["reference"] == "reservation-123"
    assert event["reason_code"] == "QR_REPLACEMENT"
    assert [item["status"] for item in event["items"]] == ["INVALID", "IN_STOCK"]
    assert "notes" not in event


def test_lite_creation_services_and_routes_are_blocked(db_session, monkeypatch):
    _setup(monkeypatch)
    user = User(username="admin", password_hash="x", role="admin")
    product = Product(
        product_code="P-01", product_name="Example Product", hsn="", gst_rate=18,
        unit="Pcs", tally_stock_item_name="Example Product",
    )
    db_session.add_all([user, product])
    db_session.commit()
    with pytest.raises(InventoryError, match="Setuora Master"):
        inventory.generate_serials(db_session, product, 1)
    with pytest.raises(InventoryError, match="Setuora Master"):
        replacement.replace_barcode_serial(db_session, user, "SQR-MISSING", "SQR-" + "F" * 32)

    monkeypatch.setattr(barcode_assignment, "require_permission", lambda *_: user)
    monkeypatch.setattr(replacements, "require_permission", lambda *_: user)
    monkeypatch.setattr(products, "require_user", lambda *_: user)
    with pytest.raises(HTTPException) as single:
        barcode_assignment.generate_assignment(_request("/barcode-assignment/generate"), db=db_session)
    with pytest.raises(HTTPException) as bulk:
        barcode_assignment.bulk_assignment(_request("/barcode-assignment/bulk"), db=db_session)
    with pytest.raises(HTTPException) as product_route:
        products.generate_product_serials(_request("/products/1/generate"), product.id, db=db_session)
    with pytest.raises(HTTPException) as replace_route:
        replacements.replace_qr(_request("/qr-replacement"), db=db_session)
    assert all(exc.value.status_code == 403 for exc in (single, bulk, product_route, replace_route))
    assert db_session.scalar(select(func.count(Serial.id))) == 0
