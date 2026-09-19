import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    InventoryTransaction,
    LocalTransfer,
    LocalTransferItem,
    MasterInboxCommand,
    MasterOutboxEvent,
    Product,
    Serial,
    SerialStatus,
    TransactionType,
    TransferDirection,
    TransferStatus,
    User,
)
from app.services import inventory as inventory_service
from app.services import master_sync
from app.services.master_sync import apply_master_command
from app.services.transfer import (
    TransferError,
    add_outbound_serial,
    apply_transfer_available_command,
    create_outbound_transfer,
    dispatch_outbound_transfer,
    finalize_inbound_receipt,
    remove_outbound_serial,
    scan_inbound_transfer_item,
)


def _settings(code: str):
    return SimpleNamespace(
        app_mode="lite",
        franchise_code=code,
        master_sync_enabled=True,
        master_url="https://master.example",
        master_api_key="test-secret",
        master_request_timeout_seconds=3,
    )


def _product(code: str = "TR-PROD"):
    return Product(
        product_code=code,
        product_name="Transfer Product",
        category="Nutrition",
        brand="Setuora",
        hsn="2106",
        gst_rate=12,
        unit="Pcs",
        default_rate=80,
        tally_stock_item_name="Transfer Product",
    )


def _seed_stock(db, count=2):
    user = User(username="transfer-admin", password_hash="x", role="admin")
    product = _product()
    db.add_all([user, product])
    db.commit()
    # These represent stock already received from Master or enrolled during
    # migration; Lite no longer allocates QR identities itself.
    serials = [
        Serial(
            serial_number=f"SQR-{index + 1:032X}",
            product_id=product.id,
            status=SerialStatus.IN_STOCK.value,
        )
        for index in range(count)
    ]
    db.add_all(serials)
    db.commit()
    return user, product, serials


def _initialize_empty_node(db) -> None:
    assert master_sync.enqueue_initial_inventory_snapshot(
        db,
        actor=SimpleNamespace(username="initialization-test"),
    ) == (1, 0)


def test_dispatch_reservation_is_atomic_when_one_serial_is_no_longer_stock(
    db_session,
    monkeypatch,
):
    source_settings = _settings("SRC-ATOMIC")
    monkeypatch.setattr(master_sync, "get_settings", lambda: source_settings)
    monkeypatch.setattr(inventory_service, "get_settings", lambda: source_settings)
    user, _product_row, serials = _seed_stock(db_session)
    transfer = create_outbound_transfer(db_session, user, "DEST-ATOMIC")
    removable = add_outbound_serial(db_session, transfer, serials[0].serial_number)
    remove_outbound_serial(db_session, transfer, removable.id)
    assert db_session.get(LocalTransferItem, removable.id) is None
    add_outbound_serial(db_session, transfer, serials[0].serial_number)
    add_outbound_serial(db_session, transfer, serials[1].serial_number)

    serials[1].status = SerialStatus.SOLD.value
    db_session.commit()
    with pytest.raises(TransferError, match="no longer available"):
        dispatch_outbound_transfer(db_session, transfer, user)

    db_session.refresh(serials[0])
    db_session.refresh(transfer)
    assert serials[0].status == SerialStatus.IN_STOCK.value
    assert transfer.status == TransferStatus.DRAFT.value
    assert db_session.scalar(select(func.count(MasterOutboxEvent.id))) == 0
    assert db_session.scalar(select(func.count(InventoryTransaction.id))) == 0


