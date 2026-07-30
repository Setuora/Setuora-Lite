from datetime import timedelta
import hashlib
from io import BytesIO
import json
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from starlette.requests import Request

from app import config as config_module
from app.models import (
    BatchStatus,
    BatchType,
    LocalTransfer,
    MasterInboxCommand,
    MasterOutboxEvent,
    MasterOutboxStatus,
    Product,
    Serial,
    SerialStatus,
    StorageLocation,
    User,
    utc_now,
)
from app.routers import batches as batches_router
from app.routers import lite_sync as lite_sync_router
from app.security import create_session_token
from app.services import inventory as inventory_service
from app.services import master_sync
from app.services.assignment import AssignmentLine, assign_barcodes_to_existing_stock
from app.services.inventory import (
    InventoryError,
    add_serial_to_batch,
    apply_batch_statuses,
    create_batch,
    generate_serials,
)


def _settings(**overrides):
    values = {
        "app_mode": "lite",
        "franchise_code": "BLR-01",
        "master_sync_enabled": True,
        "master_url": "https://master.example",
        "master_api_key": "never-print-this-key",
        "master_request_timeout_seconds": 3,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _initialize_empty_node(db):
    event_count, item_count = master_sync.enqueue_initial_inventory_snapshot(
        db,
        actor=SimpleNamespace(username="initialization-test"),
    )
    assert (event_count, item_count) == (1, 0)
    marker = db.scalar(
        select(MasterOutboxEvent).where(
            MasterOutboxEvent.aggregate_type
            == master_sync.INITIAL_INVENTORY_AGGREGATE_TYPE
        )
    )
    assert marker is not None
    marker.status = MasterOutboxStatus.SENT.value
    marker.sent_at = utc_now()
    db.commit()
    return marker


def _product(code: str = "SYNC-PROD") -> Product:
    return Product(
        product_code=code,
        product_name="Complete Sync Product",
        nickname="Sync Nickname",
        category="Nutrition",
        brand="Setuora",
        hsn="2106",
        gst_rate=18,
        unit="Pcs",
        default_rate=125.5,
        sales_discount_rate=2,
        tally_stock_item_name="Complete Sync Product",
        alternate_tally_stock_item_name="Sync Product Alias",
    )


def _max_length_network_item(index: int) -> dict:
    return {
        "serial_number": f"BLR-01-MAX-{index:06d}",
        "product_code": "P" * 80,
        "product_name": "界" * 180,
        "tally_stock_item_name": "薬" * 180,
        "hsn": "1" * 40,
        "gst_rate": 18,
        "unit": "件" * 40,
        "rate": 999999.99,
        "status": SerialStatus.IN_STOCK.value,
        "product_batch_number": "批" * 80,
        "mfg_date": "2026-01-01",
        "expiry_date": "2028-01-01",
        "warehouse": "倉" * 80,
    }


def _sale_batch(db, *, code: str = "SYNC-PROD"):
    user = User(username=f"user-{code.lower()}", password_hash="x", role="admin")
    product = _product(code)
    location = StorageLocation(
        code=f"LOC-{code}",
        warehouse="MAIN",
        zone=code,
        section="1",
        rack="R1",
        shelf="S1",
        bin="B1",
    )
    db.add_all([user, product, location])
    db.commit()
    serial = generate_serials(
        db,
        product,
        1,
        initial_status=SerialStatus.IN_STOCK,
    )[0]
    serial.location_id = location.id
    serial.warehouse = location.warehouse
    db.commit()
    batch = create_batch(
        db,
        user,
        BatchType.SALE,
        "Customer One",
        "Event snapshot notes",
        party_state="Karnataka",
        party_gst_registration_type="Regular",
        party_gst_name="Customer One GST",
        party_gstin="29ABCDE1234F1Z5",
        gst_treatment="INTRA_STATE",
        gst_cgst_rate=9,
        gst_sgst_rate=9,
    )
    add_serial_to_batch(db, batch, user, serial.serial_number)
    return user, product, serial, batch


def _request(user_id: int, path: str, *, app_mode: str = "legacy") -> Request:
    token = create_session_token(user_id)
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [(b"cookie", f"setuora_session={token}".encode())],
            "query_string": b"",
            "server": ("testserver", 80),
            "scheme": "http",
            "app": SimpleNamespace(
                state=SimpleNamespace(app_mode=app_mode),
            ),
        }
    )


