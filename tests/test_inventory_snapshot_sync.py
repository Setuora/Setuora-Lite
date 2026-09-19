import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app import config as config_module
from app.models import (
    MasterOutboxEvent,
    Product,
    Serial,
    SerialStatus,
    StockRelocation,
    StorageLocation,
    User,
)
from app.services import inventory as inventory_service
from app.services import master_sync
from app.services import replacement as replacement_service
from app.services.inventory import InventoryError
from app.services.relocation import MoveItem, RelocationError, relocate_stock
from app.services.replacement import replace_qr_serial


def _settings():
    return SimpleNamespace(
        app_mode="lite",
        master_sync_enabled=True,
        franchise_code="FR01",
    )


def _product(code: str = "PROD-1") -> Product:
    return Product(
        product_code=code,
        product_name="Test product",
        hsn="1234",
        gst_rate=5,
        unit="Pcs",
        default_rate=100,
        tally_stock_item_name="Test Product",
        active=True,
    )


def _location(code: str, warehouse: str) -> StorageLocation:
    return StorageLocation(
        code=code,
        warehouse=warehouse,
        zone="Z1",
        section="S1",
        rack="R1",
        shelf="SH1",
        bin=code,
        active=True,
    )


def _enable_lite_sync(monkeypatch, *, transport_enabled=True) -> None:
    settings = _settings()
    settings.master_sync_enabled = transport_enabled
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(master_sync, "get_settings", lambda: settings)
    monkeypatch.setattr(inventory_service, "get_settings", lambda: settings)
    monkeypatch.setattr(replacement_service, "get_settings", lambda: settings)


def _initialize_empty_node(db) -> None:
    assert master_sync.enqueue_initial_inventory_snapshot(
        db,
        actor=SimpleNamespace(username="initialization-test"),
    ) == (1, 0)


@pytest.mark.parametrize("transport_enabled", [True, False])
def test_qr_replacement_enqueues_old_and_new_serial_snapshot(
    db_session,
    monkeypatch,
    transport_enabled,
):
    _enable_lite_sync(monkeypatch, transport_enabled=transport_enabled)
    _initialize_empty_node(db_session)
    user = User(username="admin", password_hash="x", role="admin")
    product = _product()
    old = Serial(
        serial_number="FR01-PROD-1-000001",
        product=product,
        status=SerialStatus.IN_STOCK.value,
        active=True,
        warehouse="OLD",
    )
    db_session.add_all([user, product, old])
    db_session.commit()

    new_number = "SQR-00000000000000000000000000000099"
    master_sync.apply_master_command(
        db_session,
        {
            "command_id": str(uuid4()),
            "type": "QR_REPLACE",
            "payload": {
                "version": 1,
                "franchise_code": "FR01",
                "old_serial_number": old.serial_number,
                "new_serial_number": new_number,
                "expected_status": SerialStatus.IN_STOCK.value,
                "reason": "Damaged label",
            },
        },
    )
    db_session.commit()
    event = db_session.scalar(
        select(MasterOutboxEvent)
        .where(MasterOutboxEvent.aggregate_type == "QR_REPLACEMENT")
    )
    payload = json.loads(event.payload_json)["events"][0]
    items = {item["serial_number"]: item for item in payload["items"]}

    assert payload["type"] == "STOCK_SNAPSHOT"
    assert items[old.serial_number]["status"] == SerialStatus.INVALID.value
    assert items[new_number]["status"] == SerialStatus.IN_STOCK.value
    assert db_session.scalar(select(Serial).where(Serial.serial_number == new_number)) is not None


