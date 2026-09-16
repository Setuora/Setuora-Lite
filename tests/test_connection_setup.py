import json
from datetime import timedelta
from types import SimpleNamespace
from urllib.error import URLError
from urllib.parse import unquote
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from starlette.requests import Request

from app import config as config_module
from app.models import MasterOutboxEvent, MasterOutboxStatus, User, utc_now
from app.routers import lite_sync
from app.security import create_session_token
from app.services import master_sync


KEY = "setuora-node.connection_test." + "s" * 40
ORIGIN = "https://warehouse.example.com"


def _request(user, path="/master-connection/connect"):
    return Request({
        "type": "http", "method": "POST", "path": path,
        "headers": [(b"cookie", f"setuora_session={create_session_token(user.id)}".encode())],
        "query_string": b"", "server": ("testserver", 80), "scheme": "http",
        "app": SimpleNamespace(state=SimpleNamespace(app_mode="lite")),
    })


class Response:
    status = 200

    def __init__(self, data):
        self.body = json.dumps({"data": data, "error": None}).encode()

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]

    def close(self):
        pass


class Master:
    def __init__(self):
        self.code = "BLR-01"
        self.node_id = str(uuid4())
        self.sequence = 0
        self.key = KEY
        self.fail_delivery = False
        self.requests = []

    def open(self, request, *, timeout):
        self.requests.append(request)
        assert request.full_url.startswith(ORIGIN + "/")
        assert request.get_header("Authorization") == f"Bearer {self.key}"
        if request.full_url.endswith("/api/v1/node"):
            return Response({
                "public_id": self.node_id, "code": self.code,
                "last_sequence": self.sequence, "next_sequence": self.sequence + 1,
            })
        if self.fail_delivery:
            raise URLError("Master is temporarily offline")
        if request.get_method() == "POST":
            event = json.loads(request.data)["events"][0]
            self.sequence = event["sequence"]
            return Response({"acknowledgements": [{
                "event_id": event["event_id"], "sequence": self.sequence,
            }], "last_sequence": self.sequence})
        return Response({"commands": []})


@pytest.fixture()
def setup_connection(db_session, monkeypatch):
    settings = SimpleNamespace(
        app_mode="lite", franchise_code="", master_url="", master_api_key="",
        master_sync_enabled=False, master_sync_interval_seconds=30,
        master_request_timeout_seconds=15, master_sync_configuration_error=None,
    )
    for module in (config_module, lite_sync, master_sync):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    user = User(username="connection-admin", password_hash="x", role="admin")
    db_session.add(user)
    db_session.commit()
    remote = Master()
    saved = []

    def save(values):
        saved.append(dict(values))
        settings.franchise_code = values["FRANCHISE_CODE"]
        settings.master_url = values["MASTER_URL"]
        settings.master_api_key = values["MASTER_API_KEY"]
        settings.master_sync_enabled = values["MASTER_SYNC_ENABLED"] == "true"

    monkeypatch.setattr(lite_sync, "save_master_connection_settings", save)
    monkeypatch.setattr(master_sync, "_master_urlopen", remote.open)
    bundle = {
        "format": "setuora-lite-connection", "version": 1,
        "master_url": ORIGIN, "franchise_code": "BLR-01", "node_credential": KEY,
    }
    return SimpleNamespace(settings=settings, remote=remote, saved=saved, user=user, bundle=bundle)


def _connect(db, setup, bundle=None):
    return lite_sync.connect_to_master(
        _request(setup.user), setup_details=json.dumps(bundle if bundle is not None else setup.bundle), db=db
    )


def test_one_click_verifies_initializes_syncs_and_reconnect_is_idempotent(db_session, setup_connection):
    setup = setup_connection
    for _ in range(2):
        response = _connect(db_session, setup)
        assert "message=" in response.headers["location"]
        assert "Connected" in response.headers["location"]
        assert KEY not in unquote(response.headers["location"])
    assert db_session.scalar(select(func.count(MasterOutboxEvent.id))) == 1
    assert db_session.scalar(select(MasterOutboxEvent)).status == MasterOutboxStatus.SENT.value
    assert lite_sync._saved_connection_value(db_session, lite_sync.CONNECTION_NODE_KEY) == setup.remote.node_id
    assert [request.get_method() for request in setup.remote.requests] == ["GET", "POST", "GET", "GET", "GET"]


@pytest.mark.parametrize("change", [
    {"version": 2}, {"version": True}, {"format": "unknown"}, {"extra": "value"},
    {"franchise_code": []}, {"master_url": "http://warehouse.example.com"},
    {"master_url": "https://warehouse.example.com/path"}, {"node_credential": "invalid"},
])
def test_bad_setup_details_never_save_or_contact_master(db_session, setup_connection, change):
    setup = setup_connection
    response = _connect(db_session, setup, {**setup.bundle, **change})
    assert "error=" in response.headers["location"]
    assert KEY not in unquote(response.headers["location"])
    assert setup.saved == []
    assert setup.remote.requests == []
    assert db_session.scalar(select(func.count(MasterOutboxEvent.id))) == 0


