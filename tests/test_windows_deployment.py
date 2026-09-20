from pathlib import Path

import deploy

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_active_deployment_is_windows_native_with_tailscale_setup():
    assert not (PROJECT_ROOT / "compose.yaml").exists()
    assert not (PROJECT_ROOT / "Dockerfile").exists()
    deployment = (PROJECT_ROOT / "deploy.py").read_text(encoding="utf-8").lower()
    assert "schtasks.exe" in deployment
    assert "remove-netfirewallrule" in deployment
    assert "new-netfirewallrule" not in deployment
    assert "docker" not in deployment
    controller = (PROJECT_ROOT / "client/windows/setuora.ps1").read_text(encoding="utf-8")
    assert "Initialize-SetuoraTailnet" in controller
    assert "--unattended=true" in controller
    assert "--accept-dns=true" in controller


def test_linux_and_old_private_network_assets_are_archived():
    assert (PROJECT_ROOT / "archive" / "linux" / "Linux — Setuora Lite.run").is_file()
    assert (PROJECT_ROOT / "archive" / "linux" / "container-deployment" / "compose.yaml").is_file()
    assert (
        PROJECT_ROOT
        / "archive"
        / "tailscale"
        / "docs"
        / "adr-002-lite-self-hosted-tailscale-egress.md"
    ).is_file()


def test_windows_preflight_accepts_native_configuration():
    issues = deploy._environment_issues(
        {
            "APP_SECRET_KEY": "x" * 48,
            "BOOTSTRAP_ADMIN_PASSWORD": "strong-admin-password",
            "SETUORA_APP_MODE": "lite",
            "SESSION_COOKIE_SECURE": "true",
            "TRUSTED_HOSTS": "127.0.0.1,localhost,franchise-server",
            "SETUORA_WEB_PORT": "8000",
            "AUTOMATIC_BACKUPS_ENABLED": "true",
            "BACKUP_RETENTION_COUNT": "14",
        },
        has_application_data=False,
    )
    assert issues == []


