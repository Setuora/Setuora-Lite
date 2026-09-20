"""Windows-native lifecycle helper for Setuora Lite."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import socket
import stat
import subprocess  # nosec B404
import sys
import tempfile
import time
import urllib.error
import urllib.request
import venv
from pathlib import Path
from xml.sax.saxutils import escape

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_PATH = PROJECT_ROOT / ".env"
ENV_EXAMPLE_PATH = PROJECT_ROOT / ".env.example"
VENV_PATH = PROJECT_ROOT / ".venv"
RUNNER_PATH = PROJECT_ROOT / "scripts" / "windows" / "run-server.cmd"
PORT_CLEARER_PATH = PROJECT_ROOT / "scripts" / "windows" / "clear-owned-port.ps1"
RUNTIME_PORTS_PATH = PROJECT_ROOT / ".runtime-ports.json"
TASK_NAME = "Setuora-Lite"
FIREWALL_RULE_NAME = "Setuora-Lite-LAN"
UNSAFE_PASSWORDS = {
    "",
    "admin123",
    "change-this-password",
    "change-this-before-first-start",
}
PLACEHOLDER_SECRETS = {
    "",
    "dev-change-me",
    "change-this-before-production",
    "replace-with-a-long-random-secret",
}
PLAIN_ENV_VALUE = re.compile(r"^[A-Za-z0-9_./,:*?=@+%-]+$")
WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)


class DeploymentError(RuntimeError):
    pass


def _run(
    command: list[str],
    *,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  # nosec B603
        command,
        cwd=PROJECT_ROOT,
        check=check,
        text=True,
        capture_output=capture,
    )


def _read_env(path: Path | None = None) -> tuple[list[str], dict[str, str]]:
    path = path or ENV_PATH
    if not path.exists():
        return [], {}
    lines = path.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1].replace(r"\"", '"').replace("\\\\", "\\")
        elif len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        values[key] = value
    return lines, values


def _format_env_value(value: str) -> str:
    if value and PLAIN_ENV_VALUE.fullmatch(value):
        return value
    escaped = value.replace("\\", r"\\").replace('"', r"\"")
    return f'"{escaped}"'


def _write_env(updates: dict[str, str]) -> None:
    if ENV_PATH.exists() and getattr(ENV_PATH.lstat(), "st_file_attributes", 0) & WINDOWS_REPARSE_POINT:
        raise DeploymentError(".env is a linked file; setup will not write through it.")
    lines, _ = _read_env()
    pending = dict(updates)
    output: list[str] = []
    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip().removeprefix("export ").strip()
            if key in pending:
                output.append(f"{key}={_format_env_value(pending.pop(key))}")
                continue
        output.append(raw_line)
    if pending:
        if output and output[-1]:
            output.append("")
        output.extend(f"{key}={_format_env_value(value)}" for key, value in pending.items())
    ENV_PATH.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    if sys.platform == "win32":
        _run(
            [
                "icacls.exe",
                str(ENV_PATH),
                "/inheritance:r",
                "/grant:r",
                "*S-1-5-18:F",
                "*S-1-5-32-544:F", "/L",
            ]
        )
        _run(["icacls.exe", str(ENV_PATH), "/remove:g", "*S-1-5-32-545", "*S-1-5-11", "*S-1-1-0", "/Q", "/L"])


def _check_windows() -> None:
    if sys.platform != "win32":
        raise DeploymentError(
            "The active Setuora Lite deployment supports Windows only. "
            "Historical Linux/container files are preserved under archive/."
        )


def _environment_issues(
    values: dict[str, str],
    *,
    has_application_data: bool,
) -> list[str]:
    issues: list[str] = []
    database_url = values.get("DATABASE_URL") or "sqlite:///./data/setuora.db"
    if not database_url.startswith("sqlite:///") or database_url == "sqlite:///:memory:":
        issues.append("DATABASE_URL must point to a persistent SQLite database for backups.")
    secret = values.get("APP_SECRET_KEY", "")
    if secret in PLACEHOLDER_SECRETS or len(secret) < 32:
        issues.append("APP_SECRET_KEY must contain at least 32 random characters.")
    password = values.get("BOOTSTRAP_ADMIN_PASSWORD", "")
    if not has_application_data and (password in UNSAFE_PASSWORDS or len(password) < 12):
        issues.append("BOOTSTRAP_ADMIN_PASSWORD must be unique and at least 12 characters.")
    if values.get("SETUORA_APP_MODE", "lite").strip().lower() != "lite":
        issues.append("SETUORA_APP_MODE must be lite.")
    if values.get("SESSION_COOKIE_SECURE", "true").strip().lower() != "true":
        issues.append("SESSION_COOKIE_SECURE must be true for private HTTPS.")
    trusted_hosts = {
        item.strip().lower() for item in values.get("TRUSTED_HOSTS", "").split(",") if item.strip()
    }
    if not {"localhost", "127.0.0.1"}.issubset(trusted_hosts):
        issues.append("TRUSTED_HOSTS must include localhost and 127.0.0.1.")
    for key, default in (("SETUORA_WEB_PORT", "8000"), ("SETUORA_CADDY_PORT", "8080")):
        try:
            port = int(values.get(key) or default)
        except ValueError:
            port = 0
        if not 1024 <= port <= 65535:
            issues.append(f"{key} must be a port from 1024 to 65535.")
    if values.get("SETUORA_WEB_PORT", "8000") == values.get("SETUORA_CADDY_PORT", "8080"):
        issues.append("The Lite application and Caddy must use different ports.")
    if values.get("AUTOMATIC_BACKUPS_ENABLED", "true").strip().lower() != "true":
        issues.append("AUTOMATIC_BACKUPS_ENABLED must be true for production.")
    try:
        retention = int(values.get("BACKUP_RETENTION_COUNT", "14"))
    except ValueError:
        retention = 0
    if retention < 2:
        issues.append("BACKUP_RETENTION_COUNT must be at least 2.")
    return issues


def _prompt_secret(label: str) -> str:
    if not sys.stdin.isatty():
        raise DeploymentError(f"{label} is missing. Set it in .env before non-interactive setup.")
    first = getpass.getpass(f"{label}: ").strip()
    second = getpass.getpass(f"Confirm {label}: ").strip()
    if first != second:
        raise DeploymentError(f"{label} values did not match.")
    return first


def _prepare_environment() -> None:
    if not ENV_PATH.exists():
        ENV_PATH.write_text(
            ENV_EXAMPLE_PATH.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    _, values = _read_env()
    updates: dict[str, str] = {}
    if (
        values.get("APP_SECRET_KEY", "") in PLACEHOLDER_SECRETS
        or len(values.get("APP_SECRET_KEY", "")) < 32
    ):
        updates["APP_SECRET_KEY"] = secrets.token_urlsafe(48)
    database_exists = _has_application_data()
    password = values.get("BOOTSTRAP_ADMIN_PASSWORD", "")
    if not database_exists and (password in UNSAFE_PASSWORDS or len(password) < 12):
        password = _prompt_secret("First administrator password")
        if len(password) < 12:
            raise DeploymentError(
                "The first administrator password must be at least 12 characters."
            )
        updates["BOOTSTRAP_ADMIN_PASSWORD"] = password
    computer_name = os.getenv("COMPUTERNAME", "").strip().lower()
    trusted_hosts = {
        item.strip().lower() for item in values.get("TRUSTED_HOSTS", "").split(",") if item.strip()
    } | {"127.0.0.1", "localhost"}
    if computer_name:
        trusted_hosts.add(computer_name)
    updates.update(
        {
            "SETUORA_APP_MODE": "lite",
            "DATABASE_URL": values.get("DATABASE_URL") or "sqlite:///./data/setuora.db",
            "SESSION_COOKIE_SECURE": "true",
            "TRUSTED_HOSTS": ",".join(sorted(trusted_hosts)),
            "SETUORA_WEB_PORT": values.get("SETUORA_WEB_PORT") or "8000",
            "SETUORA_CADDY_PORT": values.get("SETUORA_CADDY_PORT") or "8080",
            "MASTER_SYNC_ENABLED": values.get("MASTER_SYNC_ENABLED") or "false",
            "SFTP_SYNC_ENABLED": "false",
        }
    )
    _write_env(updates)
    (PROJECT_ROOT / "data").mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "logs").mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        for protected in (PROJECT_ROOT / "data", PROJECT_ROOT / "logs"):
            if getattr(protected.lstat(), "st_file_attributes", 0) & WINDOWS_REPARSE_POINT:
                raise DeploymentError(f"{protected.name} is a linked folder; setup will not change its permissions.")
            _run(
                [
                    "icacls.exe", str(protected), "/inheritance:r", "/grant:r",
                    "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F", "/T", "/Q", "/L",
                ]
            )
            _run(["icacls.exe", str(protected), "/remove:g", "*S-1-5-32-545", "*S-1-5-11", "*S-1-1-0", "/T", "/Q", "/L"])


def _venv_python() -> Path:
    return VENV_PATH / "Scripts" / "python.exe"


def _install_runtime() -> None:
    if not _venv_python().is_file():
        venv.EnvBuilder(with_pip=True).create(VENV_PATH)
    _run(
        [
            str(_venv_python()),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--require-hashes",
            "-r",
            str(PROJECT_ROOT / "requirements-runtime.lock"),
        ]
    )


def _task(*arguments: str, check: bool = True, capture: bool = False):
    return _run(["schtasks.exe", *arguments], check=check, capture=capture)


def _task_xml() -> str:
    # Defaults stop a task after 72 hours and when a PC switches to battery.
    # Register explicit settings suitable for a continuously running server.
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><BootTrigger>
    <Enabled>true</Enabled><ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
  </BootTrigger></Triggers>
  <Principals><Principal id="System">
    <UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel>
  </Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>10</Count></RestartOnFailure>
  </Settings>
  <Actions Context="System"><Exec>
    <Command>cmd.exe</Command>
    <Arguments>{escape(f'/d /s /c ""{RUNNER_PATH}""')}</Arguments>
    <WorkingDirectory>{escape(str(PROJECT_ROOT))}</WorkingDirectory>
  </Exec></Actions>
</Task>
"""


