import json
from types import SimpleNamespace

import pytest

import deploy


def completed(*, returncode: int = 0, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def production_values() -> dict[str, str]:
    return {
        "APP_SECRET_KEY": "s" * 48,
        "AUTOMATIC_BACKUPS_ENABLED": "true",
        "BACKUP_INTERVAL_HOURS": "24",
        "BACKUP_RETENTION_COUNT": "14",
        "BOOTSTRAP_ADMIN_PASSWORD": "a-unique-admin-password",
        "FRANCHISE_CODE": "SG-NORTH-01",
        "MASTER_API_KEY": f"setuora-node.node_id.{'k' * 40}",
        "MASTER_SYNC_ENABLED": "true",
        "MASTER_TLS_VERIFY": "true",
        "MASTER_URL": "https://setuora-master.example.ts.net",
        "SESSION_COOKIE_SECURE": "true",
        "SETUORA_APP_MODE": "lite",
        "SETUORA_HTTP_PORT": "80",
        "SETUORA_HTTPS_PORT": "443",
        "SETUORA_LAN_BIND_ADDRESS": "192.168.10.12",
        "SETUORA_LAN_HOSTNAME": "192.168.10.12",
        "TAILSCALE_AUTH_KEY": "tskey-auth-abcdefghijk",
        "TAILSCALE_HOSTNAME": "setuora-lite-sg-north-01",
        "TAILSCALE_TAG": "tag:setuora-lite",
        "TRUSTED_HOSTS": "127.0.0.1,192.168.10.12,localhost",
    }


def test_env_updates_preserve_comments_prefix_spacing_and_quote_style(
    tmp_path,
    monkeypatch,
):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# Keep this heading\n"
        'export APP_SECRET_KEY = "old"  # keep inline\n'
        "BOOTSTRAP_ADMIN_PASSWORD='old password'\n"
        "UNRELATED = untouched\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(deploy, "ENV_PATH", env_path)

    deploy._write_env(
        {
            "APP_SECRET_KEY": "new-secret",
            "BOOTSTRAP_ADMIN_PASSWORD": "new password",
            "MASTER_API_KEY": "value with $literal",
        }
    )

    raw = env_path.read_text(encoding="utf-8")
    assert "# Keep this heading" in raw
    assert 'export APP_SECRET_KEY = "new-secret"  # keep inline' in raw
    assert "BOOTSTRAP_ADMIN_PASSWORD='new password'" in raw
    assert "UNRELATED = untouched" in raw
    assert "MASTER_API_KEY='value with $literal'" in raw
    values = deploy._read_env()[1]
    assert values["APP_SECRET_KEY"] == "new-secret"
    assert values["BOOTSTRAP_ADMIN_PASSWORD"] == "new password"
    assert values["MASTER_API_KEY"] == "value with $literal"


def test_env_round_trips_spaces_quotes_backslashes_and_hash(
    tmp_path,
    monkeypatch,
):
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_PATH", env_path)
    password = 'spaces # double " quote and slash \\ stay intact'

    deploy._write_env({"BOOTSTRAP_ADMIN_PASSWORD": password})

    raw = env_path.read_text(encoding="utf-8")
    assert raw.startswith('BOOTSTRAP_ADMIN_PASSWORD="')
    assert deploy._read_env()[1]["BOOTSTRAP_ADMIN_PASSWORD"] == password


def test_env_update_replaces_every_duplicate_secret_assignment(
    tmp_path,
    monkeypatch,
):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "MASTER_API_KEY=old-first\n"
        "# A duplicate must not retain an old secret.\n"
        "MASTER_API_KEY='old-second'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(deploy, "ENV_PATH", env_path)

    deploy._write_env({"MASTER_API_KEY": "replacement"})

    raw = env_path.read_text(encoding="utf-8")
    assert "old-first" not in raw
    assert "old-second" not in raw
    assert raw.count("replacement") == 2
    assert deploy._read_env()[1]["MASTER_API_KEY"] == "replacement"


def test_env_reader_preserves_unknown_double_quote_backslash_escapes(
    tmp_path,
    monkeypatch,
):
    env_path = tmp_path / ".env"
    env_path.write_text(
        'BACKUP_OFFSITE_DIRECTORY="C:\\Setuora\\backup"\n', encoding="utf-8"
    )
    monkeypatch.setattr(deploy, "ENV_PATH", env_path)

    assert deploy._read_env()[1]["BACKUP_OFFSITE_DIRECTORY"] == r"C:\Setuora\backup"


def test_production_environment_validation_accepts_exact_secure_values():
    issues = deploy._environment_issues(
        production_values(),
        has_application_data=False,
        has_tailnet_identity=False,
    )

    assert issues == []


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("FRANCHISE_CODE", "SG north", "FRANCHISE_CODE"),
        ("FRANCHISE_CODE", "CHANGE-ME", "FRANCHISE_CODE"),
        ("MASTER_URL", "https://master.example.com", "MASTER_URL"),
        ("MASTER_URL", "https://master.example.ts.net/api", "MASTER_URL"),
        ("MASTER_API_KEY", "setuora-node.short", "MASTER_API_KEY"),
        ("SETUORA_LAN_BIND_ADDRESS", "0.0.0.0", "SETUORA_LAN_BIND_ADDRESS"),
        ("SETUORA_LAN_BIND_ADDRESS", "192.0.2.10", "SETUORA_LAN_BIND_ADDRESS"),
        ("SETUORA_LAN_HOSTNAME", "*.local", "SETUORA_LAN_HOSTNAME"),
        (
            "TAILSCALE_AUTH_KEY",
            "tskey-client-not-a-one-off-key",
            "TAILSCALE_AUTH_KEY",
        ),
        ("TAILSCALE_HOSTNAME", "setuora-lite", "TAILSCALE_HOSTNAME"),
        ("TAILSCALE_TAG", "tag:setuora-master", "TAILSCALE_TAG"),
        ("MASTER_TLS_VERIFY", "false", "MASTER_TLS_VERIFY"),
    ],
)
def test_production_environment_validation_rejects_unsafe_values(
    name,
    value,
    message,
):
    values = production_values()
    values[name] = value

    issues = deploy._environment_issues(
        values,
        has_application_data=False,
        has_tailnet_identity=False,
    )

    assert any(message in issue for issue in issues)


