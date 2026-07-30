"""Cross-platform Setuora Lite deployment helper.

Docker Compose owns the application, Caddy, and Tailscale processes.  This
script intentionally does not install host services, change the firewall, or
copy an existing workstation database into the production volume.
"""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess  # nosec B404
import sys
import time
import urllib.error
import urllib.request
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_PATH = PROJECT_ROOT / ".env"
ENV_EXAMPLE_PATH = PROJECT_ROOT / ".env.example"
COMPOSE_FILE = PROJECT_ROOT / "compose.yaml"
CADDYFILE = PROJECT_ROOT / "deployment" / "caddy" / "Caddyfile.container"
DEFAULT_CA_EXPORT_PATH = PROJECT_ROOT / "setuora-lite-root-ca.crt"
CADDY_CA_SOURCE = "caddy:/data/caddy/pki/authorities/local/root.crt"

APPLICATION_VOLUME = "setuora-lite_setuora-data"
TAILSCALE_VOLUME = "setuora-lite_tailscale-state"
CADDY_DATA_VOLUME = "setuora-lite_caddy-data"
CADDY_CONFIG_VOLUME = "setuora-lite_caddy-config"
ENROLLMENT_MARKER_PATH = "/srv/setuora/data/.setuora-lite-enrollment.json"
PERSISTENT_VOLUMES = (
    APPLICATION_VOLUME,
    TAILSCALE_VOLUME,
    CADDY_DATA_VOLUME,
    CADDY_CONFIG_VOLUME,
)

UNSAFE_PASSWORDS = {
    "",
    "admin123",
    "change-this-password",
    "change-this-before-first-start",
    "password",
    "setuora",
}
PLACEHOLDER_SECRETS = {
    "",
    "dev-change-me",
    "change-this-before-production",
    "replace-with-a-long-random-secret",
}
FRANCHISE_CODE_PLACEHOLDERS = {
    "",
    "CHANGE-ME",
    "CHANGEME",
    "DEFAULT",
    "FRANCHISE",
    "FRANCHISE-CODE",
    "LITE",
    "MASTER",
    "SETUORA",
    "YOUR-FRANCHISE",
    "YOUR-FRANCHISE-CODE",
}

PLAIN_ENV_VALUE = re.compile(r"^[A-Za-z0-9_./,:*?=@+%-]+$")
FRANCHISE_CODE = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$")
DNS_NAME = re.compile(
    r"^(?=.{1,253}\.?$)"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?$"
)
TAILSCALE_KEY = re.compile(r"^tskey-auth-[A-Za-z0-9_-]{8,}$")
MASTER_API_KEY = re.compile(r"^setuora-node\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{32,}$")
RFC1918_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)

LEGACY_DATABASE_CANDIDATES = (
    PROJECT_ROOT / "data" / "setu.db",
    PROJECT_ROOT / "data" / "setuora.db",
)

LOCAL_HEALTH_SCRIPT = """
import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3) as response:
    print(response.read().decode("utf-8"))
""".strip()

MASTER_NODE_SCRIPT = """
import json
import os
import urllib.request

proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
if not proxy:
    raise SystemExit("HTTPS proxy is not configured")
url = os.environ["MASTER_URL"].rstrip("/") + "/api/v1/node"
request = urllib.request.Request(
    url,
    headers={
        "Accept": "application/json",
        "Authorization": "Bearer " + os.environ["MASTER_API_KEY"],
    },
)
opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({"https": proxy}),
)
with opener.open(
    request,
    timeout=max(3, int(os.environ.get("MASTER_REQUEST_TIMEOUT_SECONDS", "15"))),
) as response:
    print(response.read().decode("utf-8"))
""".strip()

BASELINE_STATE_SCRIPT = """
import json
from sqlalchemy import func, select
from app.database import SessionLocal
from app.models import MasterOutboxEvent, MasterOutboxStatus
from app.services.master_sync import initial_inventory_is_queued

with SessionLocal() as db:
    queued = initial_inventory_is_queued(db)
    maximum = db.scalar(select(func.max(MasterOutboxEvent.id))) or 0
    last_sent = db.scalar(
        select(func.max(MasterOutboxEvent.id)).where(
            MasterOutboxEvent.status == MasterOutboxStatus.SENT.value
        )
    ) or 0
    unsent = db.scalar(
        select(func.count(MasterOutboxEvent.id)).where(
            MasterOutboxEvent.status != MasterOutboxStatus.SENT.value
        )
    ) or 0
print(json.dumps({
    "queued": queued,
    "max_sequence": maximum,
    "last_sent_sequence": last_sent,
    "unsent_count": unsent,
}))
""".strip()

INITIALIZE_BASELINE_SCRIPT = """
import json
from sqlalchemy import select
from app.database import SessionLocal
from app.models import MasterOutboxEvent
from app.services.master_sync import (
    enqueue_initial_inventory_snapshot,
    initial_inventory_is_queued,
    push_pending_events,
)

with SessionLocal() as db:
    if initial_inventory_is_queued(db):
        created = 0
        items = 0
    elif db.scalar(select(MasterOutboxEvent.id).limit(1)) is not None:
        raise SystemExit("outbox is not empty")
    else:
        created, items = enqueue_initial_inventory_snapshot(db, actor=None)
    sent = push_pending_events(db, limit=100000)
print(json.dumps({"created": created, "items": items, "sent": sent}))
""".strip()

READ_ENROLLMENT_MARKER_SCRIPT = f"""
import json
from pathlib import Path

path = Path({ENROLLMENT_MARKER_PATH!r})
if not path.exists():
    print("null")
else:
    print(path.read_text(encoding="utf-8"))
""".strip()

WRITE_ENROLLMENT_MARKER_SCRIPT = f"""
import json
import os
import sys
from pathlib import Path

path = Path({ENROLLMENT_MARKER_PATH!r})
state = sys.argv[1]
public_id = sys.argv[2].strip() if len(sys.argv) > 2 else ""
if state == "complete" and not public_id:
    raise SystemExit("complete marker requires Master public_id")
payload = {{
    "franchise_code": os.environ["FRANCHISE_CODE"],
    "state": state,
}}
if public_id:
    payload["master_public_id"] = public_id
temporary = path.with_suffix(".tmp")
temporary.write_text(
    json.dumps(payload, sort_keys=True) + "\\n",
    encoding="utf-8",
)
temporary.replace(path)
print(json.dumps(payload, sort_keys=True))
""".strip()


class DeploymentError(RuntimeError):
    """A safe, user-facing deployment failure."""