def test_environment_writer_preserves_secret_characters(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("# keep\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_PATH", env_path)
    monkeypatch.setattr(deploy, "_run", lambda *args, **kwargs: None)
    password = 'spaces # quote " slash \\ stay intact'
    deploy._write_env({"SFTP_PASSWORD": password})
    assert deploy._read_env()[1]["SFTP_PASSWORD"] == password
    assert "# keep" in env_path.read_text(encoding="utf-8")


def test_batch_and_packaged_controls_share_safe_interactive_elevation():
    controller = (PROJECT_ROOT / "setuora.bat").read_text(encoding="utf-8")
    launcher = (PROJECT_ROOT / "client/windows/setuora.ps1").read_text(encoding="utf-8")
    finder = (PROJECT_ROOT / "scripts/find_python.bat").read_text(encoding="utf-8")

    assert 'set "ROOT_DIR=%~dp0"' in controller
    assert "%ROOT_DIR%setuora.ps1" in controller
    assert "%ROOT_DIR%client\\windows\\setuora.ps1" in controller
    assert "endlocal & exit /b %EXIT_CODE%" in controller
    assert "set /p" not in controller.lower()
    assert "$selection = Read-Host" in launcher
    assert "switch ($selection)" in launcher
    assert "-Verb RunAs -Wait -PassThru" in launcher
    assert "$process.ExitCode" in launcher
    assert "SETUORA_ELEVATED_LOG" not in launcher
    assert "RedirectStandardOutput" not in launcher
    assert "No action was completed" in launcher
    assert "if ($PauseAfter)" in launcher
    assert (
        '$Action -in @("setup", "start", "stop", "preflight", "update", "update-runtime", "logs"'
        in launcher
    )
    for action in (
        "open",
        "status",
        "logs",
        "preflight",
        "setup",
        "start",
        "stop",
        "update",
        "help",
    ):
        assert f'"{action}"' in launcher
    assert "Show-SetuoraMenu" in launcher
    assert "Install downloaded update" in launcher
    assert "$ApplicationRoot\\.venv\\Scripts\\python.exe" in launcher
    assert finder.index("..\\.venv\\Scripts\\python.exe") < finder.index("py -3.11")
    for script_name, action in (
        ("setup.bat", "setup"),
        ("start_setuora.bat", "start"),
        ("stop_setuora.bat", "stop"),
        ("update.bat", "update"),
    ):
        wrapper = (PROJECT_ROOT / "scripts" / script_name).read_text(encoding="utf-8")
        assert f'call "%~dp0..\\setuora.bat" {action} %*' in wrapper
        assert "endlocal & exit /b %EXIT_CODE%" in wrapper


def test_source_update_stops_before_mutating_application_files():
    launcher = (PROJECT_ROOT / "client/windows/setuora.ps1").read_text(encoding="utf-8")
    update = launcher[
        launcher.index("function Update-SetuoraSource") : launcher.index(
            "function Install-SetuoraUpdate"
        )
    ]
    assert "--porcelain" in update and "--untracked-files=all" in update
    assert "--is-ancestor" in update
    assert update.index('"fetch"') < update.index('Invoke-SetuoraDeployment "stop"')
    assert update.index('Invoke-SetuoraDeployment "stop"') < update.index('"merge", "--ff-only"')
    assert update.index('"merge", "--ff-only"') < update.index('Invoke-SetuoraDeployment "update"')


def _valid_environment():
    return {
        "APP_SECRET_KEY": "x" * 48,
        "BOOTSTRAP_ADMIN_PASSWORD": "unique-admin-password",
        "SETUORA_APP_MODE": "lite",
        "SESSION_COOKIE_SECURE": "true",
        "TRUSTED_HOSTS": "127.0.0.1,localhost",
        "SETUORA_WEB_PORT": "8000",
        "AUTOMATIC_BACKUPS_ENABLED": "true",
        "BACKUP_RETENTION_COUNT": "14",
    }


def test_task_registration_survives_continuous_runtime_battery_and_crashes(tmp_path, monkeypatch):
    from xml.etree import ElementTree

    runner = tmp_path / "warehouse & office" / "run-server.cmd"
    monkeypatch.setattr(deploy, "RUNNER_PATH", runner)
    monkeypatch.setattr(deploy, "PROJECT_ROOT", runner.parent)
    registrations = []

    def register(*arguments):
        assert arguments[:3] == ("/Create", "/TN", deploy.TASK_NAME)
        task_path = Path(arguments[arguments.index("/XML") + 1])
        registrations.append(ElementTree.fromstring(task_path.read_text(encoding="utf-16")))

    monkeypatch.setattr(deploy, "_task", register)
    deploy._ensure_task()
    task = registrations[0]
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert task.findtext("t:Triggers/t:BootTrigger/t:Enabled", namespaces=ns) == "true"
    assert task.findtext("t:Principals/t:Principal/t:UserId", namespaces=ns) == "S-1-5-18"
    for key, expected in {
        "ExecutionTimeLimit": "PT0S",
        "DisallowStartIfOnBatteries": "false",
        "StopIfGoingOnBatteries": "false",
        "MultipleInstancesPolicy": "IgnoreNew",
        "StartWhenAvailable": "true",
        "RestartOnFailure/Interval": "PT1M",
        "RestartOnFailure/Count": "10",
    }.items():
        xpath = "t:Settings/" + "/".join(f"t:{part}" for part in key.split("/"))
        assert task.findtext(xpath, namespaces=ns) == expected
    assert task.findtext("t:Actions/t:Exec/t:Command", namespaces=ns) == "cmd.exe"
    assert task.findtext("t:Actions/t:Exec/t:Arguments", namespaces=ns) == (
        f'/d /s /c ""{runner}""'
    )
    assert task.findtext("t:Actions/t:Exec/t:WorkingDirectory", namespaces=ns) == str(runner.parent)


def test_invalid_setup_and_update_fail_before_install_or_stop(tmp_path, monkeypatch):
    import argparse

    import pytest

    env_path = tmp_path / ".env"
    env_path.write_text("APP_SECRET_KEY=invalid\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_PATH", env_path)
    monkeypatch.setattr(deploy, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deploy, "_check_windows", lambda: None)
    monkeypatch.setattr(deploy, "_prepare_environment", lambda: None)
    mutations = []
    monkeypatch.setattr(deploy, "_install_runtime", lambda: mutations.append("install"))
    monkeypatch.setattr(deploy, "stop", lambda _: mutations.append("stop"))
    for operation in (deploy.setup, deploy.update):
        with pytest.raises(deploy.DeploymentError, match="Preflight failed"):
            operation(argparse.Namespace())
    assert mutations == []


def test_repair_preserves_existing_database_and_host_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deploy, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(deploy, "_run", lambda *args, **kwargs: None)
    monkeypatch.setenv("COMPUTERNAME", "warehouse-pc")
    values = _valid_environment()
    values.update(
        {
            "DATABASE_URL": "sqlite:///./warehouse/custom.db",
            "TRUSTED_HOSTS": "127.0.0.1,localhost,warehouse.example,192.168.1.50",
            "BOOTSTRAP_ADMIN_PASSWORD": "",
        }
    )
    database = tmp_path / "warehouse" / "custom.db"
    database.parent.mkdir()
    database.write_bytes(b"existing database content")
    deploy._write_env(values)

    def unexpected_password_prompt(_):
        raise AssertionError("An existing database must not request another bootstrap password")

    monkeypatch.setattr(deploy, "_prompt_secret", unexpected_password_prompt)
    deploy._prepare_environment()
    stored = deploy._read_env()[1]
    assert stored["DATABASE_URL"] == values["DATABASE_URL"]
    assert stored["APP_SECRET_KEY"] == values["APP_SECRET_KEY"]
    assert {"warehouse.example", "192.168.1.50", "localhost", "127.0.0.1"}.issubset(
        stored["TRUSTED_HOSTS"].split(",")
    )
    assert database.read_bytes() == b"existing database content"
    assert deploy._has_application_data()


def test_preflight_rejects_wrong_port_and_nonpersistent_database():
    values = _valid_environment()
    values.update({"SETUORA_WEB_PORT": "9000", "DATABASE_URL": "sqlite:///:memory:"})
    issues = deploy._environment_issues(values, has_application_data=False)
    assert "SETUORA_WEB_PORT must remain 8000 for the Windows service." in issues
    assert "DATABASE_URL must point to a persistent SQLite database for backups." in issues


def test_stop_waits_until_web_process_releases_port(monkeypatch):
    import argparse

    events = []
    monkeypatch.setattr(deploy, "_check_windows", lambda: None)
    monkeypatch.setattr(deploy, "_task", lambda *args, **kwargs: events.append("task-end"))
    attempts = iter([[42], []])

    def listeners():
        events.append("check-port")
        return next(attempts)

    monkeypatch.setattr(deploy, "_port_listeners", listeners)
    monkeypatch.setattr(
        deploy,
        "_run",
        lambda *args, **kwargs: __import__("subprocess").CompletedProcess(args, 0),
    )
    monkeypatch.setattr(deploy.time, "sleep", lambda _: None)
    deploy.stop(argparse.Namespace())
    assert events == ["task-end", "check-port", "check-port"]


def test_no_listener_is_free_even_when_loopback_connections_time_out(monkeypatch):
    import subprocess

    monkeypatch.setattr(
        deploy, "_run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0)
    )
    monkeypatch.setattr(deploy, "_port_listeners", lambda: [])
    deploy._wait_for_stop(timeout_seconds=1)


def test_stop_does_not_report_success_when_process_keeps_running(monkeypatch):
    import argparse

    import pytest

    monkeypatch.setattr(deploy, "_check_windows", lambda: None)
    monkeypatch.setattr(deploy, "_task", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        deploy,
        "_run",
        lambda *args, **kwargs: __import__("subprocess").CompletedProcess(args, 0),
    )
    monkeypatch.setattr(deploy, "_port_listeners", lambda: [42])
    times = iter([0, 31])
    monkeypatch.setattr(deploy.time, "monotonic", lambda: next(times))
    with pytest.raises(deploy.DeploymentError, match="still in use"):
        deploy.stop(argparse.Namespace())


def test_foreign_port_owner_blocks_stop_and_update(monkeypatch):
    import argparse
    import subprocess

    import pytest

    monkeypatch.setattr(deploy, "_check_windows", lambda: None)
    monkeypatch.setattr(deploy, "_task", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        deploy,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 1, "", "Port 8000 is held by process 42, which is not this Setuora Lite service."
        ),
    )
    with pytest.raises(deploy.DeploymentError, match="not this Setuora Lite service"):
        deploy.stop(argparse.Namespace())