def _ensure_task() -> None:
    with tempfile.TemporaryDirectory(prefix="setuora-task-") as directory:
        task_path = Path(directory) / "task.xml"
        task_path.write_text(_task_xml(), encoding="utf-16")
        _task("/Create", "/TN", TASK_NAME, "/XML", str(task_path), "/F")


def _remove_legacy_lan_firewall() -> None:
    script = (
        "$ErrorActionPreference='Stop'; "
        f"$existing=Get-NetFirewallRule -Name '{FIREWALL_RULE_NAME}' "
        "-ErrorAction SilentlyContinue; if ($existing) { Remove-NetFirewallRule "
        f"-Name '{FIREWALL_RULE_NAME}' }}"
    )
    _run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-Command",
            script,
        ]
    )


def _wait_for_health(timeout_seconds: int = 120) -> None:
    _, values = _read_env()
    port = values.get("SETUORA_WEB_PORT", "8000")
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:  # nosec B310
                payload = json.load(response)
            if payload == {"status": "ok", "role": "lite"}:
                return
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(2)
    raise DeploymentError(f"Setuora Lite did not become healthy at {url}. Run `setuora.ps1 logs`.")


def _configured_port(key: str, default: int) -> int:
    _, values = _read_env()
    try:
        port = int(values.get(key) or default)
    except ValueError as exc:
        raise DeploymentError(f"{key} is not a valid port.") from exc
    if not 1024 <= port <= 65535:
        raise DeploymentError(f"{key} must be a port from 1024 to 65535.")
    return port