def test_batch_event_is_transactional_and_contains_complete_immutable_snapshot(
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(master_sync, "get_settings", lambda: _settings())
    _initialize_empty_node(db_session)
    user, product, serial, batch = _sale_batch(db_session)

    apply_batch_statuses(db_session, batch, user)
    batch.status = BatchStatus.PENDING_SYNC.value
    event = master_sync.enqueue_batch_submitted_event(db_session, batch, user=user)
    event_id = event.event_id

    assert event.status == MasterOutboxStatus.PENDING.value
    assert event.sequence == event.id
    assert event.payload_sha256 == hashlib.sha256(
        event.payload_json.encode("utf-8")
    ).hexdigest()
    request_body = json.loads(event.payload_json)
    assert list(request_body) == ["events"]
    envelope = request_body["events"][0]
    assert envelope["event_id"] == event_id
    assert envelope["sequence"] == event.id
    assert envelope["type"] == "SALE"
    assert envelope["party_gstin"] == "29ABCDE1234F1Z5"
    line = envelope["items"][0]
    assert line["rate"] == 125.5
    assert line["product_code"] == product.product_code
    assert line["hsn"] == "2106"
    assert line["gst_rate"] == 18
    assert line["serial_number"] == serial.serial_number
    assert line["status"] == SerialStatus.SOLD.value
    assert line["warehouse"] == "MAIN"

    db_session.rollback()
    assert db_session.scalar(
        select(MasterOutboxEvent).where(MasterOutboxEvent.event_id == event_id)
    ) is None
    db_session.refresh(batch)
    db_session.refresh(serial)
    assert batch.status == BatchStatus.DRAFT.value
    assert serial.status == SerialStatus.IN_STOCK.value


class _Response:
    def __init__(self, body=b"{}", status=202):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def close(self):
        pass


def _accepted_response(request, *, event_id=None, sequence=None, last_sequence=None):
    submitted = json.loads(request.data)["events"][0]
    acknowledged_sequence = (
        submitted["sequence"] if sequence is None else sequence
    )
    return _Response(
        json.dumps(
            {
                "data": {
                    "acknowledgements": [
                        {
                            "event_id": event_id or submitted["event_id"],
                            "sequence": acknowledged_sequence,
                            "status": "ACCEPTED",
                        }
                    ],
                    "last_sequence": (
                        acknowledged_sequence
                        if last_sequence is None
                        else last_sequence
                    ),
                },
                "error": None,
                "request_id": str(uuid4()),
            }
        ).encode(),
        status=200,
    )


def _node_response(
    *,
    code: str = "BLR-01",
    last_sequence: int = 0,
    next_sequence: int = 1,
) -> _Response:
    return _Response(
        json.dumps(
            {
                "data": {
                    "code": code,
                    "last_sequence": last_sequence,
                    "next_sequence": next_sequence,
                },
                "error": None,
                "request_id": str(uuid4()),
            }
        ).encode(),
        status=200,
    )


def test_master_enrollment_identity_verification_succeeds_with_exact_empty_node(
    monkeypatch,
):
    settings = _settings(franchise_code="NODE-01")
    monkeypatch.setattr(master_sync, "get_settings", lambda: settings)
    calls = []

    def opener(request, **kwargs):
        calls.append((request, kwargs))
        return _node_response(code="NODE-01")

    node = master_sync.verify_master_enrollment_identity(opener=opener)

    assert node["code"] == "NODE-01"
    request, kwargs = calls[0]
    assert request.get_method() == "GET"
    assert request.full_url == "https://master.example/api/v1/node"
    assert request.get_header("Authorization") == (
        f"Bearer {settings.master_api_key}"
    )
    assert kwargs["timeout"] == settings.master_request_timeout_seconds


def test_master_enrollment_identity_rejects_malformed_response(monkeypatch):
    monkeypatch.setattr(master_sync, "get_settings", lambda: _settings())

    with pytest.raises(master_sync.MasterSyncError, match="malformed GET"):
        master_sync.verify_master_enrollment_identity(
            opener=lambda *_args, **_kwargs: _Response(b"{not-json", status=200)
        )


def test_master_enrollment_identity_reports_connection_timeout(monkeypatch):
    monkeypatch.setattr(master_sync, "get_settings", lambda: _settings())

    with pytest.raises(master_sync.MasterSyncError, match="connection failed"):
        master_sync.verify_master_enrollment_identity(
            opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                TimeoutError("timed out")
            )
        )


def test_master_enrollment_identity_rejects_wrong_franchise_code(monkeypatch):
    monkeypatch.setattr(master_sync, "get_settings", lambda: _settings())

    with pytest.raises(master_sync.MasterSyncError, match="different franchise code"):
        master_sync.verify_master_enrollment_identity(
            opener=lambda *_args, **_kwargs: _node_response(code="OTHER-01")
        )


@pytest.mark.parametrize(
    ("last_sequence", "next_sequence"),
    [(1, 2), (0, 2)],
)
def test_master_enrollment_identity_rejects_non_pristine_cursor(
    monkeypatch,
    last_sequence,
    next_sequence,
):
    monkeypatch.setattr(master_sync, "get_settings", lambda: _settings())

    with pytest.raises(master_sync.MasterSyncError, match="expected cursor 0/1"):
        master_sync.verify_master_enrollment_identity(
            opener=lambda *_args, **_kwargs: _node_response(
                last_sequence=last_sequence,
                next_sequence=next_sequence,
            )
        )


def test_admin_initialization_verifies_master_before_local_enqueue(
    db_session,
    monkeypatch,
):
    user = User(username="enrollment-admin", password_hash="x", role="admin")
    db_session.add(user)
    db_session.commit()
    calls = []
    monkeypatch.setattr(
        lite_sync_router,
        "verify_master_enrollment_identity",
        lambda: calls.append("verify"),
    )

    def enqueue(_db, *, actor):
        assert actor.id == user.id
        assert calls == ["verify"]
        calls.append("enqueue")
        return 1, 0

    monkeypatch.setattr(
        lite_sync_router,
        "enqueue_initial_inventory_snapshot",
        enqueue,
    )

    response = lite_sync_router.initialize_inventory(
        _request(
            user.id,
            "/master-connection/initialize-inventory",
            app_mode="lite",
        ),
        db_session,
    )

    assert response.status_code == 303
    assert calls == ["verify", "enqueue"]