def test_persisted_identities_allow_cleared_bootstrap_keys():
    values = production_values()
    values["BOOTSTRAP_ADMIN_PASSWORD"] = ""
    values["TAILSCALE_AUTH_KEY"] = ""

    issues = deploy._environment_issues(
        values,
        has_application_data=True,
        has_tailnet_identity=True,
    )

    assert issues == []


def test_prepare_replaces_generic_tailscale_hostname_with_franchise_identity(
    tmp_path,
    monkeypatch,
):
    env_path = tmp_path / ".env"
    values = production_values()
    values["TAILSCALE_HOSTNAME"] = "setuora-lite"
    env_path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(deploy, "ENV_PATH", env_path)

    deploy._prepare_environment(
        has_application_data=False,
        has_tailnet_identity=False,
    )

    assert deploy._read_env()[1]["TAILSCALE_HOSTNAME"] == "setuora-lite-sg-north-01"


def test_compose_ignores_shell_overrides_of_reviewed_env(monkeypatch):
    monkeypatch.setenv("FRANCHISE_CODE", "WRONG-SHELL-CODE")
    monkeypatch.setenv("MASTER_API_KEY", "wrong-shell-key")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "wrong-project")
    monkeypatch.setenv("SETUORA_TEST_UNRELATED", "preserved")

    environment = deploy._compose_process_environment()

    assert "FRANCHISE_CODE" not in environment
    assert "MASTER_API_KEY" not in environment
    assert "COMPOSE_PROJECT_NAME" not in environment
    assert environment["SETUORA_TEST_UNRELATED"] == "preserved"