@pytest.mark.parametrize("failure", ["wrong-franchise", "used-node", "wrong-node-id"])
def test_remote_identity_failure_does_not_replace_connection(db_session, setup_connection, failure):
    setup = setup_connection
    if failure == "wrong-franchise":
        setup.remote.code = "OTHER-01"
    elif failure == "used-node":
        setup.remote.sequence = 50
    else:
        setup.remote.node_id = "not-a-node-id"
    response = _connect(db_session, setup)
    assert "error=" in response.headers["location"]
    assert setup.saved == []
    assert setup.settings.master_api_key == ""
    assert db_session.scalar(select(func.count(MasterOutboxEvent.id))) == 0


def test_verified_connection_survives_failed_first_delivery_and_retries_once(db_session, setup_connection):
    setup = setup_connection
    setup.remote.fail_delivery = True
    response = _connect(db_session, setup)
    assert "notice=" in response.headers["location"]
    row = db_session.scalar(select(MasterOutboxEvent))
    assert row.status == MasterOutboxStatus.FAILED.value
    assert len(setup.saved) == 1
    assert lite_sync._saved_connection_value(db_session, lite_sync.CONNECTION_RESULT_KEY) == "pending"
    row.next_attempt_at = utc_now() - timedelta(seconds=1)
    db_session.commit()
    setup.remote.fail_delivery = False
    response = _connect(db_session, setup)
    assert "message=" in response.headers["location"]
    assert db_session.scalar(select(func.count(MasterOutboxEvent.id))) == 1
    assert row.status == MasterOutboxStatus.SENT.value


@pytest.mark.parametrize("change", [
    {"franchise_code": "OTHER-01"}, {"master_url": "https://different.example.com"},
])
def test_initialized_connection_cannot_be_reassigned(db_session, setup_connection, change):
    setup = setup_connection
    _connect(db_session, setup)
    setup.remote.requests.clear()
    response = _connect(db_session, setup, {**setup.bundle, **change})
    assert "error=" in response.headers["location"]
    assert len(setup.saved) == 1
    assert setup.remote.requests == []


def test_new_key_can_rotate_only_for_original_master_node(db_session, setup_connection):
    setup = setup_connection
    _connect(db_session, setup)
    next_key = "setuora-node.new_key." + "n" * 40
    setup.remote.key = next_key
    response = _connect(db_session, setup, {**setup.bundle, "node_credential": next_key})
    assert "message=" in response.headers["location"]
    assert setup.settings.master_api_key == next_key
    setup.remote.node_id = str(uuid4())
    response = _connect(db_session, setup, {**setup.bundle, "node_credential": next_key})
    assert "error=" in response.headers["location"]
    assert len(setup.saved) == 2


def test_manual_setup_cannot_send_saved_credential_to_changed_origin(db_session, setup_connection):
    setup = setup_connection
    setup.settings.master_url = ORIGIN
    setup.settings.master_api_key = KEY
    response = lite_sync.connect_to_master(
        _request(setup.user), franchise_code="BLR-01", master_url="https://other.example.com", db=db_session
    )
    assert "error=" in response.headers["location"]
    assert setup.saved == []
    assert setup.remote.requests == []


def test_connection_errors_never_echo_credentials(db_session, setup_connection, monkeypatch):
    setup = setup_connection

    def fail(**kwargs):
        raise master_sync.MasterSyncError(f"Failed key {KEY}")

    monkeypatch.setattr(lite_sync, "verify_master_enrollment_identity", fail)
    response = _connect(db_session, setup)
    assert KEY not in unquote(response.headers["location"])
    assert setup.saved == []


def test_non_admin_cannot_connect(db_session, setup_connection):
    setup = setup_connection
    setup.user.role = "sales"
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        _connect(db_session, setup)
    assert exc.value.status_code == 403
    assert setup.saved == []
    assert setup.remote.requests == []


def test_master_redirect_is_rejected_before_credential_can_be_forwarded():
    handler = master_sync._RejectMasterRedirects()
    with pytest.raises(master_sync.MasterSyncError, match="redirected"):
        handler.redirect_request(None, None, 302, "Found", {}, "http://other.example.com")


def test_connection_page_guides_paste_flow_and_never_displays_stored_secret(db_session, setup_connection):
    setup = setup_connection
    _connect(db_session, setup)
    response = lite_sync.master_connection_page(_request(setup.user, "/master-connection"), db_session)
    html = response.body.decode()
    assert 'name="setup_details"' in html
    assert "Connected to Master" in html
    assert "Initialize inventory" not in html
    assert KEY not in html
    assert "Advanced connection options" in html


def test_background_delivery_does_not_leave_stale_waiting_label(db_session, setup_connection):
    setup = setup_connection
    setup.remote.fail_delivery = True
    _connect(db_session, setup)
    row = db_session.scalar(select(MasterOutboxEvent))
    row.next_attempt_at = utc_now() - timedelta(seconds=1)
    db_session.commit()
    setup.remote.fail_delivery = False
    assert master_sync.push_pending_events(db_session) == 1
    response = lite_sync.master_connection_page(_request(setup.user, "/master-connection"), db_session)
    assert "Waiting to sync" not in response.body.decode()
    assert "Connection configured" in response.body.decode()