def test_port_clearer_requires_exact_server_executable_and_command():
    script = (PROJECT_ROOT / "scripts/windows/clear-owned-port.ps1").read_text(encoding="utf-8")
    assert '.venv\\Scripts\\python.exe' in script
    assert '.venv\\Scripts\\uvicorn.exe' in script
    assert "[StringComparison]::OrdinalIgnoreCase" in script
    assert "app\\.main:app" in script
    assert "--port" in script
    assert script.index("Test-SetuoraProcess $process") < script.index("Stop-Process -Id $processId")


def test_master_route_check_requires_setuora_api_without_sending_credentials(tmp_path, monkeypatch):
    import argparse
    import io
    import json
    import urllib.error

    import pytest

    env_path = tmp_path / ".env"
    env_path.write_text("MASTER_URL=\n", encoding="utf-8")
    runtime = tmp_path / "data" / "master-connection.env"
    runtime.parent.mkdir()
    runtime.write_text(
        "MASTER_URL=https://master.tailnet.ts.net\nMASTER_API_KEY=never-send-this\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(deploy, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deploy, "ENV_PATH", env_path)
    monkeypatch.setattr(deploy, "_check_windows", lambda: None)
    seen = []

    def reachable(request, timeout):
        seen.append((request.full_url, request.get_header("Authorization"), timeout))
        body = io.BytesIO(json.dumps({"error": {"code": "AUTH_REQUIRED"}}).encode())
        raise urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", {"WWW-Authenticate": "Bearer"}, body
        )

    monkeypatch.setattr(deploy.urllib.request, "urlopen", reachable)
    deploy.master_check(argparse.Namespace())
    assert seen == [("https://master.tailnet.ts.net/api/v1/node", None, 10)]

    def wrong_route(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO())

    monkeypatch.setattr(deploy.urllib.request, "urlopen", wrong_route)
    with pytest.raises(deploy.DeploymentError, match="HTTP 404"):
        deploy.master_check(argparse.Namespace())

    def wrong_unauthorized(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            {"WWW-Authenticate": "Bearer"},
            io.BytesIO(b'{"error":null}'),
        )

    monkeypatch.setattr(deploy.urllib.request, "urlopen", wrong_unauthorized)
    with pytest.raises(deploy.DeploymentError, match="HTTP 401"):
        deploy.master_check(argparse.Namespace())