def test_clear_bootstrap_credentials_recreates_services_without_secret_arguments(
    tmp_path,
    monkeypatch,
):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# bootstrap values are removed after identity persistence\n"
        "TAILSCALE_AUTH_KEY=tskey-auth-supersecret\n"
        'BOOTSTRAP_ADMIN_PASSWORD="admin supersecret"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(deploy, "ENV_PATH", env_path)
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        deploy,
        "_compose",
        lambda *args, **_kwargs: commands.append(args) or completed(),
    )
    monkeypatch.setattr(deploy, "_wait_for_local_health", lambda: None)
    monkeypatch.setattr(deploy, "_wait_for_tailscale", dict)

    deploy._clear_bootstrap_credentials()

    values = deploy._read_env()[1]
    assert values["TAILSCALE_AUTH_KEY"] == ""
    assert values["BOOTSTRAP_ADMIN_PASSWORD"] == ""
    assert "# bootstrap values" in env_path.read_text(encoding="utf-8")
    assert commands == [
        (
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "tailscale",
            "setuora",
        )
    ]
    assert "supersecret" not in repr(commands)


def test_complete_enrollment_marker_is_bound_to_master_public_id(monkeypatch):
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        deploy,
        "_compose",
        lambda *args, **kwargs: commands.append(args) or completed(),
    )

    deploy._write_enrollment_marker(
        "complete",
        master_public_id="master-node-public-id",
    )

    assert commands[0][-2:] == ("complete", "master-node-public-id")


def test_master_node_check_requires_exact_franchise_code(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("FRANCHISE_CODE=SG-NORTH-01\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_PATH", env_path)
    response = {
        "data": {
            "code": "SG-NORTH-02",
            "last_sequence": 0,
        },
        "error": None,
        "request_id": "request-1",
    }
    monkeypatch.setattr(
        deploy,
        "_compose",
        lambda *args, **kwargs: completed(stdout=json.dumps(response)),
    )

    with pytest.raises(deploy.DeploymentError, match="different franchise code"):
        deploy._master_node()


def test_master_node_check_runs_inside_app_and_through_configured_proxy(
    tmp_path,
    monkeypatch,
):
    env_path = tmp_path / ".env"
    env_path.write_text("FRANCHISE_CODE=SG-NORTH-01\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_PATH", env_path)
    commands: list[tuple[str, ...]] = []
    response = {
        "data": {
            "code": "SG-NORTH-01",
            "last_sequence": 7,
        }
    }

    def fake_compose(*args, **_kwargs):
        commands.append(args)
        return completed(stdout=json.dumps(response))

    monkeypatch.setattr(deploy, "_compose", fake_compose)

    node = deploy._master_node()

    assert node["last_sequence"] == 7
    assert commands[0][:5] == ("exec", "-T", "setuora", "python", "-c")
    script = commands[0][5]
    assert 'ProxyHandler({"https": proxy})' in script
    assert "get_settings" in script
    assert "settings.master_api_key" in script
    assert "setuora-node." not in repr(commands)


def test_regular_start_does_not_require_tailscale_or_master(
    tmp_path,
    monkeypatch,
):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "SETUORA_LAN_HOSTNAME=192.168.10.12\nSETUORA_HTTPS_PORT=443\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(deploy, "ENV_PATH", env_path)
    monkeypatch.setattr(deploy, "_check_prerequisites", lambda: None)
    monkeypatch.setattr(
        deploy,
        "_volume_state",
        lambda: {name: True for name in deploy.PERSISTENT_VOLUMES},
    )
    monkeypatch.setattr(deploy, "_validate_configuration", lambda **kwargs: None)
    monkeypatch.setattr(deploy, "_compose", lambda *args, **kwargs: completed())
    monkeypatch.setattr(deploy, "_seed_pending_enrollment_marker", lambda: None)
    monkeypatch.setattr(deploy, "_wait_for_local_health", lambda: None)
    monkeypatch.setattr(
        deploy,
        "_export_ca_file",
        lambda path: tmp_path / "root.crt",
    )
    monkeypatch.setattr(deploy, "_wait_for_lan_https_health", lambda path: None)
    monkeypatch.setattr(
        deploy,
        "_wait_for_tailscale",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("ordinary start must not gate on Tailscale")
        ),
    )
    monkeypatch.setattr(
        deploy,
        "_master_node",
        lambda: (_ for _ in ()).throw(
            AssertionError("ordinary start must not gate on Master")
        ),
    )

    deploy.start(SimpleNamespace())