def _write_runtime_ports() -> None:
    ports = {
        "web_port": _configured_port("SETUORA_WEB_PORT", 8000),
        "caddy_port": _configured_port("SETUORA_CADDY_PORT", 8080),
    }
    if RUNTIME_PORTS_PATH.exists() and getattr(RUNTIME_PORTS_PATH.lstat(), "st_file_attributes", 0) & WINDOWS_REPARSE_POINT:
        raise DeploymentError("The runtime ports file is linked; setup will not write through it.")
    RUNTIME_PORTS_PATH.write_text(json.dumps(ports, sort_keys=True) + "\n", encoding="utf-8")
    if sys.platform == "win32":
        _run(["icacls.exe", str(RUNTIME_PORTS_PATH), "/inheritance:r", "/grant:r", "*S-1-5-18:F", "*S-1-5-32-544:F", "*S-1-5-32-545:RX", "/Q", "/L"])
        _run(["icacls.exe", str(RUNTIME_PORTS_PATH), "/remove:g", "*S-1-5-11", "*S-1-1-0", "/Q", "/L"])


def _port_is_free(port: int) -> bool:
    if _port_listeners(port):
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _master_reserved_ports() -> set[int]:
    """Keep Master's saved port free even when its task is stopped."""
    program_data = os.environ.get("ProgramData")
    if not program_data:
        return set()
    reserved: set[int] = set()
    for name in ("Setuora-Master", "Setuora-Master-windows"):
        root = Path(program_data) / "Setuora" / name
        public_port = root / "runtime-port.txt"
        if public_port.is_file():
            try:
                port = int(public_port.read_text(encoding="ascii").strip())
            except (OSError, UnicodeError, ValueError):
                port = 0
            if 1024 <= port <= 65535:
                reserved.add(port)
                continue
        # Older Master releases may lack the public port file. Setup runs with
        # Administrator rights and can use the protected environment instead.
        env_path = root / ".env"
        if env_path.is_file():
            try:
                _, values = _read_env(env_path)
                port = int(values.get("SETUORA_WEB_PORT") or "8000")
            except (OSError, UnicodeError, ValueError) as exc:
                raise DeploymentError(
                    f"The Master port record in {root} is unreadable. Repair Master before setting up Lite."
                ) from exc
            if not 1024 <= port <= 65535:
                raise DeploymentError(
                    f"The Master port record in {root} is invalid. Repair Master before setting up Lite."
                )
            reserved.add(port)
    return reserved