def test_setup_repair_stops_existing_task_before_replacing_runtime(monkeypatch):
    import argparse
    import subprocess

    events = []
    monkeypatch.setattr(deploy, "_check_windows", lambda: None)
    monkeypatch.setattr(deploy, "_prepare_environment", lambda: None)
    monkeypatch.setattr(deploy, "preflight", lambda _: None)
    monkeypatch.setattr(
        deploy, "_task", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0)
    )
    monkeypatch.setattr(deploy, "stop", lambda _: events.append("stop"))
    monkeypatch.setattr(deploy, "_install_runtime", lambda: events.append("install"))
    monkeypatch.setattr(deploy, "_secure_code_permissions", lambda: None)
    monkeypatch.setattr(deploy, "_ensure_task", lambda: None)
    monkeypatch.setattr(deploy, "_wait_for_health", lambda: events.append("healthy"))
    monkeypatch.setattr(deploy, "_write_env", lambda _: None)
    monkeypatch.setattr(deploy, "_remove_legacy_lan_firewall", lambda: None)
    deploy.setup(argparse.Namespace())
    assert events == ["stop", "install", "healthy"]


def test_application_reads_installer_password_escaping(tmp_path, monkeypatch):
    import os

    from app.config import _load_env_file

    key = "SETUORA_DEPLOYMENT_ESCAPE_TEST"
    for value in (
        'Password with "quotes" and \\ slash # spaces café',
        r"Literal\n\t\U0001f600 and escaped ending\\",
        r"Combined slash and quote: \"",
    ):
        monkeypatch.delenv(key, raising=False)
        fixture = tmp_path / "password.env"
        fixture.write_text(f"{key}={deploy._format_env_value(value)}\n", encoding="utf-8")
        _load_env_file(fixture)
        assert os.environ[key] == value
    monkeypatch.delenv(key, raising=False)