def test_regular_update_does_not_require_tailscale_or_master(
    tmp_path,
    monkeypatch,
):
    env_path = tmp_path / ".env"
    env_path.write_text("present=true\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_PATH", env_path)
    monkeypatch.setattr(deploy, "_check_prerequisites", lambda: None)
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        deploy,
        "_compose",
        lambda *args, **kwargs: commands.append(args) or completed(),
    )
    monkeypatch.setattr(
        deploy,
        "_start_existing",
        lambda *, build: None,
    )
    monkeypatch.setattr(
        deploy,
        "_wait_for_tailscale",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("ordinary update must not gate on Tailscale")
        ),
    )
    monkeypatch.setattr(
        deploy,
        "_master_node",
        lambda: (_ for _ in ()).throw(
            AssertionError("ordinary update must not gate on Master")
        ),
    )

    deploy.update(SimpleNamespace())

    assert commands == [("pull", "tailscale", "caddy")]


def test_new_setup_refuses_to_initialize_a_reused_master_cursor(
    tmp_path,
    monkeypatch,
):
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_PATH", env_path)
    monkeypatch.setattr(deploy, "_check_prerequisites", lambda: None)
    monkeypatch.setattr(
        deploy,
        "_volume_state",
        lambda: {name: False for name in deploy.PERSISTENT_VOLUMES},
    )
    monkeypatch.setattr(
        deploy, "_block_unmigrated_local_database", lambda **kwargs: None
    )
    monkeypatch.setattr(deploy, "_prepare_environment", lambda **kwargs: None)
    monkeypatch.setattr(deploy, "_validate_configuration", lambda **kwargs: None)
    monkeypatch.setattr(deploy, "_compose", lambda *args, **kwargs: completed())
    monkeypatch.setattr(deploy, "_wait_for_local_health", lambda: None)
    monkeypatch.setattr(deploy, "_pending_helper_enrollment", lambda **kwargs: True)
    monkeypatch.setattr(
        deploy,
        "_export_ca_file",
        lambda path: tmp_path / "root.crt",
    )
    monkeypatch.setattr(deploy, "_wait_for_lan_https_health", lambda path: None)
    monkeypatch.setattr(deploy, "_wait_for_tailscale", dict)
    monkeypatch.setattr(deploy, "_clear_bootstrap_credentials", lambda: None)
    monkeypatch.setattr(
        deploy,
        "_master_node",
        lambda: {"code": "SG-NORTH-01", "last_sequence": 4, "next_sequence": 5},
    )
    monkeypatch.setattr(
        deploy,
        "_baseline_state",
        lambda: {
            "queued": False,
            "max_sequence": 0,
            "last_sent_sequence": 0,
            "unsent_count": 0,
        },
    )
    monkeypatch.setattr(
        deploy,
        "_initialize_new_application",
        lambda: (_ for _ in ()).throw(
            AssertionError("baseline must not be queued against a reused node")
        ),
    )

    with pytest.raises(deploy.DeploymentError, match="expected cursor 0/1"):
        deploy.setup(SimpleNamespace())


def test_initial_cursor_verification_requires_exact_master_and_sent_lite_state(
    monkeypatch,
):
    monkeypatch.setattr(
        deploy,
        "_baseline_state",
        lambda: {
            "queued": True,
            "max_sequence": 2,
            "last_sent_sequence": 2,
            "unsent_count": 0,
        },
    )
    monkeypatch.setattr(
        deploy,
        "_master_node",
        lambda: {"code": "SG-NORTH-01", "last_sequence": 3, "next_sequence": 4},
    )

    with pytest.raises(deploy.DeploymentError, match="ahead of Lite"):
        deploy._wait_for_master_cursor(2)