def test_admin_initialization_does_not_enqueue_when_master_preflight_fails(
    db_session,
    monkeypatch,
):
    user = User(username="blocked-enrollment-admin", password_hash="x", role="admin")
    db_session.add(user)
    db_session.commit()
    monkeypatch.setattr(
        lite_sync_router,
        "verify_master_enrollment_identity",
        lambda: (_ for _ in ()).throw(
            master_sync.MasterSyncError("Master node cursor is not pristine.")
        ),
    )
    monkeypatch.setattr(
        lite_sync_router,
        "enqueue_initial_inventory_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("baseline enqueue must not run")
        ),
    )

    response = lite_sync_router.initialize_inventory(
        _request(
            user.id,
            "/master-connection/initialize-inventory",
            app_mode="lite",
        ),
        db_session,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert db_session.scalar(select(func.count(MasterOutboxEvent.id))) == 0


def test_committed_outbox_payload_cannot_be_mutated(
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(master_sync, "get_settings", lambda: _settings())
    _initialize_empty_node(db_session)
    event = master_sync.enqueue_outbox_event(
        db_session,
        event_type="HEARTBEAT",
        aggregate_type="NODE",
        aggregate_id="heartbeat",
        payload={"reference": "heartbeat", "actor": "test", "items": []},
    )
    db_session.commit()
    event.payload_json = "{}"
    with pytest.raises(ValueError, match="immutable"):
        db_session.commit()
    db_session.rollback()


def test_valid_5000_item_ordinary_event_over_five_mib_is_rejected_before_commit(
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(master_sync, "get_settings", lambda: _settings())
    _initialize_empty_node(db_session)
    items = [
        _max_length_network_item(index)
        for index in range(master_sync.MASTER_EVENT_MAX_ITEMS)
    ]

    with pytest.raises(
        master_sync.MasterSyncError,
        match="UTF-8 bytes.*Split this transaction",
    ):
        master_sync.enqueue_outbox_event(
            db_session,
            event_type="STOCK_SNAPSHOT",
            aggregate_type="BATCH",
            aggregate_id="oversized-valid-transaction",
            payload={
                "reference": "oversized-valid-transaction",
                "actor": "size-test",
                "items": items,
            },
        )
    db_session.rollback()

    rows = db_session.scalars(
        select(MasterOutboxEvent).order_by(MasterOutboxEvent.id)
    ).all()
    assert len(rows) == 1
    assert rows[0].aggregate_type == master_sync.INITIAL_INVENTORY_AGGREGATE_TYPE


def test_oversized_batch_submission_rolls_back_stock_and_outbox(
    db_session,
    monkeypatch,
):
    settings = _settings()
    monkeypatch.setattr(master_sync, "get_settings", lambda: settings)
    _initialize_empty_node(db_session)
    user, _product_row, serial, batch = _sale_batch(
        db_session,
        code="OVERSIZED-PROD",
    )
    batch.party_name = "界" * 180
    db_session.commit()
    monkeypatch.setattr(master_sync, "MASTER_EVENT_MAX_BODY_BYTES", 1024)

    response = batches_router.submit_batch(
        _request(
            user.id,
            f"/batches/{batch.id}/submit",
            app_mode="lite",
        ),
        batch.id,
        db_session,
    )

    assert response.status_code == 400
    assert b"UTF-8 bytes" in response.body
    assert b"Split this transaction" in response.body
    db_session.refresh(batch)
    db_session.refresh(serial)
    assert batch.status == BatchStatus.DRAFT.value
    assert serial.status == SerialStatus.IN_STOCK.value
    assert db_session.scalar(select(func.count(MasterOutboxEvent.id))) == 1


def test_outbox_retries_oldest_event_before_sending_newer_events(
    db_session,
    monkeypatch,
):
    settings = _settings()
    monkeypatch.setattr(master_sync, "get_settings", lambda: settings)
    _initialize_empty_node(db_session)
    first = master_sync.enqueue_outbox_event(
        db_session,
        event_type="HEARTBEAT",
        aggregate_type="TEST",
        aggregate_id="one",
        payload={"reference": "one", "actor": "test", "items": []},
    )
    second = master_sync.enqueue_outbox_event(
        db_session,
        event_type="HEARTBEAT",
        aggregate_type="TEST",
        aggregate_id="two",
        payload={"reference": "two", "actor": "test", "items": []},
    )
    db_session.commit()
    first_id = first.event_id
    second_id = second.event_id
    calls = []

    def offline(request, **_kwargs):
        calls.append(json.loads(request.data)["events"][0]["event_id"])
        raise URLError(f"offline: {settings.master_api_key}")

    assert master_sync.push_pending_events(db_session, opener=offline) == 0
    db_session.refresh(first)
    db_session.refresh(second)
    assert calls == [first_id]
    assert first.status == MasterOutboxStatus.FAILED.value
    assert second.status == MasterOutboxStatus.PENDING.value
    assert settings.master_api_key not in first.last_error

    first.next_attempt_at = utc_now() - timedelta(seconds=1)
    db_session.commit()

    def online(request, **_kwargs):
        calls.append(json.loads(request.data)["events"][0]["event_id"])
        return _accepted_response(request)

    assert master_sync.push_pending_events(db_session, opener=online) == 2
    assert calls == [first_id, first_id, second_id]
    assert [row.status for row in db_session.scalars(
        select(MasterOutboxEvent)
        .where(
            MasterOutboxEvent.aggregate_type
            != master_sync.INITIAL_INVENTORY_AGGREGATE_TYPE
        )
        .order_by(MasterOutboxEvent.id)
    )] == [MasterOutboxStatus.SENT.value, MasterOutboxStatus.SENT.value]


def test_malformed_or_mismatched_2xx_ack_is_retryable_not_sent(
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(master_sync, "get_settings", lambda: _settings())
    _initialize_empty_node(db_session)
    event = master_sync.enqueue_outbox_event(
        db_session,
        event_type="HEARTBEAT",
        aggregate_type="NODE",
        aggregate_id="ack-safety",
        payload={"reference": "ack-safety", "actor": "test", "items": []},
    )
    db_session.commit()

    assert master_sync.push_pending_events(
        db_session,
        opener=lambda *_args, **_kwargs: _Response(
            b"<html>proxy login</html>",
            status=200,
        ),
    ) == 0
    db_session.refresh(event)
    assert event.status == MasterOutboxStatus.FAILED.value
    assert event.sent_at is None
    assert event.next_attempt_at is not None

    event.next_attempt_at = utc_now() - timedelta(seconds=1)
    db_session.commit()
    assert master_sync.push_pending_events(
        db_session,
        opener=lambda request, **_kwargs: _accepted_response(
            request,
            event_id=str(uuid4()),
        ),
    ) == 0
    db_session.refresh(event)
    assert event.status == MasterOutboxStatus.FAILED.value
    assert event.sent_at is None
    assert event.next_attempt_at is not None

    event.next_attempt_at = utc_now() - timedelta(seconds=1)
    db_session.commit()
    assert master_sync.push_pending_events(
        db_session,
        opener=lambda request, **_kwargs: _accepted_response(request),
    ) == 1
    db_session.refresh(event)
    assert event.status == MasterOutboxStatus.SENT.value


@pytest.mark.parametrize(
    ("status_code", "retryable"),
    [
        (401, True),
        (403, True),
        (408, True),
        (409, False),
        (412, False),
        (429, True),
        (500, True),
        (400, False),
        (413, False),
        (415, False),
        (422, False),
    ],
)
def test_http_failure_retry_classification(
    db_session,
    monkeypatch,
    status_code,
    retryable,
):
    monkeypatch.setattr(master_sync, "get_settings", lambda: _settings())
    _initialize_empty_node(db_session)
    event = master_sync.enqueue_outbox_event(
        db_session,
        event_type="HEARTBEAT",
        aggregate_type="NODE",
        aggregate_id=f"http-{status_code}",
        payload={"reference": f"http-{status_code}", "actor": "test", "items": []},
    )
    db_session.commit()

    def reject(request, **_kwargs):
        raise HTTPError(request.full_url, status_code, "rejected", {}, None)

    assert master_sync.push_pending_events(db_session, opener=reject) == 0
    db_session.refresh(event)
    assert event.status == MasterOutboxStatus.FAILED.value
    assert (event.next_attempt_at is not None) is retryable


@pytest.mark.parametrize(
    ("status_code", "error_code", "message", "details", "detail_key"),
    [
        (
            409,
            "SEQUENCE_GAP",
            "The event sequence is ahead of Master.",
            {"expected_sequence": 2, "received_sequence": 4},
            "expected_sequence",
        ),
        (
            409,
            "STOCK_OWNERSHIP_CONFLICT",
            "The QR belongs to another franchise.",
            {
                "serial_number": "SRC-01-ITEM-000001",
                "owner_franchise_code": "SRC-01",
            },
            "owner_franchise_code",
        ),
        (
            412,
            "STALE_SEQUENCE",
            "The event sequence is behind Master.",
            {"last_sequence": 7, "received_sequence": 6},
            "last_sequence",
        ),
    ],
)
def test_master_conflicts_preserve_bounded_details_and_require_manual_retry(
    db_session,
    monkeypatch,
    status_code,
    error_code,
    message,
    details,
    detail_key,
):
    settings = _settings()
    monkeypatch.setattr(master_sync, "get_settings", lambda: settings)
    _initialize_empty_node(db_session)
    event = master_sync.enqueue_outbox_event(
        db_session,
        event_type="HEARTBEAT",
        aggregate_type="NODE",
        aggregate_id=f"conflict-{error_code}",
        payload={
            "reference": f"conflict-{error_code}",
            "actor": "test",
            "items": [],
        },
    )
    db_session.commit()
    body = json.dumps(
        {
            "data": None,
            "error": {
                "code": error_code,
                "message": message,
                "details": {
                    **details,
                    "credential": settings.master_api_key,
                },
            },
            "request_id": str(uuid4()),
        }
    ).encode()
    calls = []

    def reject(request, **_kwargs):
        calls.append(request)
        raise HTTPError(
            request.full_url,
            status_code,
            "conflict",
            {},
            BytesIO(body),
        )

    assert master_sync.push_pending_events(db_session, opener=reject) == 0
    db_session.refresh(event)
    assert len(calls) == 1
    assert event.status == MasterOutboxStatus.FAILED.value
    assert event.next_attempt_at is None
    assert f"Master HTTP {status_code} {error_code}" in event.last_error
    assert message in event.last_error
    assert detail_key in event.last_error
    assert settings.master_api_key not in event.last_error
    assert "[redacted]" in event.last_error
    assert len(event.last_error) <= 1000

    assert master_sync.push_pending_events(
        db_session,
        opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("manual conflict must not auto-retry")
        ),
    ) == 0


def test_documented_concurrent_event_conflict_is_retryable(
    db_session,
    monkeypatch,
):
    settings = _settings()
    monkeypatch.setattr(master_sync, "get_settings", lambda: settings)
    _initialize_empty_node(db_session)
    event = master_sync.enqueue_outbox_event(
        db_session,
        event_type="HEARTBEAT",
        aggregate_type="NODE",
        aggregate_id="concurrent-conflict",
        payload={
            "reference": "concurrent-conflict",
            "actor": "test",
            "items": [],
        },
    )
    db_session.commit()
    body = json.dumps(
        {
            "error": {
                "code": "CONCURRENT_EVENT_CONFLICT",
                "message": "The node sequence changed concurrently; retry.",
            }
        }
    ).encode()

    def reject(request, **_kwargs):
        raise HTTPError(request.full_url, 409, "conflict", {}, BytesIO(body))

    assert master_sync.push_pending_events(db_session, opener=reject) == 0
    db_session.refresh(event)
    assert event.status == MasterOutboxStatus.FAILED.value
    assert event.next_attempt_at is not None
    assert "CONCURRENT_EVENT_CONFLICT" in event.last_error


def test_admin_can_explicitly_requeue_a_terminal_conflict(
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(master_sync, "get_settings", lambda: _settings())
    _initialize_empty_node(db_session)
    user = User(username="retry-admin", password_hash="x", role="admin")
    db_session.add(user)
    event = master_sync.enqueue_outbox_event(
        db_session,
        event_type="HEARTBEAT",
        aggregate_type="NODE",
        aggregate_id="manual-conflict-retry",
        payload={
            "reference": "manual-conflict-retry",
            "actor": user.username,
            "items": [],
        },
    )
    event.status = MasterOutboxStatus.FAILED.value
    event.last_error = "Master HTTP 409 SEQUENCE_GAP."
    event.next_attempt_at = None
    db_session.commit()

    response = lite_sync_router.retry_failed_event(
        _request(
            user.id,
            f"/master-connection/events/{event.id}/retry",
            app_mode="lite",
        ),
        event.id,
        db_session,
    )

    assert response.status_code == 303
    db_session.refresh(event)
    assert event.status == MasterOutboxStatus.PENDING.value
    assert event.next_attempt_at is None
    assert event.last_error is None


def test_oversized_master_error_body_is_never_exposed_or_auto_retried(
    db_session,
    monkeypatch,
):
    settings = _settings()
    monkeypatch.setattr(master_sync, "get_settings", lambda: settings)
    _initialize_empty_node(db_session)
    event = master_sync.enqueue_outbox_event(
        db_session,
        event_type="HEARTBEAT",
        aggregate_type="NODE",
        aggregate_id="bounded-error",
        payload={"reference": "bounded-error", "actor": "test", "items": []},
    )
    db_session.commit()
    body = json.dumps(
        {
            "error": {
                "code": "STOCK_CONFLICT",
                "message": settings.master_api_key + ("x" * 100_000),
            }
        }
    ).encode()

    def reject(request, **_kwargs):
        raise HTTPError(request.full_url, 409, "conflict", {}, BytesIO(body))

    assert master_sync.push_pending_events(db_session, opener=reject) == 0
    db_session.refresh(event)
    assert event.next_attempt_at is None
    assert event.last_error == "Master returned HTTP 409."
    assert settings.master_api_key not in event.last_error
    assert "x" * 100 not in event.last_error


def test_batch_delivery_updates_exact_originating_batch_status(
    db_session,
    monkeypatch,
):
    settings = _settings()
    monkeypatch.setattr(master_sync, "get_settings", lambda: settings)
    _initialize_empty_node(db_session)
    user, _product_row, _serial, batch = _sale_batch(
        db_session,
        code="ACK-PROD",
    )
    apply_batch_statuses(db_session, batch, user)
    batch.status = BatchStatus.PENDING_SYNC.value
    event = master_sync.enqueue_batch_submitted_event(
        db_session,
        batch,
        user=user,
    )
    db_session.commit()
    assert event.batch_id == batch.id

    def offline(_request, **_kwargs):
        raise URLError("temporary outage")

    assert master_sync.push_pending_events(db_session, opener=offline) == 0
    db_session.refresh(batch)
    db_session.refresh(event)
    assert batch.status == BatchStatus.FAILED.value
    assert batch.last_error == event.last_error

    event.next_attempt_at = utc_now() - timedelta(seconds=1)
    db_session.commit()
    assert master_sync.push_pending_events(
        db_session,
        opener=lambda request, **_kwargs: _accepted_response(request),
    ) == 1
    db_session.refresh(batch)
    assert batch.status == BatchStatus.SYNCED.value
    assert batch.synced_at is not None
    assert batch.last_error is None


def test_command_poll_accepts_master_response_wrapper_and_uses_exact_ack_body(
    db_session,
    monkeypatch,
):
    settings = _settings(franchise_code="DEST-01")
    monkeypatch.setattr(master_sync, "get_settings", lambda: settings)
    _initialize_empty_node(db_session)
    command_id = str(uuid4())
    transfer_id = str(uuid4())
    command = {
        "command_id": command_id,
        "type": "TRANSFER_AVAILABLE",
        "payload": {
            "transfer": {
                "transfer_uuid": transfer_id,
                "source_franchise_code": "SOURCE-01",
                "destination_franchise_code": "DEST-01",
                "status": "DISPATCHED",
            },
            "items": [
                {
                    "manifest_serial_number": "SOURCE-01-CMD-000001",
                    "product": {
                        "product_code": "CMD",
                        "product_name": "Command Product",
                        "hsn": "2106",
                        "gst_rate": 5,
                        "unit": "Pcs",
                        "default_rate": 10,
                        "tally_stock_item_name": "Command Product",
                    },
                    "serial": {
                        "serial_number": "SOURCE-01-CMD-000001",
                        "status": "IN_STOCK",
                    },
                }
            ],
        },
    }
    calls = []

    def opener(request, **_kwargs):
        calls.append(request)
        if request.get_method() == "GET":
            return _Response(
                json.dumps(
                    {
                        "data": {"commands": [command]},
                        "error": None,
                        "request_id": str(uuid4()),
                    }
                ).encode(),
                status=200,
            )
        assert request.get_method() == "PATCH"
        assert json.loads(request.data) == {"acknowledged": True}
        assert request.full_url.endswith(f"/api/v1/commands/{command_id}")
        return _Response(status=200)

    assert master_sync.poll_master_commands(db_session, opener=opener) == 1
    assert [request.get_method() for request in calls] == ["GET", "PATCH"]
    assert db_session.scalar(select(func.count(MasterInboxCommand.id))) == 1
    assert db_session.scalar(select(func.count(LocalTransfer.id))) == 1


def test_lite_serials_are_globally_namespaced_and_misconfiguration_is_clear(
    db_session,
    monkeypatch,
):
    product = _product("QR-PROD")
    db_session.add(product)
    db_session.commit()
    monkeypatch.setattr(
        inventory_service,
        "get_settings",
        lambda: _settings(franchise_code="Mysuru 07"),
    )

    serial = generate_serials(db_session, product, 1)[0]
    assert serial.serial_number == "MYSURU-07-QR-PROD-000001"

    monkeypatch.setattr(
        inventory_service,
        "get_settings",
        lambda: _settings(franchise_code="change-me"),
    )
    with pytest.raises(InventoryError, match="FRANCHISE_CODE"):
        generate_serials(db_session, product, 1)

    monkeypatch.setattr(
        inventory_service,
        "get_settings",
        lambda: _settings(franchise_code="", master_sync_enabled=False),
    )
    with pytest.raises(InventoryError, match="FRANCHISE_CODE"):
        generate_serials(db_session, product, 1)


def test_empty_initialization_queues_heartbeat_before_qr_assignment_snapshot(
    db_session,
    monkeypatch,
):
    settings = _settings()
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(master_sync, "get_settings", lambda: settings)
    monkeypatch.setattr(inventory_service, "get_settings", lambda: settings)
    event_count, item_count = master_sync.enqueue_initial_inventory_snapshot(
        db_session,
        actor=SimpleNamespace(username="empty-node-admin"),
    )
    assert (event_count, item_count) == (1, 0)
    marker = db_session.scalar(
        select(MasterOutboxEvent).where(
            MasterOutboxEvent.aggregate_type
            == master_sync.INITIAL_INVENTORY_AGGREGATE_TYPE
        )
    )
    marker_payload = json.loads(marker.payload_json)["events"][0]
    assert marker.event_type == "HEARTBEAT"
    assert marker.aggregate_id == "BLR-01:INITIAL-INVENTORY:EMPTY"
    assert marker_payload["items"] == []
    assert marker_payload["reason_code"] == "INITIAL_ENROLLMENT"

    user = User(username="assignment-admin", password_hash="x", role="admin")
    product = _product("ASSIGN-PROD")
    db_session.add_all([user, product])
    db_session.commit()
    batch = assign_barcodes_to_existing_stock(
        db_session,
        user,
        [AssignmentLine(product=product, quantity=1)],
        notes="Existing stock assignment",
    )
    serial = batch.items[0].serial
    event = db_session.scalar(
        select(MasterOutboxEvent).where(
            MasterOutboxEvent.aggregate_id.like(f"%:{batch.batch_number}")
        )
    )

    wire_event = json.loads(event.payload_json)["events"][0]
    assert wire_event["type"] == "STOCK_SNAPSHOT"
    assert wire_event["reference"] == batch.batch_number
    assert wire_event["actor"] == user.username
    assert wire_event["items"][0]["serial_number"] == serial.serial_number
    assert wire_event["items"][0]["status"] == SerialStatus.IN_STOCK.value
    assert event.id > marker.id


def test_initial_inventory_partitions_by_exact_utf8_body_size_with_multibyte_text(
    db_session,
    monkeypatch,
):
    settings = _settings(franchise_code="BYTE-01", master_sync_enabled=False)
    monkeypatch.setattr(master_sync, "get_settings", lambda: settings)
    actor = SimpleNamespace(username="管理者" * 30)
    product = _product("BYTE-PROD")
    product.product_name = "界" * 180
    product.tally_stock_item_name = "薬" * 180
    product.hsn = "1" * 40
    product.unit = "件" * 40
    serials = [
        Serial(
            serial_number=f"BYTE-01-BYTE-PROD-{index:06d}",
            product=product,
            status=SerialStatus.IN_STOCK.value,
            active=True,
            product_batch_number="批次" * 40,
            warehouse="倉庫" * 40,
        )
        for index in range(7)
    ]
    db_session.add_all([product, *serials])
    db_session.commit()

    items = [master_sync.network_event_item(serial) for serial in serials]
    item_sizes = [
        master_sync._utf8_size(master_sync.canonical_json(item))
        for item in items
    ]
    reference = master_sync._initial_inventory_reference("BYTE-01", 1)
    probe_time = utc_now()
    two_item_size = master_sync._conservative_initial_inventory_body_size(
        reference=reference,
        actor=actor,
        occurred_at=probe_time,
        item_json_sizes=item_sizes[:2],
    )
    three_item_size = master_sync._conservative_initial_inventory_body_size(
        reference=reference,
        actor=actor,
        occurred_at=probe_time,
        item_json_sizes=item_sizes[:3],
    )
    body_limit = two_item_size + max(1, (three_item_size - two_item_size) // 2)
    assert two_item_size <= body_limit < three_item_size
    monkeypatch.setattr(
        master_sync,
        "MASTER_EVENT_MAX_BODY_BYTES",
        body_limit,
    )

    event_count, item_count = master_sync.enqueue_initial_inventory_snapshot(
        db_session,
        actor=actor,
    )

    assert (event_count, item_count) == (4, 7)
    events = db_session.scalars(
        select(MasterOutboxEvent).order_by(MasterOutboxEvent.id)
    ).all()
    payloads = [json.loads(event.payload_json)["events"][0] for event in events]
    assert [len(payload["items"]) for payload in payloads] == [2, 2, 2, 1]
    assert [event.sequence for event in events] == sorted(
        event.sequence for event in events
    )
    assert all(
        len(event.payload_json.encode("utf-8")) <= body_limit
        for event in events
    )
    assert all(
        len(payload["items"]) <= master_sync.MASTER_EVENT_MAX_ITEMS
        for payload in payloads
    )
    assert any(
        len(event.payload_json.encode("utf-8")) > len(event.payload_json)
        for event in events
    )
    assert [
        item["serial_number"]
        for payload in payloads
        for item in payload["items"]
    ] == [serial.serial_number for serial in serials]


def test_initial_inventory_size_failure_leaves_no_partial_marker(
    db_session,
    monkeypatch,
):
    settings = _settings(franchise_code="BYTE-FAIL", master_sync_enabled=False)
    monkeypatch.setattr(master_sync, "get_settings", lambda: settings)
    product = _product("BYTE-FAIL-PROD")
    product.product_name = "界" * 180
    product.tally_stock_item_name = "薬" * 180
    serial = Serial(
        serial_number="BYTE-FAIL-BYTE-FAIL-PROD-000001",
        product=product,
        status=SerialStatus.IN_STOCK.value,
        active=True,
    )
    db_session.add_all([product, serial])
    db_session.commit()
    monkeypatch.setattr(master_sync, "MASTER_EVENT_MAX_BODY_BYTES", 256)

    with pytest.raises(master_sync.MasterSyncError, match="One initial-inventory"):
        master_sync.enqueue_initial_inventory_snapshot(
            db_session,
            actor=SimpleNamespace(username="size-test"),
        )

    assert db_session.scalar(select(func.count(MasterOutboxEvent.id))) == 0


def test_initial_inventory_snapshot_must_be_the_first_outbox_event(
    db_session,
    monkeypatch,
):
    settings = _settings(franchise_code="BOOT-01", master_sync_enabled=False)
    monkeypatch.setattr(master_sync, "get_settings", lambda: settings)
    user = User(username="bootstrap-admin", password_hash="x", role="admin")
    product = _product("BOOT-PROD")
    db_session.add_all([user, product])
    db_session.flush()
    db_session.add_all(
        [
            Serial(
                serial_number="BOOT-01-BOOT-PROD-000001",
                product_id=product.id,
                status=SerialStatus.IN_STOCK.value,
                active=True,
            ),
            Serial(
                serial_number="BOOT-01-BOOT-PROD-000002",
                product_id=product.id,
                status=SerialStatus.GENERATED.value,
                active=True,
            ),
            Serial(
                serial_number="BOOT-01-BOOT-PROD-000003",
                product_id=product.id,
                status=SerialStatus.SOLD.value,
                active=True,
            ),
        ]
    )
    db_session.commit()

    event_count, item_count = master_sync.enqueue_initial_inventory_snapshot(
        db_session,
        actor=user,
    )

    assert (event_count, item_count) == (1, 2)
    outbox = db_session.scalar(select(MasterOutboxEvent))
    wire_event = json.loads(outbox.payload_json)["events"][0]
    assert wire_event["type"] == "STOCK_SNAPSHOT"
    assert wire_event["reason_code"] == "INITIAL_ENROLLMENT"
    assert {item["status"] for item in wire_event["items"]} == {
        SerialStatus.GENERATED.value,
        SerialStatus.IN_STOCK.value,
    }
    assert master_sync.initial_inventory_is_queued(db_session)
    ordinary = master_sync.enqueue_outbox_event(
        db_session,
        event_type="HEARTBEAT",
        aggregate_type="NODE",
        aggregate_id="after-initial-inventory",
        payload={
            "reference": "after-initial-inventory",
            "actor": user.username,
            "items": [],
        },
    )
    assert ordinary.id > outbox.id
    with pytest.raises(master_sync.MasterSyncError, match="before the first"):
        master_sync.enqueue_initial_inventory_snapshot(db_session, actor=user)


@pytest.mark.parametrize(
    ("franchise_code", "master_sync_enabled", "error_pattern"),
    [
        ("", False, "FRANCHISE_CODE"),
        ("CUTOVER-01", False, "Initialize inventory"),
    ],
)
def test_lite_submit_fails_closed_before_initialization_and_rolls_back_stock(
    db_session,
    monkeypatch,
    franchise_code,
    master_sync_enabled,
    error_pattern,
):
    settings = _settings(
        franchise_code=franchise_code,
        master_sync_enabled=master_sync_enabled,
    )
    monkeypatch.setattr(master_sync, "get_settings", lambda: settings)
    user, _product_row, serial, batch = _sale_batch(
        db_session,
        code="CUTOVER-PROD",
    )

    response = batches_router.submit_batch(
        _request(
            user.id,
            f"/batches/{batch.id}/submit",
            app_mode="lite",
        ),
        batch.id,
        db_session,
    )

    assert response.status_code == 400
    assert error_pattern.encode() in response.body
    db_session.refresh(batch)
    db_session.refresh(serial)
    assert batch.status == BatchStatus.DRAFT.value
    assert serial.status == SerialStatus.IN_STOCK.value
    assert db_session.scalar(select(func.count(MasterOutboxEvent.id))) == 0


def test_permanent_franchise_code_change_blocks_enqueue_and_transport(
    db_session,
    monkeypatch,
):
    original_settings = _settings(franchise_code="PERMANENT-01")
    monkeypatch.setattr(master_sync, "get_settings", lambda: original_settings)
    marker = _initialize_empty_node(db_session)
    pending = master_sync.enqueue_outbox_event(
        db_session,
        event_type="HEARTBEAT",
        aggregate_type="NODE",
        aggregate_id="queued-under-original-code",
        payload={
            "reference": "queued-under-original-code",
            "actor": "test",
            "items": [],
        },
    )
    db_session.commit()

    changed_settings = _settings(franchise_code="PERMANENT-02")
    monkeypatch.setattr(master_sync, "get_settings", lambda: changed_settings)
    calls = []

    with pytest.raises(master_sync.MasterSyncError, match="PERMANENT-02"):
        master_sync.push_pending_events(
            db_session,
            opener=lambda request, **_kwargs: calls.append(request),
        )
    with pytest.raises(master_sync.MasterSyncError, match="PERMANENT-02"):
        master_sync.enqueue_outbox_event(
            db_session,
            event_type="HEARTBEAT",
            aggregate_type="NODE",
            aggregate_id="wrong-code",
            payload={"reference": "wrong-code", "actor": "test", "items": []},
        )

    assert calls == []
    db_session.refresh(marker)
    db_session.refresh(pending)
    assert marker.status == MasterOutboxStatus.SENT.value
    assert pending.status == MasterOutboxStatus.PENDING.value
    assert db_session.scalar(select(func.count(MasterOutboxEvent.id))) == 2


def test_submit_route_uses_direct_tally_only_in_legacy_mode(
    db_session,
    monkeypatch,
):
    sync_calls: list[str] = []
    monkeypatch.setattr(
        batches_router,
        "sync_batch",
        lambda _db, batch: sync_calls.append(batch.batch_number),
    )

    legacy_user, _product, _serial, legacy_batch = _sale_batch(
        db_session,
        code="LEGACY-PROD",
    )
    response = batches_router.submit_batch(
        _request(legacy_user.id, f"/batches/{legacy_batch.id}/submit"),
        legacy_batch.id,
        db_session,
    )
    assert response.status_code == 303
    assert sync_calls == [legacy_batch.batch_number]

    lite_settings = _settings(app_mode="lite")
    monkeypatch.setattr(master_sync, "get_settings", lambda: lite_settings)
    _initialize_empty_node(db_session)
    lite_user, _product, _serial, lite_batch = _sale_batch(
        db_session,
        code="LITE-PROD",
    )
    response = batches_router.submit_batch(
        _request(
            lite_user.id,
            f"/batches/{lite_batch.id}/submit",
            app_mode="lite",
        ),
        lite_batch.id,
        db_session,
    )

    assert response.status_code == 303
    assert sync_calls == [legacy_batch.batch_number]
    db_session.refresh(lite_batch)
    assert lite_batch.status == BatchStatus.PENDING_SYNC.value
    event = db_session.scalar(
        select(MasterOutboxEvent).where(
            MasterOutboxEvent.aggregate_id.like(f"%:{lite_batch.batch_number}")
        )
    )
    assert event is not None