def _run(
    command: list[str],
    *,
    check: bool = True,
    capture: bool = False,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a caller-safe argument list without a shell."""

    return subprocess.run(  # nosec B603
        command,
        cwd=PROJECT_ROOT,
        check=check,
        text=True,
        capture_output=capture,
        shell=False,
        env=environment,
    )


def _compose_process_environment() -> dict[str, str]:
    """Prevent the caller's shell from overriding reviewed dotenv settings."""

    environment = dict(os.environ)
    with suppress(OSError):
        compose = COMPOSE_FILE.read_text(encoding="utf-8")
        for name in re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)", compose):
            environment.pop(name, None)
    for name in (
        "COMPOSE_DISABLE_ENV_FILE",
        "COMPOSE_ENV_FILES",
        "COMPOSE_PROJECT_NAME",
    ):
        environment.pop(name, None)
    return environment


def _compose(
    *arguments: str,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    return _run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), *arguments],
        check=check,
        capture=capture,
        environment=_compose_process_environment(),
    )


def _assignment_parts(raw_line: str) -> tuple[str, str, str, str | None] | None:
    """Return assignment prefix, key, raw value, and quote style.

    The prefix retains indentation, an optional ``export``, and whitespace
    around ``=``.  Inline comments remain part of the raw value and are split
    by ``_value_and_comment`` only when a setting is rewritten.
    """

    match = re.match(
        r"^(?P<prefix>\s*(?:export\s+)?)(?P<key>[A-Za-z_][A-Za-z0-9_]*)"
        r"(?P<equals>\s*=\s*)(?P<value>.*)$",
        raw_line,
    )
    if match is None:
        return None
    raw_value = match.group("value")
    stripped = raw_value.lstrip()
    quote = stripped[0] if stripped.startswith(("'", '"')) else None
    prefix = f"{match.group('prefix')}{match.group('key')}{match.group('equals')}"
    return prefix, match.group("key"), raw_value, quote


def _value_and_comment(raw_value: str) -> tuple[str, str]:
    """Split an env value from a shell-style inline comment."""

    quote: str | None = None
    escaped = False
    for index, character in enumerate(raw_value):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote == '"':
            escaped = True
            continue
        if quote:
            if character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
            continue
        if character == "#" and (index == 0 or raw_value[index - 1].isspace()):
            value = raw_value[:index].rstrip()
            whitespace = raw_value[len(value) : index]
            return value, f"{whitespace}{raw_value[index:]}"
    return raw_value.rstrip(), ""


def _decode_env_value(raw_value: str) -> str:
    value, _comment = _value_and_comment(raw_value.strip())
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        body = value[1:-1]
        output: list[str] = []
        escaped = False
        translations = {"n": "\n", "r": "\r", "t": "\t"}
        for character in body:
            if escaped:
                if character in translations:
                    output.append(translations[character])
                elif character in {'"', "\\"}:
                    output.append(character)
                else:
                    output.extend(("\\", character))
                escaped = False
            elif character == "\\":
                escaped = True
            else:
                output.append(character)
        if escaped:
            output.append("\\")
        return "".join(output)
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace(r"\'", "'")
    return value


def _read_env() -> tuple[list[str], dict[str, str]]:
    if not ENV_PATH.exists():
        return [], {}
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    for raw_line in lines:
        stripped = raw_line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = _assignment_parts(raw_line)
        if parts is None:
            continue
        _prefix, key, raw_value, _quote = parts
        values[key] = _decode_env_value(raw_value)
    return lines, values


def _env_assignment_values(key: str) -> list[str]:
    lines, _values = _read_env()
    found: list[str] = []
    for raw_line in lines:
        parts = _assignment_parts(raw_line)
        if parts is None or parts[1] != key:
            continue
        found.append(_decode_env_value(parts[2]))
    return found


def _format_env_value(value: str, *, preferred_quote: str | None = None) -> str:
    if "\n" in value or "\r" in value or "\x00" in value:
        raise DeploymentError(
            "Environment values cannot contain line breaks or NUL bytes."
        )
    if preferred_quote == "'":
        return "'" + value.replace("'", r"\'") + "'"
    if preferred_quote == '"' and "$" not in value:
        escaped = value.replace("\\", r"\\").replace('"', r"\"")
        return f'"{escaped}"'
    if value and PLAIN_ENV_VALUE.fullmatch(value):
        return value
    # Compose does not interpolate single-quoted dotenv values, which keeps a
    # password containing '$' literal.  Double quotes are easier to read for
    # the common spaces/#/quote case and remain compatible with prior files.
    if "$" in value:
        return "'" + value.replace("'", r"\'") + "'"
    escaped = value.replace("\\", r"\\").replace('"', r"\"")
    return f'"{escaped}"'


def _write_env(updates: dict[str, str]) -> None:
    """Update dotenv settings without reformatting unrelated content."""

    lines, _values = _read_env()
    written: set[str] = set()
    output: list[str] = []
    for raw_line in lines:
        parts = _assignment_parts(raw_line)
        if parts is None:
            output.append(raw_line)
            continue
        prefix, key, raw_value, quote = parts
        if key not in updates:
            output.append(raw_line)
            continue
        _old_value, inline_comment = _value_and_comment(raw_value)
        formatted = _format_env_value(updates[key], preferred_quote=quote)
        output.append(f"{prefix}{formatted}{inline_comment}")
        written.add(key)

    pending = {key: value for key, value in updates.items() if key not in written}
    if pending:
        if output and output[-1]:
            output.append("")
        output.extend(
            f"{key}={_format_env_value(value)}" for key, value in pending.items()
        )

    ENV_PATH.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    with suppress(OSError):
        ENV_PATH.chmod(0o600)


def _check_prerequisites() -> None:
    if not COMPOSE_FILE.is_file():
        raise DeploymentError(f"Docker Compose file is missing: {COMPOSE_FILE}")
    if shutil.which("docker") is None:
        raise DeploymentError(
            "Docker was not found. Install Docker Engine (Linux) or Docker "
            "Desktop (Windows), then run this command again."
        )
    compose = _run(["docker", "compose", "version"], check=False, capture=True)
    if compose.returncode:
        raise DeploymentError("Docker Compose v2 is required (`docker compose`).")
    engine = _run(["docker", "info"], check=False, capture=True)
    if engine.returncode:
        raise DeploymentError("Docker is installed but its engine is not running.")


def _has_named_volume(name: str) -> bool:
    result = _run(
        ["docker", "volume", "inspect", name],
        check=False,
        capture=True,
    )
    return result.returncode == 0


def _volume_state() -> dict[str, bool]:
    return {name: _has_named_volume(name) for name in PERSISTENT_VOLUMES}


def _volume_state_issues(state: dict[str, bool]) -> list[str]:
    present = {name for name, exists in state.items() if exists}
    if not present or len(present) == len(PERSISTENT_VOLUMES):
        return []
    missing = [name for name in PERSISTENT_VOLUMES if not state.get(name, False)]
    if not state.get(APPLICATION_VOLUME, False):
        return [
            (
                "A partial deployment has persistent identity volumes but no "
                f"{APPLICATION_VOLUME} volume. Review the volumes before setup; "
                "the helper will not delete them."
            )
        ]
    return [
        (
            "The deployment has an application volume but is missing persistent "
            f"volume(s): {', '.join(missing)}. Restore or explicitly review the "
            "deployment before continuing."
        )
    ]