def test_complete_marker_rejects_same_code_with_different_master_public_id(
    tmp_path,
    monkeypatch,
):
    env_path = tmp_path / ".env"
    env_path.write_text("FRANCHISE_CODE=SG-NORTH-01\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_PATH", env_path)
    monkeypatch.setattr(
        deploy,
        "_enrollment_marker",
        lambda: {
            "state": "complete",
            "franchise_code": "SG-NORTH-01",
            "master_public_id": "node-public-a",
        },
    )

    with pytest.raises(deploy.DeploymentError, match="different node identity"):
        deploy._ensure_complete_marker_identity(
            {
                "code": "SG-NORTH-01",
                "public_id": "node-public-b",
            },
            allow_create=False,
        )


def test_established_sync_rejects_master_cursor_behind_last_sent(
    monkeypatch,
):
    state = {
        "queued": True,
        "max_sequence": 3,
        "last_sent_sequence": 3,
        "unsent_count": 0,
    }
    monkeypatch.setattr(deploy, "_baseline_state", lambda: state)
    monkeypatch.setattr(
        deploy,
        "_master_node",
        lambda: {
            "code": "SG-NORTH-01",
            "public_id": "node-public-a",
            "last_sequence": 2,
            "next_sequence": 3,
        },
    )

    with pytest.raises(deploy.DeploymentError, match="behind Lite"):
        deploy._verify_established_master_state(allow_marker_creation=False)


def test_established_sync_reports_pending_after_exact_sent_prefix(
    monkeypatch,
):
    state = {
        "queued": True,
        "max_sequence": 3,
        "last_sent_sequence": 2,
        "unsent_count": 1,
    }
    node = {
        "code": "SG-NORTH-01",
        "public_id": "node-public-a",
        "last_sequence": 2,
        "next_sequence": 3,
    }
    monkeypatch.setattr(deploy, "_baseline_state", lambda: state)
    monkeypatch.setattr(deploy, "_master_node", lambda: node)
    verified: list[tuple[dict, bool]] = []
    monkeypatch.setattr(
        deploy,
        "_ensure_complete_marker_identity",
        lambda value, *, allow_create: verified.append((value, allow_create)),
    )

    result_node, pending = deploy._verify_established_master_state(
        allow_marker_creation=False
    )

    assert result_node == node
    assert pending == 1
    assert verified == [(node, False)]


def test_unmigrated_local_database_is_detected_without_mutation(
    tmp_path,
    monkeypatch,
):
    legacy = tmp_path / "data" / "setu.db"
    legacy.parent.mkdir()
    original = b"existing-sqlite-data"
    legacy.write_bytes(original)
    monkeypatch.setattr(deploy, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(deploy, "LEGACY_DATABASE_CANDIDATES", (legacy,))

    with pytest.raises(deploy.DeploymentError, match="No data was changed or imported"):
        deploy._block_unmigrated_local_database(has_application_data=False)

    assert legacy.read_bytes() == original


def test_named_volume_does_not_bypass_local_database_migration_block(
    tmp_path,
    monkeypatch,
):
    legacy = tmp_path / "setu.db"
    legacy.write_bytes(b"existing-sqlite-data")
    monkeypatch.setattr(deploy, "LEGACY_DATABASE_CANDIDATES", (legacy,))

    with pytest.raises(deploy.DeploymentError, match="named volume alone"):
        deploy._block_unmigrated_local_database(has_application_data=True)


def test_cli_exposes_all_deployment_commands():
    parser = deploy.build_parser()

    for command in (
        "preflight",
        "setup",
        "start",
        "stop",
        "status",
        "logs",
        "update",
        "verify-sync",
        "export-ca",
    ):
        assert parser.parse_args([command]).command == command
