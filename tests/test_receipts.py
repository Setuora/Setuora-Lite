import json
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import (
    MasterInboxStatus,
    MasterOutboxEvent,
    MasterOutboxStatus,
    Receipt,
    ReceiptStatus,
    User,
)
from app.services import master_sync
from app.services.receipts import ReceiptError, create_receipt


def _settings():
    return SimpleNamespace(
        app_mode="lite",
        franchise_code="RCPT-01",
        master_sync_enabled=True,
        master_url="https://master.example",
        master_api_key="secret-node-key",
        master_request_timeout_seconds=3,
    )


def _initialize_sync(db, monkeypatch, *, transport_enabled=True):
    settings = _settings()
    settings.master_sync_enabled = transport_enabled
    monkeypatch.setattr(master_sync, "get_settings", lambda: settings)
    master_sync.enqueue_initial_inventory_snapshot(
        db, actor=SimpleNamespace(username="initializer")
    )
    marker = db.scalar(
        select(MasterOutboxEvent).where(
            MasterOutboxEvent.aggregate_type
            == master_sync.INITIAL_INVENTORY_AGGREGATE_TYPE
        )
    )
    marker.status = MasterOutboxStatus.SENT.value
    db.commit()
    return settings


@pytest.mark.parametrize("transport_enabled", [True, False])
def test_receipt_is_queued_and_master_decision_is_applied(
    db_session, monkeypatch, transport_enabled
):
    settings = _initialize_sync(db_session, monkeypatch, transport_enabled=transport_enabled)
    user = User(username="sales-user", password_hash="x", role="sales")
    db_session.add(user)
    db_session.commit()
    proof = b"\x89PNG\r\n\x1a\n" + b"proof-data"

    receipt = create_receipt(
        db_session,
        user=user,
        receipt_date=date(2026, 8, 17),
        proof_image=proof,
        proof_content_type="image/png",
        utr_number=" utr 123 ",
    )
    outbox = db_session.scalar(
        select(MasterOutboxEvent).where(MasterOutboxEvent.aggregate_type == "RECEIPT")
    )
    wire_event = json.loads(outbox.payload_json)["events"][0]
    assert receipt.status == ReceiptStatus.PENDING.value
    assert receipt.utr_number == "UTR123"
    assert wire_event["type"] == "RECEIPT_SUBMITTED"
    assert wire_event["receipt_id"] == receipt.receipt_uuid
    assert wire_event["proof_content_type"] == "image/png"

    class AcceptedResponse:
        status = 200

        def __init__(self, body):
            self.body = body

        def read(self, size=-1):
            return self.body if size < 0 else self.body[:size]

        def close(self):
            pass

    def accept(request, **_kwargs):
        submitted = json.loads(request.data)["events"][0]
        return AcceptedResponse(
            json.dumps(
                {
                    "data": {
                        "acknowledgements": [
                            {
                                "event_id": submitted["event_id"],
                                "sequence": submitted["sequence"],
                                "status": "ACCEPTED",
                            }
                        ],
                        "last_sequence": submitted["sequence"],
                    },
                    "error": None,
                    "request_id": str(uuid4()),
                }
            ).encode()
        )

    settings.master_sync_enabled = True
    assert master_sync.push_pending_events(db_session, opener=accept) == 1
    db_session.refresh(receipt)
    assert receipt.synced_at is not None

    command = master_sync.apply_master_command(
        db_session,
        {
            "command_id": "receipt-review-1",
            "command_type": "RECEIPT_REVIEWED",
            "schema_version": 1,
            "payload": {
                "receipt_id": receipt.receipt_uuid,
                "status": "DENIED",
                "rejection_remarks": "UTR is missing from the image",
                "reviewed_by": "master-admin",
                "reviewed_at": "2026-08-17T12:00:00+00:00",
            },
        },
    )
    db_session.commit()
    db_session.refresh(receipt)
    assert command.status == MasterInboxStatus.APPLIED.value
    assert receipt.status == ReceiptStatus.DENIED.value
    assert receipt.rejection_remarks == "UTR is missing from the image"
    assert receipt.reviewed_by == "master-admin"


def test_receipt_rejects_non_image_proof(db_session, monkeypatch):
    _initialize_sync(db_session, monkeypatch)
    user = User(username="sales-user", password_hash="x", role="sales")
    db_session.add(user)
    db_session.commit()

    with pytest.raises(ReceiptError, match="JPEG, PNG, or WebP"):
        create_receipt(
            db_session,
            user=user,
            receipt_date=date(2026, 8, 17),
            proof_image=b"not-an-image",
            proof_content_type="image/png",
        )

    assert db_session.scalar(select(Receipt)) is None


def test_receipt_without_initialization_rolls_back_proof_and_outbox(db_session, monkeypatch):
    settings = _settings()
    settings.master_sync_enabled = False
    monkeypatch.setattr(master_sync, "get_settings", lambda: settings)
    user = User(username="uninitialized-user", password_hash="x", role="sales")
    db_session.add(user)
    db_session.commit()

    with pytest.raises(ReceiptError, match="Initialize inventory"):
        create_receipt(
            db_session,
            user=user,
            receipt_date=date(2026, 8, 17),
            proof_image=b"\x89PNG\r\n\x1a\nproof",
            proof_content_type="image/png",
        )
    db_session.commit()
    assert db_session.scalar(select(Receipt)) is None
    assert db_session.scalar(select(MasterOutboxEvent)) is None