def _assign_free_port(key: str, default: int, *, exclude: set[int] | None = None) -> int:
    preferred = _configured_port(key, default)
    excluded = set(exclude or ()) | _master_reserved_ports()
    candidates = [preferred, *range(default, 65536)]
    for port in candidates:
        if port not in excluded and _port_is_free(port):
            if port != preferred:
                _write_env({key: str(port)})
                print(f"Port {preferred} is in use; Setuora Lite will use {port} for {key}.")
            _write_runtime_ports()
            return port
    raise DeploymentError(f"No free localhost port is available for {key}.")


def _wait_for_stop(timeout_seconds: int = 30, *, allow_foreign: bool = False) -> None:
    # Do not replace runtime files while the scheduled process still owns the port.
    # Inspect every interface, including a listener that does not bind loopback.
    # The helper refuses to stop any process it cannot identify as this server.
    port = _configured_port("SETUORA_WEB_PORT", 8000)
    command = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PORT_CLEARER_PATH),
            "-ProjectRoot",
            str(PROJECT_ROOT),
            "-Port",
            str(port),
        ]
    if allow_foreign:
        command.append("-AllowForeign")
    result = _run(
        command,
        check=False,
        capture=True,
    )
    if result.returncode != 0:
        raise DeploymentError(
            (result.stderr or result.stdout or f"Could not inspect the owner of port {port}.").strip()
        )
    if allow_foreign:
        return
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _port_listeners(port):
            return
        time.sleep(0.5)
    raise DeploymentError(
        f"Port {port} is still in use after stopping the task. "
        "Check the running Setuora process before updating files."
    )
def _secure_code_permissions() -> None:
    if sys.platform != "win32":
        return
    # ProgramData normally allows Users to create files in child directories.
    # Task Scheduler executes this code as SYSTEM, so installed code must be
    # writable only by SYSTEM and Administrators. Secrets have separate ACLs.
    if getattr(PROJECT_ROOT.lstat(), "st_file_attributes", 0) & WINDOWS_REPARSE_POINT:
        raise DeploymentError("The installation folder is linked; setup will not change its permissions.")
    _run(
        [
            "icacls.exe", str(PROJECT_ROOT), "/inheritance:r", "/grant:r",
            "*S-1-5-18:F", "*S-1-5-32-544:F", "*S-1-5-32-545:RX", "/L",
        ]
    )
    _run(["icacls.exe", str(PROJECT_ROOT), "/remove:g", "*S-1-5-11", "*S-1-1-0", "/Q", "/L"])
    for child in PROJECT_ROOT.iterdir():
        if child.name in {".env", "data", "logs"}:
            continue
        if getattr(child.lstat(), "st_file_attributes", 0) & WINDOWS_REPARSE_POINT:
            raise DeploymentError(f"Installed code contains a linked path: {child.name}")
        grants = (
            ["*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F", "*S-1-5-32-545:(OI)(CI)RX"]
            if child.is_dir()
            else ["*S-1-5-18:F", "*S-1-5-32-544:F", "*S-1-5-32-545:RX"]
        )
        command = ["icacls.exe", str(child), "/inheritance:r", "/grant:r", *grants]
        if child.is_dir():
            command.append("/T")
        command.append("/Q")
        command.append("/L")
        _run(command)
        if child.is_dir():
            _run(["icacls.exe", str(child), "/remove:g", "*S-1-5-11", "*S-1-1-0", "/T", "/Q", "/L"])
        else:
            _run(["icacls.exe", str(child), "/remove:g", "*S-1-5-11", "*S-1-1-0", "/Q", "/L"])