def _legacy_database_paths() -> list[Path]:
    paths: list[Path] = []
    for candidate in LEGACY_DATABASE_CANDIDATES:
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                paths.append(candidate)
        except OSError:
            continue
    return paths


def _block_unmigrated_local_database(*, has_application_data: bool) -> None:
    legacy_paths = _legacy_database_paths()
    if not legacy_paths:
        return
    display = ", ".join(
        str(path.relative_to(PROJECT_ROOT))
        if path.is_relative_to(PROJECT_ROOT)
        else str(path)
        for path in legacy_paths
    )
    raise DeploymentError(
        "Existing local database detected "
        f"({display}). Automatic migration into the self-hosted volume is not "
        "implemented, and a named volume alone is not proof that it was "
        "migrated. No data was changed or imported. Back it up, move the "
        "workstation copy out of the project, and arrange a reviewed franchise "
        "baseline cutover before running setup."
    )


def _normalize_franchise_code(value: str) -> str:
    raw = str(value or "").strip().upper()
    return re.sub(r"[^A-Z0-9]+", "-", raw).strip("-")


def _franchise_code_issue(value: str) -> str | None:
    if not value or value in FRANCHISE_CODE_PLACEHOLDERS:
        return "FRANCHISE_CODE must be a permanent, unique non-placeholder code."
    if len(value) > 40:
        return "FRANCHISE_CODE must be 40 characters or fewer."
    if FRANCHISE_CODE.fullmatch(value) is None:
        return "FRANCHISE_CODE may contain only A-Z, 0-9, and single hyphens."
    return None


def _private_ipv4(value: str) -> ipaddress.IPv4Address | None:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if not isinstance(address, ipaddress.IPv4Address):
        return None
    if (
        not any(address in network for network in RFC1918_NETWORKS)
        or address.is_loopback
        or address.is_unspecified
        or address.is_link_local
        or address.is_multicast
    ):
        return None
    return address


def _lan_hostname_issue(value: str, bind_address: str) -> str | None:
    hostname = value.strip().rstrip(".")
    if (
        not hostname
        or "*" in hostname
        or "," in hostname
        or "://" in hostname
        or "/" in hostname
        or "\\" in hostname
    ):
        return (
            "SETUORA_LAN_HOSTNAME must be one exact hostname or private IPv4 address."
        )
    try:
        parsed_ip = ipaddress.ip_address(hostname)
    except ValueError:
        parsed_ip = None
    if parsed_ip is not None:
        if _private_ipv4(hostname) is None:
            return "SETUORA_LAN_HOSTNAME cannot be a public, loopback, or wildcard address."
        if hostname != bind_address:
            return (
                "When SETUORA_LAN_HOSTNAME is an IP address, it must exactly match "
                "SETUORA_LAN_BIND_ADDRESS."
            )
        return None
    if hostname.lower() == "localhost" or DNS_NAME.fullmatch(hostname) is None:
        return "SETUORA_LAN_HOSTNAME must be an exact LAN DNS name or private IPv4 address."
    return None