def test_transfer_commands_are_idempotent_and_receipts_can_be_partial(
    monkeypatch,
):
    source_engine = create_engine("sqlite:///:memory:")
    destination_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(source_engine)
    Base.metadata.create_all(destination_engine)
    SourceSession = sessionmaker(bind=source_engine)
    DestinationSession = sessionmaker(bind=destination_engine)
    source = SourceSession()
    destination = DestinationSession()
    source_settings = _settings("SRC-01")
    destination_settings = _settings("DEST-02")
    try:
        monkeypatch.setattr(master_sync, "get_settings", lambda: source_settings)
        monkeypatch.setattr(inventory_service, "get_settings", lambda: source_settings)
        _initialize_empty_node(source)
        source_user, _product_row, source_serials = _seed_stock(source)
        outbound = create_outbound_transfer(source, source_user, "DEST-02")
        first_item = add_outbound_serial(source, outbound, source_serials[0].serial_number)
        add_outbound_serial(source, outbound, source_serials[1].serial_number)

        with pytest.raises(TransferError, match="already in this transfer"):
            add_outbound_serial(source, outbound, source_serials[0].serial_number)
        competing = create_outbound_transfer(source, source_user, "DEST-03")
        with pytest.raises(TransferError, match="another transfer"):
            add_outbound_serial(source, competing, source_serials[0].serial_number)

        outbound = dispatch_outbound_transfer(source, outbound, source_user)
        source.refresh(first_item)
        assert outbound.status == TransferStatus.DISPATCHED.value
        assert {serial.status for serial in source_serials} == {
            SerialStatus.IN_TRANSIT.value
        }
        dispatch_event = source.scalar(
            select(MasterOutboxEvent).where(
                MasterOutboxEvent.event_type == "TRANSFER_DISPATCHED"
            )
        )
        dispatch_payload = json.loads(dispatch_event.payload_json)["events"][0]
        assert dispatch_payload["type"] == "TRANSFER_DISPATCHED"
        assert dispatch_payload["destination_franchise_code"] == "DEST-02"
        assert dispatch_payload["transfer_id"] == outbound.transfer_uuid
        assert len(dispatch_payload["items"]) == 2
        assert dispatch_payload["items"][0]["tally_stock_item_name"]

        monkeypatch.setattr(master_sync, "get_settings", lambda: destination_settings)
        _initialize_empty_node(destination)
        destination_user = User(
            username="destination-admin",
            password_hash="x",
            role="admin",
        )
        destination.add(destination_user)
        destination.commit()
        available_command = {
            "command_id": "available-command-1",
            "command_type": "TRANSFER_INCOMING",
            "schema_version": 1,
            "payload": {
                "transfer_id": dispatch_payload["transfer_id"],
                "reference": dispatch_payload["reference"],
                "source_franchise": {
                    "code": "SRC-01",
                    "name": "Source Franchise",
                },
                "items": dispatch_payload["items"],
            },
        }
        apply_master_command(destination, available_command)
        destination.commit()
        # A re-delivered command must not create any duplicate product,
        # transfer, serial, or manifest rows.
        apply_master_command(destination, available_command)
        destination.commit()

        assert destination.scalar(select(func.count(MasterInboxCommand.id))) == 1
        assert destination.scalar(select(func.count(LocalTransfer.id))) == 1
        assert destination.scalar(select(func.count(LocalTransferItem.id))) == 2
        assert destination.scalar(select(func.count(Serial.id))) == 2
        inbound = destination.scalar(
            select(LocalTransfer).where(
                LocalTransfer.direction == TransferDirection.INBOUND.value
            )
        )
        assert inbound.status == TransferStatus.AWAITING_RECEIPT.value
        inbound_serials = destination.scalars(
            select(Serial).order_by(Serial.serial_number)
        ).all()
        assert {serial.status for serial in inbound_serials} == {
            SerialStatus.IN_TRANSIT.value
        }

        with pytest.raises(TransferError, match="not part"):
            scan_inbound_transfer_item(destination, inbound, "NOT-IN-MANIFEST")
        first_number = inbound_serials[0].serial_number
        scan_inbound_transfer_item(destination, inbound, first_number)
        with pytest.raises(TransferError, match="already been scanned"):
            scan_inbound_transfer_item(destination, inbound, first_number)
        inbound = finalize_inbound_receipt(destination, inbound, destination_user)

        assert inbound.status == TransferStatus.PARTIALLY_RECEIVED.value
        destination.refresh(inbound_serials[0])
        destination.refresh(inbound_serials[1])
        assert inbound_serials[0].status == SerialStatus.IN_STOCK.value
        assert inbound_serials[1].status == SerialStatus.IN_TRANSIT.value
        receipt_events = destination.scalars(
            select(MasterOutboxEvent)
            .where(MasterOutboxEvent.event_type == "TRANSFER_RECEIVED")
            .order_by(MasterOutboxEvent.id)
        ).all()
        assert len(receipt_events) == 1
        partial_payload = json.loads(receipt_events[0].payload_json)["events"][0]
        assert partial_payload["type"] == "TRANSFER_RECEIVED"
        assert partial_payload["transfer_id"] == outbound.transfer_uuid
        assert [item["serial_number"] for item in partial_payload["items"]] == [
            first_number
        ]

        monkeypatch.setattr(master_sync, "get_settings", lambda: source_settings)
        apply_master_command(
            source,
            {
                "command_id": "receipt-command-partial",
                "command_type": "TRANSFER_RECEIPT_STATUS",
                "payload": {
                    "transfer_id": outbound.transfer_uuid,
                    "reference": outbound.transfer_uuid,
                    "status": TransferStatus.PARTIALLY_RECEIVED.value,
                    "received_serial_numbers": [first_number],
                    "destination_franchise": {"code": "DEST-02"},
                },
            },
        )
        source.commit()
        source.refresh(outbound)
        assert outbound.status == TransferStatus.PARTIALLY_RECEIVED.value
        assert sum(item.received for item in outbound.items) == 1

        monkeypatch.setattr(master_sync, "get_settings", lambda: destination_settings)
        second_number = inbound_serials[1].serial_number
        scan_inbound_transfer_item(destination, inbound, second_number)
        inbound = finalize_inbound_receipt(destination, inbound, destination_user)
        assert inbound.status == TransferStatus.RECEIVED.value
        destination.refresh(inbound_serials[1])
        assert inbound_serials[1].status == SerialStatus.IN_STOCK.value
        receipt_events = destination.scalars(
            select(MasterOutboxEvent)
            .where(MasterOutboxEvent.event_type == "TRANSFER_RECEIVED")
            .order_by(MasterOutboxEvent.id)
        ).all()
        assert len(receipt_events) == 2
        full_payload = json.loads(receipt_events[-1].payload_json)["events"][0]
        assert full_payload["type"] == "TRANSFER_RECEIVED"
        assert full_payload["transfer_id"] == outbound.transfer_uuid
        assert [item["serial_number"] for item in full_payload["items"]] == [
            second_number
        ]

        monkeypatch.setattr(master_sync, "get_settings", lambda: source_settings)
        apply_master_command(
            source,
            {
                "command_id": "receipt-command-full",
                "command_type": "TRANSFER_RECEIPT_STATUS",
                "payload": {
                    "transfer_id": outbound.transfer_uuid,
                    "reference": outbound.transfer_uuid,
                    "status": TransferStatus.RECEIVED.value,
                    "received_serial_numbers": [second_number],
                    "destination_franchise": {"code": "DEST-02"},
                },
            },
        )
        source.commit()
        source.refresh(outbound)
        assert outbound.status == TransferStatus.RECEIVED.value
        assert all(item.received for item in outbound.items)
        assert all(not item.serial.active for item in outbound.items)

        destination_transactions = destination.scalars(
            select(InventoryTransaction)
            .where(
                InventoryTransaction.transaction_type
                == TransactionType.TRANSFER_IN.value
            )
            .order_by(InventoryTransaction.id)
        ).all()
        assert len(destination_transactions) == 2
    finally:
        source.close()
        destination.close()
        source_engine.dispose()
        destination_engine.dispose()