def test_fresh_application_start_login_backup_and_restart(tmp_path):
    import os
    import subprocess
    import sys

    password = 'Warehouse-2026 "secure" \\ pass'
    env_fixture = tmp_path / "bootstrap.env"
    env_fixture.write_text(
        f"BOOTSTRAP_ADMIN_PASSWORD={deploy._format_env_value(password)}\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "SETUORA_APP_MODE": "lite",
            "SETUORA_ALLOW_LEGACY_TEST_MODE": "false",
            "APP_SECRET_KEY": "isolated-startup-secret-" + "x" * 48,
            "DATABASE_URL": f"sqlite:///{tmp_path / 'fresh.db'}",
            "BOOTSTRAP_ADMIN_USERNAME": "warehouse-admin",
            "BOOTSTRAP_ADMIN_PASSWORD": "",
            "SESSION_COOKIE_SECURE": "false",
            "TRUSTED_HOSTS": "127.0.0.1,localhost,testserver",
            "AUTOMATIC_BACKUPS_ENABLED": "false",
            "BACKUP_DIRECTORY": str(tmp_path / "backups"),
            "BACKUP_SETTINGS_FILE": str(tmp_path / "backup-settings.env"),
            "BACKUP_OFFSITE_DIRECTORY": "",
            "MASTER_SYNC_ENABLED": "false",
            "MASTER_CONNECTION_SETTINGS_FILE": str(tmp_path / "master-connection.env"),
            "SFTP_SYNC_ENABLED": "false",
            "SFTP_CONNECTION_SETTINGS_FILE": str(tmp_path / "sftp-connection.env"),
            "SMOKE_BOOTSTRAP_ENV": str(env_fixture),
            "SMOKE_BOOTSTRAP_PASSWORD": password,
        }
    )
    script = r"""
import os
import sqlite3
from pathlib import Path

from app.config import _load_env_file, get_settings

os.environ.pop("BOOTSTRAP_ADMIN_PASSWORD", None)
_load_env_file(Path(os.environ["SMOKE_BOOTSTRAP_ENV"]))
get_settings.cache_clear()

from fastapi.testclient import TestClient
from app.main import app
from app.services.backup import create_scheduled_backup, verify_sqlite_backup

for startup in range(2):
    if startup:
        # A repaired/restarted installation no longer retains its bootstrap password.
        os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = ""
        os.environ["SESSION_COOKIE_SECURE"] = "true"
        get_settings.cache_clear()
    scheme = "https" if startup else "http"
    with TestClient(app, base_url=f"{scheme}://testserver") as client:
        health = client.get("/health")
        assert health.status_code == 200, health.text
        assert health.json() == {"status": "ok", "role": os.environ["SETUORA_APP_MODE"]}
        login_page = client.get("/login")
        assert login_page.status_code == 200, login_page.text
        login = client.post(
            "/login",
            data={
                "username": "warehouse-admin",
                "password": os.environ["SMOKE_BOOTSTRAP_PASSWORD"],
            },
            headers={"Origin": f"{scheme}://testserver"},
            follow_redirects=False,
        )
        assert login.status_code == 303, login.text
        assert "httponly" in login.headers["set-cookie"].lower()
        if startup:
            assert "secure" in login.headers["set-cookie"].lower()
        home = client.get("/")
        assert home.status_code == 200, home.text
        backup = create_scheduled_backup()
        verify_sqlite_backup(backup.path)
        with sqlite3.connect(backup.path) as saved:
            assert saved.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert saved.execute("PRAGMA foreign_key_check").fetchall() == []
            assert saved.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
print("fresh startup, login, verified backup, and restart passed")
"""
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "fresh startup, login, verified backup, and restart passed" in result.stdout


def test_status_checks_running_application_without_reading_admin_secrets(monkeypatch, capsys):
    import argparse
    import io
    import json
    import subprocess

    monkeypatch.setattr(deploy, "_check_windows", lambda: None)
    monkeypatch.setattr(
        deploy, "_task", lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, stdout="")
    )

    def protected_env():
        raise PermissionError("A regular user cannot read .env")

    monkeypatch.setattr(deploy, "_read_env", protected_env)
    monkeypatch.setattr(
        deploy.urllib.request,
        "urlopen",
        lambda *args, **kwargs: io.BytesIO(json.dumps({"status": "ok", "role": "lite"}).encode()),
    )
    deploy.status(argparse.Namespace())
    assert "database is responding" in capsys.readouterr().out


def test_status_reports_stopped_server_instead_of_claiming_it_is_running(monkeypatch):
    import argparse
    import subprocess

    import pytest

    monkeypatch.setattr(deploy, "_check_windows", lambda: None)
    monkeypatch.setattr(
        deploy, "_task", lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, stdout="")
    )

    def unavailable(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(deploy.urllib.request, "urlopen", unavailable)
    with pytest.raises(deploy.DeploymentError, match="Choose Start"):
        deploy.status(argparse.Namespace())


def test_start_recommends_repair_when_background_task_is_missing(monkeypatch):
    import argparse
    import subprocess

    import pytest

    monkeypatch.setattr(deploy, "_check_windows", lambda: None)
    monkeypatch.setattr(deploy, "preflight", lambda _: None)
    calls = []

    def missing_task(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, stdout="")

    monkeypatch.setattr(deploy, "_task", missing_task)
    with pytest.raises(deploy.DeploymentError, match="Choose Setup / repair"):
        deploy.start(argparse.Namespace())
    assert len(calls) == 1
    assert calls[0][0] == "/Query"