def _master_url_issue(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return "MASTER_URL must be an exact https://*.ts.net URL."
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not hostname.endswith(".ts.net")
        or hostname == "ts.net"
        or "*" in hostname
        or parsed.username
        or parsed.password
        or port is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        return (
            "MASTER_URL must be an exact https://*.ts.net URL without a path or port."
        )
    if DNS_NAME.fullmatch(hostname) is None:
        return "MASTER_URL contains an invalid MagicDNS hostname."
    return None


def _tailscale_hostname_issue(value: str) -> str | None:
    hostname = value.strip().lower()
    if (
        not hostname
        or len(hostname) > 63
        or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", hostname) is None
    ):
        return "TAILSCALE_HOSTNAME must be one DNS label of 63 characters or fewer."
    return None


def _normalized_master_url(value: str) -> str:
    issue = _master_url_issue(value.strip())
    if issue:
        raise DeploymentError(issue)
    return f"https://{urlsplit(value.strip()).hostname.lower().rstrip('.')}"


def _port_issue(values: dict[str, str], name: str, default: str) -> str | None:
    try:
        port = int(values.get(name, default))
    except (TypeError, ValueError):
        port = 0
    if not 1 <= port <= 65535:
        return f"{name} must be a number from 1 to 65535."
    return None


def _environment_issues(
    values: dict[str, str],
    *,
    has_application_data: bool,
    has_tailnet_identity: bool,
) -> list[str]:
    issues: list[str] = []
    app_secret = values.get("APP_SECRET_KEY", "")
    if app_secret in PLACEHOLDER_SECRETS or len(app_secret) < 32:
        issues.append("APP_SECRET_KEY must contain at least 32 random characters.")

    password = values.get("BOOTSTRAP_ADMIN_PASSWORD", "")
    if not has_application_data and (
        password.strip().lower() in UNSAFE_PASSWORDS or len(password) < 12
    ):
        issues.append(
            "BOOTSTRAP_ADMIN_PASSWORD must be unique and at least 12 characters."
        )

    raw_code = values.get("FRANCHISE_CODE", "")
    code_issue = _franchise_code_issue(raw_code)
    if code_issue:
        issues.append(code_issue)

    bind_address = values.get("SETUORA_LAN_BIND_ADDRESS", "")
    if _private_ipv4(bind_address) is None:
        issues.append(
            "SETUORA_LAN_BIND_ADDRESS must be one exact private, non-loopback IPv4 address."
        )
    hostname_issue = _lan_hostname_issue(
        values.get("SETUORA_LAN_HOSTNAME", ""),
        bind_address,
    )
    if hostname_issue:
        issues.append(hostname_issue)

    master_url_issue = _master_url_issue(values.get("MASTER_URL", ""))
    if master_url_issue:
        issues.append(master_url_issue)
    if MASTER_API_KEY.fullmatch(values.get("MASTER_API_KEY", "")) is None:
        issues.append("MASTER_API_KEY must use the setuora-node.<id>.<secret> format.")

    tailscale_key = values.get("TAILSCALE_AUTH_KEY", "")
    if tailscale_key and TAILSCALE_KEY.fullmatch(tailscale_key) is None:
        issues.append("TAILSCALE_AUTH_KEY has an unsupported format.")
    if not tailscale_key and not has_tailnet_identity:
        issues.append("Provide a one-off TAILSCALE_AUTH_KEY for first enrollment.")
    if values.get("TAILSCALE_TAG", "") != "tag:setuora-lite":
        issues.append("TAILSCALE_TAG must be exactly tag:setuora-lite.")
    tailscale_hostname_issue = _tailscale_hostname_issue(
        values.get("TAILSCALE_HOSTNAME", "")
    )
    if tailscale_hostname_issue:
        issues.append(tailscale_hostname_issue)
    elif values.get("TAILSCALE_HOSTNAME", "").strip().lower() == "setuora-lite":
        issues.append("TAILSCALE_HOSTNAME must include the permanent franchise code.")

    required_flags = {
        "SETUORA_APP_MODE": "lite",
        "MASTER_SYNC_ENABLED": "true",
        "MASTER_TLS_VERIFY": "true",
        "SESSION_COOKIE_SECURE": "true",
        "AUTOMATIC_BACKUPS_ENABLED": "true",
    }
    for name, required in required_flags.items():
        if values.get(name, "").strip().lower() != required:
            issues.append(f"{name} must be {required}.")

    trusted_hosts = {
        host.strip()
        for host in values.get("TRUSTED_HOSTS", "").split(",")
        if host.strip()
    }
    required_hosts = {
        "localhost",
        "127.0.0.1",
        bind_address,
        values.get("SETUORA_LAN_HOSTNAME", "").strip().rstrip("."),
    }
    if any("*" in host for host in trusted_hosts):
        issues.append("TRUSTED_HOSTS must not contain wildcard hosts.")
    if trusted_hosts != required_hosts:
        issues.append(
            "TRUSTED_HOSTS must contain only localhost, 127.0.0.1, and the exact LAN host/IP."
        )

    for name, default in (
        ("SETUORA_HTTP_PORT", "80"),
        ("SETUORA_HTTPS_PORT", "443"),
    ):
        issue = _port_issue(values, name, default)
        if issue:
            issues.append(issue)
    try:
        same_port = int(values.get("SETUORA_HTTP_PORT", "80")) == int(
            values.get("SETUORA_HTTPS_PORT", "443")
        )
    except ValueError:
        same_port = False
    if same_port:
        issues.append("SETUORA_HTTP_PORT and SETUORA_HTTPS_PORT must be different.")

    try:
        retention = int(values.get("BACKUP_RETENTION_COUNT", "14"))
    except ValueError:
        retention = 0
    if retention < 2:
        issues.append("BACKUP_RETENTION_COUNT must be at least 2.")
    try:
        interval = int(values.get("BACKUP_INTERVAL_HOURS", "24"))
    except ValueError:
        interval = 0
    if interval < 1:
        issues.append("BACKUP_INTERVAL_HOURS must be at least 1.")
    return issues


def _compose_contract_issues() -> list[str]:
    try:
        compose = COMPOSE_FILE.read_text(encoding="utf-8")
    except OSError:
        return ["compose.yaml could not be read."]
    required_fragments = {
        "the Setuora application service": "\n  setuora:",
        "the Caddy HTTPS service": "\n  caddy:",
        "the Tailscale egress service": "\n  tailscale:",
        "the outbound HTTPS proxy": "HTTPS_PROXY: http://tailscale:1055",
        "the lower-case outbound HTTPS proxy": "https_proxy: http://tailscale:1055",
        "the Tailscale HTTP proxy listener": "TS_OUTBOUND_HTTP_PROXY_LISTEN: :1055",
        "the outbound proxy exclusion list": (
            "NO_PROXY: 127.0.0.1,localhost,setuora,caddy,tailscale"
        ),
        "the lower-case proxy exclusion list": (
            "no_proxy: 127.0.0.1,localhost,setuora,caddy,tailscale"
        ),
        "the persistent application volume": "setuora-data:/srv/setuora/data",
        "the persistent backup directory": "BACKUP_DIRECTORY: /srv/setuora/data/backups",
        "the persistent backup settings file": (
            "BACKUP_SETTINGS_FILE: /srv/setuora/data/backup-settings.env"
        ),
        "fixed container deployment mode": 'SETUORA_CONTAINER_DEPLOYMENT: "true"',
        "the persistent Tailscale identity": "tailscale-state:/var/lib/tailscale",
        "the persistent Caddy PKI": "caddy-data:/data",
        "the private application network": "app-network:\n    internal: true",
        "the private sync network": "sync-network:\n    internal: true",
        "secure session cookies": 'SESSION_COOKIE_SECURE: "true"',
        "strict Master TLS verification": 'MASTER_TLS_VERIFY: "true"',
    }
    issues = [
        f"compose.yaml is missing {description}."
        for description, fragment in required_fragments.items()
        if fragment not in compose
    ]
    try:
        caddyfile = CADDYFILE.read_text(encoding="utf-8")
    except OSError:
        issues.append("The container Caddyfile could not be read.")
        return issues
    for description, fragment in {
        "internal TLS": "tls internal",
        "the private application reverse proxy": "reverse_proxy setuora:8000",
        "read-only-container trust behavior": "skip_install_trust",
    }.items():
        if fragment not in caddyfile:
            issues.append(f"The container Caddyfile is missing {description}.")
    return issues


def _prompt_secret(label: str) -> str:
    if not sys.stdin.isatty():
        raise DeploymentError(
            f"{label} is missing. Set it in .env before running setup non-interactively."
        )
    first = getpass.getpass(f"{label}: ").strip()
    second = getpass.getpass(f"Confirm {label}: ").strip()
    if first != second:
        raise DeploymentError(f"{label} values did not match.")
    return first


def _prompt_value(label: str, *, default: str = "") -> str:
    if not sys.stdin.isatty():
        raise DeploymentError(
            f"{label} is missing. Set it in .env before running setup non-interactively."
        )
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def _discover_private_ipv4_addresses() -> list[str]:
    addresses: set[str] = set()
    with suppress(OSError):
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = result[4][0]
            if _private_ipv4(address):
                addresses.add(address)
    # A UDP connect selects a route without sending application data.
    with suppress(OSError):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 9))
            address = probe.getsockname()[0]
            if _private_ipv4(address):
                addresses.add(address)
        finally:
            probe.close()
    return sorted(addresses, key=ipaddress.ip_address)


