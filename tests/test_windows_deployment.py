from pathlib import Path

import deploy

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_active_deployment_is_windows_native_and_tailscale_free():
    assert not (PROJECT_ROOT / "compose.yaml").exists()
    assert not (PROJECT_ROOT / "Dockerfile").exists()
    deployment = (PROJECT_ROOT / "deploy.py").read_text(encoding="utf-8").lower()
    assert "schtasks.exe" in deployment
    assert "new-netfirewallrule" in deployment
    assert "docker" not in deployment
    assert "tailscale" not in deployment


def test_linux_and_old_private_network_assets_are_archived():
    assert (PROJECT_ROOT / "archive" / "linux" / "Linux — Setuora Lite.run").is_file()
    assert (
        PROJECT_ROOT / "archive" / "linux" / "container-deployment" / "compose.yaml"
    ).is_file()
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
            "SESSION_COOKIE_SECURE": "false",
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
    password = 'spaces # quote " slash \\ stay intact'
    deploy._write_env({"SFTP_PASSWORD": password})
    assert deploy._read_env()[1]["SFTP_PASSWORD"] == password
    assert "# keep" in env_path.read_text(encoding="utf-8")