def test_dispatch_before_initialization_rolls_back_with_clear_error(
    db_session,
    monkeypatch,
):
    settings = _settings("SRC-PREINIT")
    monkeypatch.setattr(master_sync, "get_settings", lambda: settings)
    monkeypatch.setattr(inventory_service, "get_settings", lambda: settings)
    user, _product_row, serials = _seed_stock(db_session, count=1)
    transfer = create_outbound_transfer(db_session, user, "DEST-PREINIT")
    add_outbound_serial(db_session, transfer, serials[0].serial_number)

    with pytest.raises(TransferError, match="Initialize inventory"):
        dispatch_outbound_transfer(db_session, transfer, user)

    db_session.refresh(transfer)
    db_session.refresh(serials[0])
    assert transfer.status == TransferStatus.DRAFT.value
    assert transfer.dispatched_at is None
    assert serials[0].status == SerialStatus.IN_STOCK.value
    assert db_session.scalar(select(func.count(MasterOutboxEvent.id))) == 0
    assert db_session.scalar(select(func.count(InventoryTransaction.id))) == 0


def test_receipt_before_initialization_rolls_back_with_clear_error(
    db_session,
    monkeypatch,
):
    settings = _settings("DEST-PREINIT")
    monkeypatch.setattr(master_sync, "get_settings", lambda: settings)
    monkeypatch.setattr(inventory_service, "get_settings", lambda: settings)
    user = User(username="receipt-admin", password_hash="x", role="admin")
    db_session.add(user)
    db_session.commit()
    command = {
        "command_id": "preinit-inbound-command",
        "type": "TRANSFER_AVAILABLE",
        "payload": {
            "transfer": {
                "transfer_uuid": "preinit-inbound-transfer",
                "source_franchise_code": "SOURCE-PREINIT",
                "destination_franchise_code": "DEST-PREINIT",
            },
            "items": [
                {
                    "manifest_serial_number": "SOURCE-PREINIT-ITEM-000001",
                    "product": {
                        "product_code": "PREINIT-ITEM",
                        "product_name": "Pre-initialization Item",
                        "hsn": "2106",
                        "gst_rate": 5,
                        "unit": "Pcs",
                        "default_rate": 10,
                        "tally_stock_item_name": "Pre-initialization Item",
                    },
                    "serial": {
                        "serial_number": "SOURCE-PREINIT-ITEM-000001",
                        "status": SerialStatus.IN_STOCK.value,
                    },
                }
            ],
        },
    }
    apply_master_command(db_session, command)
    db_session.commit()
    inbound = db_session.scalar(
        select(LocalTransfer).where(
            LocalTransfer.direction == TransferDirection.INBOUND.value
        )
    )
    serial = db_session.scalar(select(Serial))
    scan_inbound_transfer_item(db_session, inbound, serial.serial_number)

    with pytest.raises(TransferError, match="Initialize inventory"):
        finalize_inbound_receipt(db_session, inbound, user)

    db_session.refresh(inbound)
    db_session.refresh(serial)
    db_session.refresh(inbound.items[0])
    assert inbound.status == TransferStatus.AWAITING_RECEIPT.value
    assert inbound.received_at is None
    assert serial.status == SerialStatus.IN_TRANSIT.value
    assert inbound.items[0].received is False
    assert db_session.scalar(select(func.count(MasterOutboxEvent.id))) == 0
    assert db_session.scalar(select(func.count(InventoryTransaction.id))) == 0