def _prepare_environment(
    *,
    has_application_data: bool,
    has_tailnet_identity: bool,
) -> None:
    if not ENV_PATH.exists():
        if not ENV_EXAMPLE_PATH.is_file():
            raise DeploymentError(".env.example is missing.")
        shutil.copyfile(ENV_EXAMPLE_PATH, ENV_PATH)
        with suppress(OSError):
            ENV_PATH.chmod(0o600)

    _lines, values = _read_env()
    updates: dict[str, str] = {}

    app_secret = values.get("APP_SECRET_KEY", "")
    if app_secret in PLACEHOLDER_SECRETS or len(app_secret) < 32:
        app_secret = secrets.token_urlsafe(48)
    updates["APP_SECRET_KEY"] = app_secret

    password = values.get("BOOTSTRAP_ADMIN_PASSWORD", "")
    if not has_application_data and (
        password.strip().lower() in UNSAFE_PASSWORDS or len(password) < 12
    ):
        password = _prompt_secret("First administrator password")
        if password.strip().lower() in UNSAFE_PASSWORDS or len(password) < 12:
            raise DeploymentError(
                "The first administrator password must be unique and at least "
                "12 characters."
            )
    if password:
        updates["BOOTSTRAP_ADMIN_PASSWORD"] = password

    raw_code = values.get("FRANCHISE_CODE", "")
    code = _normalize_franchise_code(raw_code)
    if _franchise_code_issue(code):
        code = _normalize_franchise_code(
            _prompt_value("Permanent franchise code (A-Z, 0-9, hyphens)")
        )
    code_issue = _franchise_code_issue(code)
    if code_issue:
        raise DeploymentError(code_issue)
    if code != raw_code:
        updates["FRANCHISE_CODE"] = code

    bind_address = values.get("SETUORA_LAN_BIND_ADDRESS", "").strip()
    if _private_ipv4(bind_address) is None:
        candidates = _discover_private_ipv4_addresses()
        default_address = candidates[0] if len(candidates) == 1 else ""
        bind_address = _prompt_value(
            "Exact private IPv4 address to bind on this LAN",
            default=default_address,
        )
    if _private_ipv4(bind_address) is None:
        raise DeploymentError(
            "SETUORA_LAN_BIND_ADDRESS must be one exact private, non-loopback IPv4 address."
        )
    updates["SETUORA_LAN_BIND_ADDRESS"] = bind_address

    lan_hostname = values.get("SETUORA_LAN_HOSTNAME", "").strip().rstrip(".")
    if _lan_hostname_issue(lan_hostname, bind_address):
        lan_hostname = _prompt_value(
            "Exact LAN hostname or private IPv4 address for HTTPS",
            default=bind_address,
        ).rstrip(".")
    hostname_issue = _lan_hostname_issue(lan_hostname, bind_address)
    if hostname_issue:
        raise DeploymentError(hostname_issue)
    updates["SETUORA_LAN_HOSTNAME"] = lan_hostname

    master_url = values.get("MASTER_URL", "").strip()
    if _master_url_issue(master_url):
        master_url = _prompt_value("Exact Setuora Master https://*.ts.net URL")
    updates["MASTER_URL"] = _normalized_master_url(master_url)

    master_api_key = values.get("MASTER_API_KEY", "").strip()
    if MASTER_API_KEY.fullmatch(master_api_key) is None:
        master_api_key = _prompt_secret("Setuora Master node API key")
    if MASTER_API_KEY.fullmatch(master_api_key) is None:
        raise DeploymentError(
            "MASTER_API_KEY must use the setuora-node.<id>.<secret> format."
        )
    updates["MASTER_API_KEY"] = master_api_key

    tailscale_key = values.get("TAILSCALE_AUTH_KEY", "").strip()
    if not has_tailnet_identity and not tailscale_key:
        tailscale_key = _prompt_secret(
            "One-off, pre-authorized Tailscale key tagged tag:setuora-lite"
        )
    if tailscale_key and TAILSCALE_KEY.fullmatch(tailscale_key) is None:
        raise DeploymentError("That does not look like a supported Tailscale auth key.")
    if tailscale_key:
        updates["TAILSCALE_AUTH_KEY"] = tailscale_key

    trusted_hosts = {
        "localhost",
        "127.0.0.1",
        bind_address,
        lan_hostname,
    }
    tailscale_hostname = values.get("TAILSCALE_HOSTNAME", "").strip().lower()
    if (
        _tailscale_hostname_issue(tailscale_hostname)
        or tailscale_hostname == "setuora-lite"
    ):
        tailscale_hostname = f"setuora-lite-{code.lower()}"[:63].rstrip("-")
    updates.update(
        {
            "APP_NAME": values.get("APP_NAME", "").strip() or "Setuora Lite",
            "AUTOMATIC_BACKUPS_ENABLED": "true",
            "BACKUP_INTERVAL_HOURS": values.get("BACKUP_INTERVAL_HOURS", "24") or "24",
            "BACKUP_RETENTION_COUNT": values.get("BACKUP_RETENTION_COUNT", "14")
            or "14",
            "MASTER_SYNC_ENABLED": "true",
            "MASTER_TLS_VERIFY": "true",
            "SESSION_COOKIE_SECURE": "true",
            "SETUORA_APP_MODE": "lite",
            "SETUORA_HTTP_PORT": values.get("SETUORA_HTTP_PORT", "80") or "80",
            "SETUORA_HTTPS_PORT": values.get("SETUORA_HTTPS_PORT", "443") or "443",
            "TAILSCALE_HOSTNAME": tailscale_hostname,
            "TAILSCALE_TAG": "tag:setuora-lite",
            "TRUSTED_HOSTS": ",".join(sorted(trusted_hosts)),
        }
    )
    _write_env(updates)


def _validate_configuration(
    *,
    volume_state: dict[str, bool] | None = None,
) -> None:
    if not ENV_PATH.exists():
        raise DeploymentError(".env is missing. Run `python deploy.py setup` first.")
    state = volume_state or _volume_state()
    _lines, values = _read_env()
    issues = _environment_issues(
        values,
        has_application_data=state.get(APPLICATION_VOLUME, False),
        has_tailnet_identity=state.get(TAILSCALE_VOLUME, False),
    )
    issues.extend(_volume_state_issues(state))
    issues.extend(_compose_contract_issues())
    compose = _compose("config", "--quiet", check=False, capture=True)
    if compose.returncode:
        issues.append("Docker Compose configuration is invalid.")
    if issues:
        raise DeploymentError("Preflight failed:\n- " + "\n- ".join(issues))


def _json_from_output(output: str, *, description: str) -> Any:
    for line in reversed(output.splitlines()):
        try:
            return json.loads(line)
        except (TypeError, ValueError):
            continue
    raise DeploymentError(f"{description} returned an invalid JSON response.")


def _wait_for_local_health(timeout_seconds: int = 120) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = _compose(
            "exec",
            "-T",
            "setuora",
            "python",
            "-c",
            LOCAL_HEALTH_SCRIPT,
            check=False,
            capture=True,
        )
        if result.returncode == 0:
            try:
                payload = _json_from_output(
                    result.stdout, description="Local health check"
                )
            except DeploymentError:
                payload = None
            if payload == {"status": "ok", "role": "lite"}:
                return
        time.sleep(2)
    raise DeploymentError(
        "Setuora did not become healthy inside its container. "
        "Run `python deploy.py logs setuora`."
    )


