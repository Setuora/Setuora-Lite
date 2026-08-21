import asyncio
import stat
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import Settings, get_settings, save_master_connection_settings
from app.main import create_app
from app.routers import maintenance


def application_paths(app) -> set[str]:
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    for included in app.routes:
        router = getattr(included, "original_router", None)
        if router is not None:
            paths.update(
                route.path for route in router.routes if hasattr(route, "path")
            )
    return paths


def test_lite_composition_owns_stock_tally_and_sftp_configuration():
    app = create_app("lite")
    paths = application_paths(app)

    assert "/batches" in paths
    assert "/barcode-assignment" in paths
    assert "/products" in paths
    assert "/serials" in paths
    assert "/transfers" in paths
    assert "/receipts" in paths
    assert "/master-connection" in paths
    assert "/settings/access" in paths

    assert "/settings" in paths
    assert "/tally-check" in paths
    assert "/api/v1/events" not in paths
    assert "/docs" not in paths
    assert "/openapi.json" not in paths


def test_lite_artifact_cannot_enable_master_or_direct_tally_in_production(
    monkeypatch,
):
    with pytest.raises(RuntimeError, match="only supports lite mode"):
        create_app("master")

    monkeypatch.setattr(get_settings(), "allow_legacy_test_mode", False)
    with pytest.raises(RuntimeError, match="test-only"):
        create_app("legacy")


def test_lite_exposes_local_tally_batch_operations():
    app = create_app("lite")
    client = TestClient(app, raise_server_exceptions=False)
    try:
        response = client.get("/batches/1/tally.xml", follow_redirects=False)
    finally:
        client.close()

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_lite_maintenance_controls_master_sync_worker(monkeypatch):
    calls: list[str] = []

    async def stop_master(_app):
        calls.append("stop-master")

    async def stop_retry(_app):
        calls.append("stop-tally")

    async def stop_backup(_app):
        calls.append("stop-backup")

    monkeypatch.setattr(maintenance, "stop_master_sync_worker", stop_master)
    monkeypatch.setattr(maintenance, "stop_retry_worker", stop_retry)
    monkeypatch.setattr(maintenance, "stop_backup_worker", stop_backup)
    monkeypatch.setattr(
        maintenance,
        "start_master_sync_worker",
        lambda _app: calls.append("start-master"),
    )
    monkeypatch.setattr(
        maintenance,
        "start_retry_worker",
        lambda _app: calls.append("start-tally"),
    )
    monkeypatch.setattr(
        maintenance,
        "start_backup_worker",
        lambda _app: calls.append("start-backup"),
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(app_mode="lite"))
    )

    asyncio.run(maintenance._stop_maintenance_workers(request))
    maintenance._start_maintenance_workers(request)

    assert calls == [
        "stop-master",
        "stop-tally",
        "stop-backup",
        "start-master",
        "start-tally",
        "start-backup",
    ]


def test_connected_lite_disables_destructive_browser_recovery(monkeypatch):
    monkeypatch.setattr(
        maintenance,
        "get_settings",
        lambda: SimpleNamespace(
            sftp_sync_enabled=True,
            master_sync_enabled=False,
        ),
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(app_mode="lite"))
    )

    with pytest.raises(HTTPException) as exc_info:
        maintenance._deny_connected_lite_destructive_maintenance(request)

    assert exc_info.value.status_code == 409
    assert "SFTP exchange state" in exc_info.value.detail


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("FRANCHISE_CODE", "CHANGE-ME", "FRANCHISE_CODE"),
        ("FRANCHISE_CODE", "INVALID CODE", "FRANCHISE_CODE"),
        ("MASTER_URL", "http://master.example.com", "MASTER_URL"),
        ("MASTER_URL", "https://master.example.com/api", "MASTER_URL"),
        ("MASTER_API_KEY", "not-a-node-credential", "MASTER_API_KEY"),
        ("MASTER_TLS_VERIFY", "false", "MASTER_TLS_VERIFY"),
    ],
)
def test_runtime_master_configuration_fails_closed(
    monkeypatch,
    name,
    value,
    expected,
):
    monkeypatch.setenv("MASTER_SYNC_ENABLED", "true")
    monkeypatch.setenv("FRANCHISE_CODE", "SG-NORTH-01")
    monkeypatch.setenv("MASTER_URL", "https://setuora-master.example.com")
    monkeypatch.setenv(
        "MASTER_API_KEY",
        f"setuora-node.node_id.{'k' * 40}",
    )
    monkeypatch.setenv("MASTER_TLS_VERIFY", "true")
    monkeypatch.setenv(name, value)

    assert expected in (Settings().master_sync_configuration_error or "")


def test_runtime_master_configuration_accepts_exact_https_endpoint(monkeypatch):
    monkeypatch.setenv("MASTER_SYNC_ENABLED", "true")
    monkeypatch.setenv("FRANCHISE_CODE", "SG-NORTH-01")
    monkeypatch.setenv("MASTER_URL", "https://setuora-master.example.com")
    monkeypatch.setenv(
        "MASTER_API_KEY",
        f"setuora-node.node_id.{'k' * 40}",
    )
    monkeypatch.setenv("MASTER_TLS_VERIFY", "true")

    assert Settings().master_sync_configuration_error is None


def test_frontend_master_settings_override_environment_and_are_private(
    tmp_path,
    monkeypatch,
):
    settings_file = tmp_path / "master-connection.env"
    monkeypatch.setenv("MASTER_CONNECTION_SETTINGS_FILE", str(settings_file))
    monkeypatch.setenv("FRANCHISE_CODE", "ENV-01")
    monkeypatch.setenv("MASTER_SYNC_ENABLED", "false")
    monkeypatch.setenv("MASTER_URL", "https://environment.example.com")
    monkeypatch.setenv("MASTER_API_KEY", f"setuora-node.env.{'e' * 40}")

    try:
        save_master_connection_settings(
            {
                "FRANCHISE_CODE": "UI-01",
                "MASTER_SYNC_ENABLED": "true",
                "MASTER_URL": "https://frontend.example.com",
                "MASTER_API_KEY": f"setuora-node.ui.{'u' * 40}",
                "MASTER_SYNC_INTERVAL_SECONDS": "45",
                "MASTER_REQUEST_TIMEOUT_SECONDS": "12",
                "MASTER_TLS_VERIFY": "true",
            }
        )
        settings = get_settings()

        assert settings.franchise_code == "UI-01"
        assert settings.master_sync_enabled is True
        assert settings.master_url == "https://frontend.example.com"
        assert settings.master_api_key == f"setuora-node.ui.{'u' * 40}"
        assert settings.master_sync_interval_seconds == 45
        assert settings.master_request_timeout_seconds == 12
        assert stat.S_IMODE(settings_file.stat().st_mode) == 0o600
    finally:
        get_settings.cache_clear()