def test_inbound_transfer_rejects_local_product_code_identity_collision(
    db_session,
    monkeypatch,
):
    destination_settings = _settings("DEST-COLLISION")
    monkeypatch.setattr(master_sync, "get_settings", lambda: destination_settings)
    monkeypatch.setattr(inventory_service, "get_settings", lambda: destination_settings)
    local_product = _product("SAME-CODE")
    local_product.product_name = "Local Vitamin"
    local_product.hsn = "1111"
    db_session.add(local_product)
    db_session.commit()

    command = {
        "command_id": "collision-command",
        "type": "TRANSFER_AVAILABLE",
        "payload": {
            "transfer": {
                "transfer_uuid": "collision-transfer",
                "source_franchise_code": "SRC-COLLISION",
                "destination_franchise_code": "DEST-COLLISION",
            },
            "items": [
                {
                    "manifest_serial_number": "SRC-COLLISION-SAME-CODE-000001",
                    "product": {
                        "product_code": "SAME-CODE",
                        "product_name": "Different Medicine",
                        "hsn": "3004",
                        "gst_rate": 12,
                        "unit": "Pcs",
                        "default_rate": 80,
                        "tally_stock_item_name": "Different Medicine",
                    },
                    "serial": {
                        "serial_number": "SRC-COLLISION-SAME-CODE-000001",
                        "status": "IN_STOCK",
                    },
                }
            ],
        },
    }

    with pytest.raises(TransferError, match="conflicts with local product identity"):
        apply_master_command(db_session, command)
    db_session.rollback()

    assert db_session.scalar(select(func.count(LocalTransfer.id))) == 0
    assert db_session.scalar(select(func.count(Serial.id))) == 0


def test_returning_qr_reactivates_only_a_completed_outbound_history(
    db_session,
    monkeypatch,
):
    settings = _settings("DEST-RETURN")
    monkeypatch.setattr(master_sync, "get_settings", lambda: settings)
    monkeypatch.setattr(inventory_service, "get_settings", lambda: settings)
    user, product, serials = _seed_stock(db_session, count=2)
    returning, unsafe = serials
    returning.status = SerialStatus.IN_TRANSIT.value
    returning.active = False
    unsafe.status = SerialStatus.IN_TRANSIT.value
    unsafe.active = False
    prior = LocalTransfer(
        transfer_uuid="prior-outbound",
        direction=TransferDirection.OUTBOUND.value,
        status=TransferStatus.RECEIVED.value,
        local_franchise_code="DEST-RETURN",
        peer_code="SOURCE-RETURN",
        created_by_id=user.id,
    )
    db_session.add(prior)
    db_session.flush()
    db_session.add(
        LocalTransferItem(
            transfer_id=prior.id,
            serial_id=returning.id,
            manifest_serial_number=returning.serial_number,
            product_code=product.product_code,
            product_name=product.product_name,
            scanned=True,
            received=True,
        )
    )
    db_session.commit()

    def command_payload(transfer_uuid: str, serial: Serial):
        return {
            "transfer": {
                "transfer_uuid": transfer_uuid,
                "source_franchise_code": "SOURCE-RETURN",
                "destination_franchise_code": "DEST-RETURN",
            },
            "items": [
                {
                    "manifest_serial_number": serial.serial_number,
                    "product": {
                        "product_code": product.product_code,
                        "product_name": product.product_name,
                        "hsn": product.hsn,
                        "gst_rate": product.gst_rate,
                        "unit": product.unit,
                        "default_rate": product.default_rate,
                        "tally_stock_item_name": product.tally_stock_item_name,
                    },
                    "serial": {
                        "serial_number": serial.serial_number,
                        "status": SerialStatus.IN_STOCK.value,
                    },
                }
            ],
        }

    inbound = apply_transfer_available_command(
        db_session,
        command_payload("return-transfer", returning),
    )
    db_session.commit()
    db_session.refresh(returning)

    assert inbound.items[0].serial_id == returning.id
    assert returning.active is True
    assert returning.status == SerialStatus.IN_TRANSIT.value

    with pytest.raises(TransferError, match="already exists"):
        apply_transfer_available_command(
            db_session,
            command_payload("unsafe-return-transfer", unsafe),
        )
    db_session.rollback()