def _port_listeners(port: int) -> list[int]:
    script = (
        f"$items = @(Get-NetTCPConnection -LocalPort {port} -State Listen "
        "-ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique); "
        "ConvertTo-Json -InputObject $items -Compress"
    )
    result = _run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", script],
        capture=True,
    )
    try:
        listeners = json.loads(result.stdout or "[]")
    except ValueError as exc:
        raise DeploymentError(f"Windows could not inspect port {port} listeners.") from exc
    if not isinstance(listeners, list) or any(
        not isinstance(pid, int) for pid in listeners
    ):
        raise DeploymentError(f"Windows returned invalid port {port} listener details.")
    return listeners


def _public_web_port() -> int:
    if not RUNTIME_PORTS_PATH.exists():
        return 8000  # Existing installations receive the public file at their next repair.
    try:
        port = json.loads(RUNTIME_PORTS_PATH.read_text(encoding="utf-8"))["web_port"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DeploymentError("The saved Lite port is unreadable. Run Setup / repair.") from exc
    if type(port) is not int or not 1024 <= port <= 65535:
        raise DeploymentError("The saved Lite port is invalid. Run Setup / repair.")
    return port


def _has_application_data() -> bool:
    _, values = _read_env()
    url = values.get("DATABASE_URL") or "sqlite:///./data/setuora.db"
    if not url.startswith("sqlite:///"):
        return False
    database_path = Path(url.removeprefix("sqlite:///"))
    if not database_path.is_absolute():
        database_path = PROJECT_ROOT / database_path
    return database_path.is_file()


def preflight(_args: argparse.Namespace) -> None:
    _check_windows()
    if not ENV_PATH.exists():
        raise DeploymentError(".env is missing. Run `setuora.ps1 setup` first.")
    _, values = _read_env()
    issues = _environment_issues(
        values,
        has_application_data=_has_application_data(),
    )
    for required in (RUNNER_PATH, PORT_CLEARER_PATH, PROJECT_ROOT / "requirements-runtime.lock"):
        if not required.is_file():
            issues.append(f"Required deployment file is missing: {required.name}")
    if issues:
        raise DeploymentError("Preflight failed:\n- " + "\n- ".join(issues))
    print("Windows production configuration preflight passed without exposing secrets.")


def setup(_args: argparse.Namespace) -> None:
    _check_windows()
    _prepare_environment()
    preflight(_args)
    if _task("/Query", "/TN", TASK_NAME, check=False, capture=True).returncode == 0:
        _task("/End", "/TN", TASK_NAME, check=False)
        _wait_for_stop(allow_foreign=True)
    else:
        _wait_for_stop(allow_foreign=True)
    _assign_free_port("SETUORA_WEB_PORT", 8000)
    _install_runtime()
    _secure_code_permissions()
    _remove_legacy_lan_firewall()
    _ensure_task()
    _task("/Run", "/TN", TASK_NAME)
    _wait_for_health()
    _write_env({"BOOTSTRAP_ADMIN_PASSWORD": ""})
    print("Setuora Lite is healthy on Windows.")
    print("Lite listens on localhost. Tailscale Serve will provide its private HTTPS address.")
    print("Configure the Master HTTPS connection from Admin -> Master connection.")


def start(_args: argparse.Namespace) -> None:
    _check_windows()
    preflight(_args)
    task = _task("/Query", "/TN", TASK_NAME, check=False, capture=True)
    if task.returncode != 0:
        raise DeploymentError(
            "The Setuora background task is missing. Choose Setup / repair first."
        )
    # Stop this installation, then choose another port if a different process
    # has occupied its previous port since the last start.
    _task("/End", "/TN", TASK_NAME, check=False)
    _wait_for_stop(allow_foreign=True)
    _assign_free_port("SETUORA_WEB_PORT", 8000)
    _task("/Run", "/TN", TASK_NAME)
    _wait_for_health()
    print("Setuora Lite is running.")


def stop(_args: argparse.Namespace) -> None:
    _check_windows()
    _task("/End", "/TN", TASK_NAME, check=False)
    _wait_for_stop(allow_foreign=True)
    print("Setuora Lite stopped. The database and Master event queue were preserved.")


def status(_args: argparse.Namespace) -> None:
    _check_windows()
    task = _task("/Query", "/TN", TASK_NAME, "/FO", "LIST", check=False, capture=True)
    if task.returncode == 0 and task.stdout.strip():
        print(task.stdout.strip())
    port = _public_web_port()
    url = f"http://127.0.0.1:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:  # nosec B310
            payload = json.load(response)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise DeploymentError(
            "Setuora Lite is not responding. Choose Start, or Setup / repair for a new installation. "
            "If Start fails, choose View recent logs."
        ) from exc
    if payload != {"status": "ok", "role": "lite"}:
        raise DeploymentError(f"Port {port} is being used by a different application.")
    print("Setuora Lite is running and its database is responding.")
    print("Open the private Tailscale HTTPS address shown by the controls menu.")


def master_check(_args: argparse.Namespace) -> None:
    """Verify the configured private HTTPS route without sending a node credential."""
    _check_windows()
    _, values = _read_env()
    runtime_path = Path(
        os.getenv("MASTER_CONNECTION_SETTINGS_FILE")
        or values.get("MASTER_CONNECTION_SETTINGS_FILE")
        or "data/master-connection.env"
    )
    if not runtime_path.is_absolute():
        runtime_path = PROJECT_ROOT / runtime_path
    _, runtime = _read_env(runtime_path)
    configured_origin = os.getenv("MASTER_URL", values.get("MASTER_URL", ""))
    origin = runtime.get("MASTER_URL", configured_origin).strip().rstrip("/")
    if not origin:
        print("Master connection is not configured yet. Paste the connection details in Admin -> Master connection.")
        return
    from app.config import master_url_configuration_error

    issue = master_url_configuration_error(origin)
    if issue:
        raise DeploymentError(issue)
    request = urllib.request.Request(
        f"{origin}/api/v1/node", headers={"Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # nosec B310
            status_code = response.status
    except urllib.error.HTTPError as exc:
        status_code = exc.code
        try:
            payload = json.load(exc)
        except (ValueError, OSError):
            payload = {}
        error = payload.get("error") if isinstance(payload, dict) else None
        if (
            status_code == 401
            and exc.headers.get("WWW-Authenticate") == "Bearer"
            and isinstance(error, dict)
            and error.get("code") == "AUTH_REQUIRED"
        ):
            print("Master private HTTPS API is reachable. Credential validation remains in the application.")
            return
    except (OSError, urllib.error.URLError) as exc:
        raise DeploymentError(
            "The saved Master HTTPS address is unreachable from this Lite computer. "
            "Check Tailscale, MagicDNS, HTTPS certificates, and Master Serve; Lite remains running locally."
        ) from exc
    raise DeploymentError(
        f"The saved Master address returned HTTP {status_code} instead of the Setuora node API. "
        "Check the address and Master Serve route; Lite remains running locally."
    )


def trust_tailnet_host(args: argparse.Namespace) -> None:
    """Allow this Lite machine's exact Tailscale HTTPS hostname."""
    _check_windows()
    host = args.host.strip().rstrip(".").lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*\.ts\.net", host):
        raise DeploymentError("Tailscale returned an invalid .ts.net hostname.")
    if not ENV_PATH.exists():
        raise DeploymentError(".env is missing. Run Setup / repair first.")
    _, values = _read_env()
    hosts = {item.strip().lower() for item in values.get("TRUSTED_HOSTS", "").split(",") if item.strip()}
    if host not in hosts:
        hosts.add(host)
        _write_env({"TRUSTED_HOSTS": ",".join(sorted(hosts))})
        print(f"Allowed private HTTPS hostname: {host}")
    else:
        print(f"Private HTTPS hostname is already allowed: {host}")


def backup(_args: argparse.Namespace) -> None:
    _check_windows()
    if not _has_application_data():
        print("No Lite database exists yet; no pre-update backup is needed.")
        return
    preflight(_args)
    from app.services.backup import create_scheduled_backup

    result = create_scheduled_backup()
    print(f"Verified SQLite backup: {result.path}")


def configure_caddy_port(_args: argparse.Namespace) -> None:
    _check_windows()
    if not ENV_PATH.exists():
        raise DeploymentError("Run Setup / repair before configuring Caddy.")
    app_port = _configured_port("SETUORA_WEB_PORT", 8000)
    port = _assign_free_port("SETUORA_CADDY_PORT", 8080, exclude={app_port})
    print(f"Caddy will proxy Lite on localhost:{port}.")


def serve(_args: argparse.Namespace) -> None:
    _check_windows()
    port = _configured_port("SETUORA_WEB_PORT", 8000)
    result = _run(
        [str(_venv_python()), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--workers", "1", "--no-access-log"],
        check=False,
    )
    if result.returncode:
        raise DeploymentError(f"The Lite web server exited with code {result.returncode}.")


def logs(args: argparse.Namespace) -> None:
    _check_windows()
    log_path = PROJECT_ROOT / "logs" / "setuora.log"
    if not log_path.exists():
        raise DeploymentError("No server log exists yet. Start Setuora Lite first.")
    escaped_path = str(log_path).replace("'", "''")
    script = f"Get-Content -LiteralPath '{escaped_path}' -Tail {args.tail}"
    if args.follow:
        script += " -Wait"
    _run(["powershell.exe", "-NoProfile", "-Command", script])


def update(_args: argparse.Namespace) -> None:
    _check_windows()
    if not ENV_PATH.exists():
        raise DeploymentError("Run `setuora.ps1 setup` first.")
    preflight(_args)
    _task("/End", "/TN", TASK_NAME, check=False)
    _wait_for_stop(allow_foreign=True)
    _assign_free_port("SETUORA_WEB_PORT", 8000)
    try:
        _install_runtime()
        _secure_code_permissions()
        _remove_legacy_lan_firewall()
        _ensure_task()
    except (DeploymentError, subprocess.CalledProcessError, OSError):
        # A failed dependency or task refresh should not leave the previous app
        # offline if its existing runtime is still usable.
        try:
            start(_args)
        except (DeploymentError, subprocess.CalledProcessError, OSError) as restart_error:
            print(f"Restart after failed update also failed: {restart_error}", file=sys.stderr)
        raise
    start(_args)
    print("Setuora Lite was updated and is healthy.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deploy Setuora Lite natively on Windows.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, function, help_text in (
        ("preflight", preflight, "validate Windows production configuration"),
        ("setup", setup, "install, configure, start, and verify Setuora Lite"),
        ("start", start, "start the Windows scheduled task"),
        ("stop", stop, "stop Setuora Lite while preserving data"),
        ("status", status, "show the Windows scheduled task state"),
        ("master-check", master_check, "verify the configured private Master HTTPS route"),
        ("trust-tailnet-host", trust_tailnet_host, "allow Lite's exact private HTTPS hostname"),
        ("backup", backup, "create and verify an SQLite backup"),
        ("configure-caddy-port", configure_caddy_port, "choose and save Caddy's free localhost port"),
        ("serve", serve, "run the configured Lite web server"),
        ("update", update, "update dependencies and restart"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.set_defaults(function=function)
    logs_parser = subparsers.add_parser("logs", help="show the Windows server log")
    subparsers.choices["trust-tailnet-host"].add_argument("--host", required=True)
    logs_parser.add_argument("--tail", type=int, default=200)
    logs_parser.add_argument("--follow", action="store_true")
    logs_parser.set_defaults(function=logs)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.function(args)
    except (DeploymentError, subprocess.CalledProcessError, OSError) as exc:
        print(f"Deployment failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