def _export_ca_file(
    destination: Path,
    *,
    timeout_seconds: int = 60,
) -> Path:
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = _compose(
            "cp",
            CADDY_CA_SOURCE,
            str(destination),
            check=False,
            capture=True,
        )
        if result.returncode == 0 and destination.is_file():
            with suppress(OSError):
                destination.chmod(0o644)
            return destination
        time.sleep(2)
    raise DeploymentError(
        "Caddy's public root CA was not available. Check `python deploy.py logs caddy`."
    )


def _lan_health_url(values: dict[str, str]) -> str:
    hostname = values["SETUORA_LAN_HOSTNAME"]
    port = int(values.get("SETUORA_HTTPS_PORT", "443"))
    authority = hostname if port == 443 else f"{hostname}:{port}"
    return f"https://{authority}/health"


def _wait_for_lan_https_health(
    ca_path: Path = DEFAULT_CA_EXPORT_PATH,
    timeout_seconds: int = 120,
) -> None:
    _lines, values = _read_env()
    url = _lan_health_url(values)
    context = ssl.create_default_context(cafile=str(ca_path))
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context),
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=4) as response:
                payload = json.load(response)
            if payload == {"status": "ok", "role": "lite"}:
                return
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(2)
    raise DeploymentError(
        f"Setuora did not become healthy through LAN HTTPS at {url}. "
        "Confirm the exact LAN hostname/IP and Docker port binding."
    )


def _tailscale_status() -> dict[str, Any] | None:
    result = _compose(
        "exec",
        "-T",
        "tailscale",
        "tailscale",
        "status",
        "--json",
        check=False,
        capture=True,
    )
    if result.returncode:
        return None
    try:
        payload = json.loads(result.stdout)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _tailscale_online(payload: dict[str, Any] | None) -> bool:
    if not payload or payload.get("BackendState") != "Running":
        return False
    self_status = payload.get("Self")
    if not isinstance(self_status, dict) or self_status.get("Online") is not True:
        return False
    tags = self_status.get("Tags")
    return isinstance(tags, list) and "tag:setuora-lite" in tags


def _wait_for_tailscale(timeout_seconds: int = 120) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        payload = _tailscale_status()
        if _tailscale_online(payload):
            return payload
        time.sleep(2)
    raise DeploymentError(
        "Tailscale did not enroll as an online tag:setuora-lite node. Confirm "
        "the key is one-off, pre-authorized, non-ephemeral, and owns that tag."
    )


def _clear_bootstrap_credentials() -> None:
    updates: dict[str, str] = {}
    if any(_env_assignment_values("TAILSCALE_AUTH_KEY")):
        updates["TAILSCALE_AUTH_KEY"] = ""
    if any(_env_assignment_values("BOOTSTRAP_ADMIN_PASSWORD")):
        updates["BOOTSTRAP_ADMIN_PASSWORD"] = ""
    if not updates:
        return
    _write_env(updates)
    # Recreate both containers so bootstrap credentials also disappear from
    # their process environments after the persistent identities are proven.
    _compose(
        "up",
        "-d",
        "--no-deps",
        "--force-recreate",
        "tailscale",
        "setuora",
    )
    _wait_for_local_health()
    _wait_for_tailscale()


def _master_node() -> dict[str, Any]:
    result = _compose(
        "exec",
        "-T",
        "setuora",
        "python",
        "-c",
        MASTER_NODE_SCRIPT,
        check=False,
        capture=True,
    )
    if result.returncode:
        raise DeploymentError(
            "Setuora Lite could not authenticate to Master through its "
            "Tailscale HTTPS proxy. No credential was printed."
        )
    payload = _json_from_output(result.stdout, description="Master node check")
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise DeploymentError("Master returned a malformed GET /api/v1/node response.")
    _lines, values = _read_env()
    expected = values.get("FRANCHISE_CODE", "")
    returned = payload["data"].get("code")
    if returned != expected:
        raise DeploymentError(
            "Master authenticated a different franchise code "
            f"(expected {expected!r}, returned {returned!r}). Correct the node "
            "credential before any synchronization."
        )
    return payload["data"]


def _baseline_state() -> dict[str, Any]:
    result = _compose(
        "exec",
        "-T",
        "setuora",
        "python",
        "-c",
        BASELINE_STATE_SCRIPT,
        check=False,
        capture=True,
    )
    if result.returncode:
        raise DeploymentError("The Lite baseline state could not be inspected.")
    payload = _json_from_output(result.stdout, description="Lite baseline check")
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("queued"), bool)
        or not isinstance(payload.get("max_sequence"), int)
        or not isinstance(payload.get("last_sent_sequence"), int)
        or not isinstance(payload.get("unsent_count"), int)
    ):
        raise DeploymentError("The Lite baseline check returned malformed state.")
    return payload


def _enrollment_marker() -> dict[str, str] | None:
    result = _compose(
        "exec",
        "-T",
        "setuora",
        "python",
        "-c",
        READ_ENROLLMENT_MARKER_SCRIPT,
        check=False,
        capture=True,
    )
    if result.returncode:
        raise DeploymentError(
            "The deployment enrollment marker could not be inspected."
        )
    payload = _json_from_output(result.stdout, description="Enrollment marker check")
    if payload is None:
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("state") not in {"pending", "complete"}
        or not isinstance(payload.get("franchise_code"), str)
    ):
        raise DeploymentError(
            "The deployment enrollment marker is malformed. Review the "
            "application volume before continuing."
        )
    marker = {
        "state": payload["state"],
        "franchise_code": payload["franchise_code"],
    }
    public_id = payload.get("master_public_id")
    if payload["state"] == "complete" and (
        not isinstance(public_id, str) or not public_id.strip()
    ):
        raise DeploymentError(
            "The completed deployment marker is missing its Master node identity."
        )
    if isinstance(public_id, str) and public_id.strip():
        marker["master_public_id"] = public_id.strip()
    return marker


def _write_enrollment_marker(
    state: str,
    *,
    master_public_id: str = "",
) -> None:
    if state not in {"pending", "complete"}:
        raise DeploymentError("Unsupported deployment enrollment marker state.")
    public_id = master_public_id.strip()
    if state == "complete" and not public_id:
        raise DeploymentError(
            "A completed deployment marker requires the Master node public ID."
        )
    result = _compose(
        "exec",
        "-T",
        "setuora",
        "python",
        "-c",
        WRITE_ENROLLMENT_MARKER_SCRIPT,
        state,
        public_id,
        check=False,
        capture=True,
    )
    if result.returncode:
        raise DeploymentError(
            "The deployment enrollment marker could not be persisted."
        )


def _seed_pending_enrollment_marker() -> None:
    """Mark a helper-created volume before waiting on application health."""

    result = _compose(
        "run",
        "--rm",
        "--no-deps",
        "setuora",
        "python",
        "-c",
        WRITE_ENROLLMENT_MARKER_SCRIPT,
        "pending",
        check=False,
        capture=True,
    )
    if result.returncode:
        raise DeploymentError(
            "The new application volume could not be marked for resumable "
            "enrollment. No legacy data was imported."
        )


