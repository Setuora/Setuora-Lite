import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _service_block(compose: str, service: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(service)}:\n(.*?)(?=^  [a-z][a-z0-9-]*:\n|^[a-z])",
        compose,
    )
    assert match is not None
    return match.group(0)


def test_compose_pins_caddy_and_master_compatible_tailscale():
    compose = (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")
    tailscale = _service_block(compose, "tailscale")

    assert (
        "tailscale/tailscale:v1.98.9@"
        "sha256:f15d5d3f4a68773a853180b72496f70ba614b64de0878c43fe3da39fe0afba47"
    ) in tailscale
    assert (
        "caddy:2.11.4-alpine@"
        "sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648"
    ) in compose
    assert "TS_EXTRA_ARGS: --advertise-tags=${TAILSCALE_TAG:-tag:setuora-lite}" in tailscale
    assert 'TS_ACCEPT_DNS: "true"' in tailscale
    assert 'TS_USERSPACE: "true"' in tailscale
    assert "TS_OUTBOUND_HTTP_PROXY_LISTEN: :1055" in tailscale
    assert "TS_SERVE_CONFIG" not in compose
    assert "funnel" not in compose.lower()
    assert "\n    ports:" not in tailscale


def test_app_uses_tailscale_only_for_optional_egress():
    compose = (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")
    app = _service_block(compose, "setuora")
    tailscale = _service_block(compose, "tailscale")

    assert "HTTPS_PROXY: http://tailscale:1055" in app
    assert "https_proxy: http://tailscale:1055" in app
    assert "NO_PROXY: 127.0.0.1,localhost,setuora,caddy,tailscale" in app
    assert "no_proxy: 127.0.0.1,localhost,setuora,caddy,tailscale" in app
    assert "depends_on:" not in app
    assert "network_mode: service:tailscale" not in compose
    assert "\n    ports:" not in app
    assert "tailscale-egress" not in app
    assert "- tailscale-egress" in tailscale


def test_app_isolated_while_edge_services_have_their_required_networks():
    compose = (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")
    tailscale = _service_block(compose, "tailscale")
    app = _service_block(compose, "setuora")
    caddy = _service_block(compose, "caddy")

    assert compose.count("- tailscale-egress") == 1
    assert "- tailscale-egress" in tailscale
    assert "tailscale-egress" not in app
    assert "tailscale-egress" not in caddy
    assert "lan-network" not in app
    assert "lan-network" not in tailscale
    assert "- lan-network" in caddy
    assert re.search(r"(?m)^  sync-network:\n    internal: true$", compose)
    assert re.search(r"(?m)^  app-network:\n    internal: true$", compose)


def test_lite_mode_is_fixed_and_excludes_tally_deployment_settings():
    compose = (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")
    app = _service_block(compose, "setuora")

    assert "SETUORA_APP_MODE: lite" in app
    assert "SESSION_COOKIE_SECURE: \"true\"" in app
    assert 'MASTER_TLS_VERIFY: "true"' in app
    assert "MASTER_URL:" in app
    assert "MASTER_API_KEY:" in app
    assert "FRANCHISE_CODE:" in app
    assert "TALLY_" not in compose


def test_lan_https_is_the_only_host_entry_point_and_has_safe_defaults():
    compose = (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")
    caddy = _service_block(compose, "caddy")
    caddyfile = (
        PROJECT_ROOT / "deployment" / "caddy" / "Caddyfile.container"
    ).read_text(encoding="utf-8")

    assert (
        '"${SETUORA_LAN_BIND_ADDRESS:-127.0.0.1}:'
        '${SETUORA_HTTP_PORT:-80}:80"'
    ) in caddy
    assert (
        '"${SETUORA_LAN_BIND_ADDRESS:-127.0.0.1}:'
        '${SETUORA_HTTPS_PORT:-443}:443"'
    ) in caddy
    assert "https://{$SETUORA_LAN_HOSTNAME:localhost}" in caddyfile
    assert "tls internal" in caddyfile
    assert "skip_install_trust" in caddyfile
    assert "reverse_proxy setuora:8000" in caddyfile
    assert "tailscale" not in caddyfile.lower()


def test_runtime_state_is_persistent_and_app_filesystem_is_read_only():
    compose = (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")
    app = _service_block(compose, "setuora")
    caddy = _service_block(compose, "caddy")

    assert "setuora-data:/srv/setuora/data" in app
    assert "BACKUP_SETTINGS_FILE: /srv/setuora/data/backup-settings.env" in app
    assert 'SETUORA_CONTAINER_DEPLOYMENT: "true"' in app
    assert "tailscale-state:/var/lib/tailscale" in compose
    assert "caddy-data:/data" in caddy
    assert "caddy-config:/config" in caddy
    assert "read_only: true" in app
    assert "read_only: true" in caddy
    assert "DATABASE_URL: sqlite:////srv/setuora/data/setuora.db" in app


def test_compose_caps_service_logs_for_long_offline_periods():
    compose = (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert compose.count("driver: local") == 3
    assert compose.count("max-size: 10m") == 3
    assert compose.count('max-file: "5"') == 3


def test_container_build_uses_a_non_root_user_and_runtime_hash_lock():
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    runtime_input = (PROJECT_ROOT / "requirements-runtime.txt").read_text(
        encoding="utf-8"
    )
    runtime_lock = (PROJECT_ROOT / "requirements-runtime.lock").read_text(
        encoding="utf-8"
    )
    ignored = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert "COPY requirements-runtime.lock ./" in dockerfile
    assert "--require-hashes -r requirements-runtime.lock" in dockerfile
    assert "USER setuora" in dockerfile
    assert "pytest" not in runtime_input.lower()
    assert "pytest" not in runtime_lock.lower()
    assert "httpx" not in runtime_input.lower()
    assert "httpx" not in runtime_lock.lower()
    assert "--hash=sha256:" in runtime_lock
    assert ".env" in ignored
    assert "data" in ignored
    assert ".git" in ignored