@pytest.mark.parametrize("transport_enabled", [True, False])
def test_relocation_enqueues_updated_warehouse_snapshot(
    db_session,
    monkeypatch,
    transport_enabled,
):
    _enable_lite_sync(monkeypatch, transport_enabled=transport_enabled)
    _initialize_empty_node(db_session)
    user = User(username="warehouse", password_hash="x", role="warehouse_manager")
    product = _product("MOVE-1")
    source = _location("SRC-1", "SOURCE")
    destination = _location("DEST-1", "DESTINATION")
    serial = Serial(
        serial_number="FR01-MOVE-1-000001",
        product=product,
        status=SerialStatus.IN_STOCK.value,
        active=True,
        warehouse=source.warehouse,
        location=source,
    )
    db_session.add_all([user, product, source, destination, serial])
    db_session.commit()

    relocate_stock(
        db_session,
        user=user,
        destination_id=destination.id,
        items=[
            MoveItem(
                product_id=product.id,
                quantity=1,
                source_location_id=source.id,
                serial_id=serial.id,
            )
        ],
        reason="Rebalance",
        device_used="test",
    )
    event = db_session.scalar(
        select(MasterOutboxEvent)
        .where(MasterOutboxEvent.aggregate_type == "RELOCATION")
    )
    payload = json.loads(event.payload_json)["events"][0]

    assert payload["type"] == "STOCK_SNAPSHOT"
    assert payload["items"][0]["serial_number"] == serial.serial_number
    assert payload["items"][0]["warehouse"] == destination.warehouse


@pytest.mark.parametrize("transport_enabled", [True, False])
def test_lite_local_qr_replacement_is_denied_without_mutation(
    db_session,
    monkeypatch,
    transport_enabled,
):
    _enable_lite_sync(monkeypatch, transport_enabled=transport_enabled)
    user = User(username="preinit-admin", password_hash="x", role="admin")
    product = _product("PREINIT-REPLACE")
    old = Serial(
        serial_number="FR01-PREINIT-REPLACE-000001",
        product=product,
        status=SerialStatus.IN_STOCK.value,
        active=True,
        warehouse="ORIGINAL",
    )
    db_session.add_all([user, product, old])
    db_session.commit()

    with pytest.raises(InventoryError, match="Setuora Master"):
        replace_qr_serial(
            db_session,
            user,
            old.serial_number,
            reason="Damaged label",
        )

    db_session.refresh(old)
    assert old.status == SerialStatus.IN_STOCK.value
    assert old.active is True
    assert old.replaced_by_id is None
    assert db_session.scalar(select(func.count(Serial.id))) == 1
    assert db_session.scalar(select(func.count(MasterOutboxEvent.id))) == 0


@pytest.mark.parametrize("transport_enabled", [True, False])
def test_relocation_before_initialization_rolls_back_with_clear_error(
    db_session,
    monkeypatch,
    transport_enabled,
):
    _enable_lite_sync(monkeypatch, transport_enabled=transport_enabled)
    user = User(
        username="preinit-warehouse",
        password_hash="x",
        role="warehouse_manager",
    )
    product = _product("PREINIT-MOVE")
    source = _location("PREINIT-SRC", "SOURCE")
    destination = _location("PREINIT-DEST", "DESTINATION")
    serial = Serial(
        serial_number="FR01-PREINIT-MOVE-000001",
        product=product,
        status=SerialStatus.IN_STOCK.value,
        active=True,
        warehouse=source.warehouse,
        location=source,
    )
    db_session.add_all([user, product, source, destination, serial])
    db_session.commit()

    with pytest.raises(RelocationError, match="Initialize inventory"):
        relocate_stock(
            db_session,
            user=user,
            destination_id=destination.id,
            items=[
                MoveItem(
                    product_id=product.id,
                    quantity=1,
                    source_location_id=source.id,
                    serial_id=serial.id,
                )
            ],
            reason="Pre-initialization check",
            device_used="test",
        )

    db_session.refresh(serial)
    assert serial.location_id == source.id
    assert serial.warehouse == source.warehouse
    assert db_session.scalar(select(func.count(StockRelocation.id))) == 0
    assert db_session.scalar(select(func.count(MasterOutboxEvent.id))) == 0