def _pending_helper_enrollment(*, created_application_volume: bool) -> bool:
    _lines, values = _read_env()
    expected_code = values.get("FRANCHISE_CODE", "")
    marker = _enrollment_marker()
    if marker is None:
        if created_application_volume:
            raise DeploymentError(
                "The new application volume is missing its enrollment marker."
            )
        return False
    if marker["franchise_code"] != expected_code:
        raise DeploymentError(
            "The persistent deployment marker belongs to franchise "
            f"{marker['franchise_code']!r}, not configured franchise "
            f"{expected_code!r}. FRANCHISE_CODE is permanent."
        )
    return marker["state"] == "pending"


def _ensure_complete_marker_identity(
    node: dict[str, Any],
    *,
    allow_create: bool,
) -> None:
    _lines, values = _read_env()
    expected_code = values.get("FRANCHISE_CODE", "")
    public_id = node.get("public_id")
    if not isinstance(public_id, str) or not public_id.strip():
        raise DeploymentError("Master returned a node without a public identity.")
    marker = _enrollment_marker()
    if marker is None:
        if not allow_create:
            raise DeploymentError(
                "The deployment has no completed Master identity marker. "
                "Run `python deploy.py setup`."
            )
        _write_enrollment_marker(
            "complete",
            master_public_id=public_id,
        )
        return
    if marker["franchise_code"] != expected_code:
        raise DeploymentError(
            "The persistent deployment marker belongs to a different "
            "FRANCHISE_CODE. The franchise identity is permanent."
        )
    if marker["state"] != "complete":
        raise DeploymentError(
            "The first deployment enrollment is still pending. "
            "Run `python deploy.py setup`."
        )
    if marker.get("master_public_id") != public_id:
        raise DeploymentError(
            "Master authenticated a different node identity with the same "
            "franchise code. Restore the originally bound credential."
        )


def _ensure_pending_marker_identity(node: dict[str, Any]) -> None:
    public_id = node.get("public_id")
    if not isinstance(public_id, str) or not public_id.strip():
        raise DeploymentError("Master returned a node without a public identity.")
    marker = _enrollment_marker()
    if marker is None or marker["state"] != "pending":
        raise DeploymentError("The resumable enrollment marker is missing.")
    bound_public_id = marker.get("master_public_id")
    if bound_public_id and bound_public_id != public_id:
        raise DeploymentError(
            "The pending enrollment is already bound to a different Master "
            "node identity."
        )
    if not bound_public_id:
        _write_enrollment_marker(
            "pending",
            master_public_id=public_id,
        )


def _initialize_new_application() -> int:
    result = _compose(
        "exec",
        "-T",
        "setuora",
        "python",
        "-c",
        INITIALIZE_BASELINE_SCRIPT,
        check=False,
        capture=True,
    )
    if result.returncode:
        raise DeploymentError(
            "The initial inventory baseline could not be queued and synchronized. "
            "No existing data was imported or changed by the deployment helper."
        )
    state = _baseline_state()
    if not state["queued"] or state["max_sequence"] < 1:
        raise DeploymentError("The initial inventory baseline was not durably queued.")
    return state["max_sequence"]


