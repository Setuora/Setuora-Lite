"""Windows-native lifecycle helper for Setuora Lite."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import subprocess  # nosec B404
import sys
import time
import urllib.error
import urllib.request
import venv
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_PATH = PROJECT_ROOT / ".env"
ENV_EXAMPLE_PATH = PROJECT_ROOT / ".env.example"
VENV_PATH = PROJECT_ROOT / ".venv"
RUNNER_PATH = PROJECT_ROOT / "scripts" / "windows" / "run-server.cmd"
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


def _read_env() -> tuple[list[str], dict[str, str]]:
    if not ENV_PATH.exists():
        return [], {}
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
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
                "*S-1-5-32-544:F",
            ]
        )


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
    secret = values.get("APP_SECRET_KEY", "")
    if secret in PLACEHOLDER_SECRETS or len(secret) < 32:
        issues.append("APP_SECRET_KEY must contain at least 32 random characters.")
    password = values.get("BOOTSTRAP_ADMIN_PASSWORD", "")
    if not has_application_data and (password in UNSAFE_PASSWORDS or len(password) < 12):
        issues.append("BOOTSTRAP_ADMIN_PASSWORD must be unique and at least 12 characters.")
    if values.get("SETUORA_APP_MODE", "lite").strip().lower() != "lite":
        issues.append("SETUORA_APP_MODE must be lite.")
    if values.get("SESSION_COOKIE_SECURE", "false").strip().lower() != "false":
        issues.append("SESSION_COOKIE_SECURE must be false for the Windows HTTP pilot.")
    trusted_hosts = {
        item.strip().lower() for item in values.get("TRUSTED_HOSTS", "").split(",") if item.strip()
    }
    if not {"localhost", "127.0.0.1"}.issubset(trusted_hosts):
        issues.append("TRUSTED_HOSTS must include localhost and 127.0.0.1.")
    try:
        port = int(values.get("SETUORA_WEB_PORT", "8000"))
    except ValueError:
        port = 0
    if port != 8000:
        issues.append("SETUORA_WEB_PORT must remain 8000 for the Windows service.")
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
    database_exists = (PROJECT_ROOT / "data" / "setuora.db").is_file()
    password = values.get("BOOTSTRAP_ADMIN_PASSWORD", "")
    if not database_exists and (password in UNSAFE_PASSWORDS or len(password) < 12):
        password = _prompt_secret("First administrator password")
        if len(password) < 12:
            raise DeploymentError(
                "The first administrator password must be at least 12 characters."
            )
        updates["BOOTSTRAP_ADMIN_PASSWORD"] = password
    computer_name = os.getenv("COMPUTERNAME", "").strip().lower()
    trusted_hosts = {"127.0.0.1", "localhost"}
    if computer_name:
        trusted_hosts.add(computer_name)
    updates.update(
        {
            "SETUORA_APP_MODE": "lite",
            "DATABASE_URL": "sqlite:///./data/setuora.db",
            "SESSION_COOKIE_SECURE": "false",
            "TRUSTED_HOSTS": ",".join(sorted(trusted_hosts)),
            "SETUORA_WEB_PORT": values.get("SETUORA_WEB_PORT") or "8000",
            "MASTER_SYNC_ENABLED": values.get("MASTER_SYNC_ENABLED") or "false",
            "SFTP_SYNC_ENABLED": "false",
        }
    )
    _write_env(updates)
    (PROJECT_ROOT / "data").mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "logs").mkdir(parents=True, exist_ok=True)


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


def _ensure_task() -> None:
    _task(
        "/Create",
        "/TN",
        TASK_NAME,
        "/SC",
        "ONSTART",
        "/RU",
        "SYSTEM",
        "/RL",
        "HIGHEST",
        "/TR",
        str(RUNNER_PATH),
        "/F",
    )


def _configure_private_firewall() -> None:
    _, values = _read_env()
    port = values.get("SETUORA_WEB_PORT", "8000")
    script = (
        f"$existing=Get-NetFirewallRule -Name '{FIREWALL_RULE_NAME}' "
        "-ErrorAction SilentlyContinue; if ($existing) { Remove-NetFirewallRule "
        f"-Name '{FIREWALL_RULE_NAME}' }}; New-NetFirewallRule -Name "
        f"'{FIREWALL_RULE_NAME}' -DisplayName 'Setuora Lite LAN' -Direction "
        f"Inbound -Action Allow -Protocol TCP -LocalPort {port} -Profile Private"
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


def _has_application_data() -> bool:
    return (PROJECT_ROOT / "data" / "setuora.db").is_file()


def preflight(_args: argparse.Namespace) -> None:
    _check_windows()
    if not ENV_PATH.exists():
        raise DeploymentError(".env is missing. Run `setuora.ps1 setup` first.")
    _, values = _read_env()
    issues = _environment_issues(
        values,
        has_application_data=_has_application_data(),
    )
    for required in (RUNNER_PATH, PROJECT_ROOT / "requirements-runtime.lock"):
        if not required.is_file():
            issues.append(f"Required deployment file is missing: {required.name}")
    if issues:
        raise DeploymentError("Preflight failed:\n- " + "\n- ".join(issues))
    print("Windows production configuration preflight passed without exposing secrets.")


def setup(_args: argparse.Namespace) -> None:
    _check_windows()
    _prepare_environment()
    _install_runtime()
    _configure_private_firewall()
    _ensure_task()
    _task("/Run", "/TN", TASK_NAME)
    _wait_for_health()
    _write_env({"BOOTSTRAP_ADMIN_PASSWORD": ""})
    host = os.getenv("COMPUTERNAME", "this-server")
    print("Setuora Lite is healthy on Windows.")
    print(f"Open http://{host}:8000 from the franchise private LAN.")
    print("Configure the Master HTTPS connection from Admin -> Master connection.")


def start(_args: argparse.Namespace) -> None:
    _check_windows()
    _task("/Run", "/TN", TASK_NAME)
    _wait_for_health()
    print("Setuora Lite is running.")


def stop(_args: argparse.Namespace) -> None:
    _check_windows()
    _task("/End", "/TN", TASK_NAME, check=False)
    print("Setuora Lite stopped. The database and Master event queue were preserved.")


def status(_args: argparse.Namespace) -> None:
    _check_windows()
    _task("/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST")
    print("Health: http://127.0.0.1:8000/health")


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
    stop(_args)
    _install_runtime()
    _configure_private_firewall()
    _ensure_task()
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
        ("update", update, "update dependencies and restart"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.set_defaults(function=function)
    logs_parser = subparsers.add_parser("logs", help="show the Windows server log")
    logs_parser.add_argument("--tail", type=int, default=200)
    logs_parser.add_argument("--follow", action="store_true")
    logs_parser.set_defaults(function=logs)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.function(args)
    except (DeploymentError, subprocess.CalledProcessError) as exc:
        print(f"Deployment failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