def _wait_for_master_cursor(
    expected_sequence: int,
    timeout_seconds: int = 90,
    *,
    bound_master_public_id: str = "",
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        local = _baseline_state()
        if local["max_sequence"] != expected_sequence:
            raise DeploymentError(
                "Lite's outbox changed while the initial baseline was being "
                "verified. Stop franchise activity and review the enrollment."
            )
        node = _master_node()
        if bound_master_public_id and node.get("public_id") != bound_master_public_id:
            raise DeploymentError(
                "Master node identity changed while the initial baseline was "
                "being acknowledged."
            )
        last_sequence = node.get("last_sequence")
        next_sequence = node.get("next_sequence")
        if isinstance(last_sequence, int) and last_sequence > expected_sequence:
            raise DeploymentError(
                "Master's cursor is ahead of Lite's initial outbox. Review the "
                "node identity before synchronization."
            )
        if (
            local["unsent_count"] == 0
            and local["last_sent_sequence"] == expected_sequence
            and last_sequence == expected_sequence
            and next_sequence == expected_sequence + 1
        ):
            return node
        time.sleep(2)
    raise DeploymentError(
        "Master authenticated this franchise but did not acknowledge the "
        f"initial Lite sequence {expected_sequence}."
    )


def _verify_established_master_state(
    *,
    allow_marker_creation: bool,
) -> tuple[dict[str, Any], int]:
    """Verify the bound node and its exact acknowledged Lite prefix."""

    for attempt in range(5):
        before = _baseline_state()
        if not before["queued"]:
            raise DeploymentError(
                "The franchise-bound INITIAL_INVENTORY marker is missing."
            )
        node = _master_node()
        after = _baseline_state()
        comparable_fields = (
            "max_sequence",
            "last_sent_sequence",
            "unsent_count",
        )
        if any(before[field] != after[field] for field in comparable_fields):
            if attempt < 4:
                time.sleep(1)
                continue
            raise DeploymentError(
                "Lite's outbox changed repeatedly during cursor verification. "
                "Retry after the current sync cycle."
            )

        last_sent = after["last_sent_sequence"]
        master_last = node.get("last_sequence")
        master_next = node.get("next_sequence")
        if master_last == last_sent and master_next == last_sent + 1:
            _ensure_complete_marker_identity(
                node,
                allow_create=allow_marker_creation,
            )
            return node, after["unsent_count"]
        if (
            isinstance(master_last, int)
            and master_last > last_sent
            and after["unsent_count"] > 0
            and attempt < 4
        ):
            # Master may have committed the in-flight event just before Lite
            # records the acknowledgement locally.
            time.sleep(1)
            continue
        direction = (
            "ahead of"
            if isinstance(master_last, int) and master_last > last_sent
            else "behind"
        )
        raise DeploymentError(
            f"Master's cursor is {direction} Lite's last SENT sequence "
            f"(Master {master_last!r}, Lite {last_sent})."
        )
    raise DeploymentError("Master cursor verification did not stabilize.")


def preflight(_args: argparse.Namespace) -> None:
    _check_prerequisites()
    state = _volume_state()
    _block_unmigrated_local_database(
        has_application_data=state.get(APPLICATION_VOLUME, False)
    )
    _validate_configuration(volume_state=state)
    print("Production configuration preflight passed without exposing secrets.")


def setup(_args: argparse.Namespace) -> None:
    _check_prerequisites()
    state_before = _volume_state()
    state_issues = _volume_state_issues(state_before)
    if state_issues:
        raise DeploymentError("Preflight failed:\n- " + "\n- ".join(state_issues))
    had_application_data = state_before.get(APPLICATION_VOLUME, False)
    _block_unmigrated_local_database(has_application_data=had_application_data)
    _prepare_environment(
        has_application_data=had_application_data,
        has_tailnet_identity=state_before.get(TAILSCALE_VOLUME, False),
    )
    _validate_configuration(volume_state=state_before)

    _compose("up", "-d", "--build", "--remove-orphans")
    if not had_application_data:
        _seed_pending_enrollment_marker()
    _wait_for_local_health()
    enrollment_pending = _pending_helper_enrollment(
        created_application_volume=not had_application_data
    )
    ca_path = _export_ca_file(DEFAULT_CA_EXPORT_PATH)
    _wait_for_lan_https_health(ca_path)
    _wait_for_tailscale()
    _clear_bootstrap_credentials()
    master_node = _master_node()

    baseline = _baseline_state()
    if enrollment_pending:
        if baseline["queued"]:
            _ensure_pending_marker_identity(master_node)
            expected_sequence = baseline["max_sequence"]
        else:
            if (
                master_node.get("last_sequence") != 0
                or master_node.get("next_sequence") != 1
            ):
                raise DeploymentError(
                    "Master's franchise node is not empty (expected cursor 0/1). "
                    "Automatic initial enrollment was blocked before queuing a "
                    "baseline."
                )
            _ensure_pending_marker_identity(master_node)
            expected_sequence = _initialize_new_application()
        acknowledged_node = _wait_for_master_cursor(
            expected_sequence,
            bound_master_public_id=str(master_node.get("public_id") or ""),
        )
        _write_enrollment_marker(
            "complete",
            master_public_id=str(acknowledged_node.get("public_id") or ""),
        )
    elif not baseline["queued"]:
        raise DeploymentError(
            "This application volume contains existing data but has no "
            "franchise-bound INITIAL_INVENTORY marker. Automatic enrollment is "
            "blocked; arrange a reviewed baseline cutover."
        )
    else:
        _verify_established_master_state(allow_marker_creation=True)

    _lines, values = _read_env()
    print("Setuora Lite is healthy on the LAN and authenticated to Master.")
    print(f"Open {_lan_health_url(values).removesuffix('/health')}")
    print(f"Install the public Caddy root CA from {ca_path}")


def _start_existing(*, build: bool) -> Path:
    _check_prerequisites()
    if not ENV_PATH.exists():
        raise DeploymentError("Run `python deploy.py setup` first.")
    state = _volume_state()
    _validate_configuration(volume_state=state)
    arguments = ["up", "-d"]
    if build:
        arguments.append("--build")
    arguments.append("--remove-orphans")
    _compose(*arguments)
    _wait_for_local_health()
    ca_path = _export_ca_file(DEFAULT_CA_EXPORT_PATH)
    _wait_for_lan_https_health(ca_path)
    return ca_path


def start(_args: argparse.Namespace) -> None:
    _start_existing(build=False)
    _lines, values = _read_env()
    print(
        f"Setuora Lite is running at {_lan_health_url(values).removesuffix('/health')}."
    )
    print(
        "Master/Tailscale availability can be checked with `python deploy.py verify-sync`."
    )


def stop(_args: argparse.Namespace) -> None:
    _check_prerequisites()
    _compose("down")
    print(
        "Setuora Lite stopped. Application, Tailscale, and Caddy identity "
        "volumes were preserved."
    )


def status(_args: argparse.Namespace) -> None:
    _check_prerequisites()
    _compose("ps")
    payload = _tailscale_status()
    if _tailscale_online(payload):
        self_status = payload.get("Self", {})
        dns_name = str(self_status.get("DNSName") or "").rstrip(".")
        suffix = f" ({dns_name})" if dns_name else ""
        print(f"Tailscale sync proxy: online{suffix}")
    else:
        print("Tailscale sync proxy: offline or not enrolled")


def logs(args: argparse.Namespace) -> None:
    _check_prerequisites()
    services = [args.service] if args.service else []
    _compose("logs", "--follow", "--tail", str(args.tail), *services)


def update(_args: argparse.Namespace) -> None:
    _check_prerequisites()
    if not ENV_PATH.exists():
        raise DeploymentError("Run `python deploy.py setup` first.")
    _compose("pull", "tailscale", "caddy")
    _start_existing(build=True)
    print(
        "Setuora Lite containers were rebuilt and LAN HTTPS is healthy. "
        "Master/Tailscale availability did not gate this update."
    )


def verify_sync(args: argparse.Namespace) -> None:
    _check_prerequisites()
    if not ENV_PATH.exists():
        raise DeploymentError("Run `python deploy.py setup` first.")
    _wait_for_tailscale(timeout_seconds=args.timeout)
    node, pending_count = _verify_established_master_state(allow_marker_creation=False)
    pending_suffix = (
        f" {pending_count} local event(s) remain pending." if pending_count else ""
    )
    print(
        "Tailscale and authenticated Master connectivity passed for franchise "
        f"{node['code']}.{pending_suffix}"
    )


def export_ca(args: argparse.Namespace) -> None:
    _check_prerequisites()
    destination = Path(args.output) if args.output else DEFAULT_CA_EXPORT_PATH
    exported = _export_ca_file(destination)
    print(f"Exported the public Caddy root CA to {exported}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deploy Setuora Lite identically on Linux and Windows."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, function, help_text in (
        ("preflight", preflight, "validate production configuration without starting"),
        ("setup", setup, "configure, build, enroll, and verify the deployment"),
        ("start", start, "start an existing LAN deployment"),
        ("stop", stop, "stop containers while preserving state"),
        ("status", status, "show container and Tailscale state"),
        ("update", update, "rebuild source and restore LAN service"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.set_defaults(function=function)

    logs_parser = subparsers.add_parser("logs", help="follow container logs")
    logs_parser.add_argument(
        "service",
        nargs="?",
        choices=("setuora", "caddy", "tailscale"),
    )
    logs_parser.add_argument("--tail", type=int, default=200)
    logs_parser.set_defaults(function=logs)

    verify_parser = subparsers.add_parser(
        "verify-sync",
        help="require Tailscale and authenticated Master connectivity",
    )
    verify_parser.add_argument("--timeout", type=int, default=120)
    verify_parser.set_defaults(function=verify_sync)

    ca_parser = subparsers.add_parser(
        "export-ca",
        help="export Caddy's public LAN root certificate",
    )
    ca_parser.add_argument("output", nargs="?")
    ca_parser.set_defaults(function=export_ca)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if getattr(args, "tail", 1) < 1:
        print("Deployment failed: --tail must be at least 1.", file=sys.stderr)
        return 1
    if getattr(args, "timeout", 1) < 1:
        print("Deployment failed: --timeout must be at least 1.", file=sys.stderr)
        return 1
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
