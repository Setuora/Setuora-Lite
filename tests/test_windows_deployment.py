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


def test_source_checkout_batch_controller_owns_waited_elevation():
    controller = (PROJECT_ROOT / "setuora.bat").read_text(encoding="utf-8")
    setup = (PROJECT_ROOT / "scripts" / "setup.bat").read_text(encoding="utf-8")
    update = (PROJECT_ROOT / "scripts" / "update.bat").read_text(encoding="utf-8")
    finder = (PROJECT_ROOT / "scripts" / "find_python.bat").read_text(encoding="utf-8")

    assert 'set "ROOT_DIR=%~dp0"' in controller
    assert all(
        f'if /I "%~1"=="{command}"' in controller
        for command in ("setup", "start", "stop", "update", "help")
    )
    assert "Setup / repair" in controller
    assert "Invalid choice" in controller
    assert "completed successfully" in controller
    assert "-Verb RunAs -Wait -PassThru" in controller
    assert "$process.ExitCode" in controller
    assert "ELEVATED_REENTRY" in controller
    assert "if defined CLI_MODE endlocal & exit /b" not in controller
    assert "if defined CLI_MODE goto cli_exit" in controller
    assert ":cli_exit\nendlocal & exit /b %EXIT_CODE%" in controller
    assert "pause\ngoto menu\n\n:cli_exit" in controller
    assert "$env:SETUORA_CONTROLLER +" not in controller
    assert '"%%SETUORA_CONTROLLER%%"' in controller
    assert '"%%SETUORA_ELEVATED_LOG%%" 2>&1' in controller
    assert "if not defined CLI_MODE goto elevation_ready\ncall :prepare_elevation_log" in controller
    assert "%RANDOM%-%RANDOM%.log" in controller
    assert 'type "%ELEVATED_LOG%"' in controller
    assert 'del /q "%ELEVATED_LOG%"' in controller
    assert controller.index('set "ELEVATED_EXIT_CODE=%ERRORLEVEL%"') < controller.index(
        'type "%ELEVATED_LOG%"'
    )

    assert "Start-Process" not in setup
    assert "requires Administrator privileges" in setup
    assert "Python.Python.3.11" in setup
    assert '"%DEPLOY_SCRIPT%" setup' in setup
    assert "py -3.11" in finder
    assert "%ProgramFiles%\\Python%%V\\python.exe" in finder
    assert "%LOCALAPPDATA%\\Programs\\Python\\Python%%V\\python.exe" in finder
    for script_name in ("setup.bat", "start_setuora.bat", "stop_setuora.bat", "update.bat"):
        lifecycle = (PROJECT_ROOT / "scripts" / script_name).read_text(encoding="utf-8")
        assert 'call "%SCRIPT_DIR%find_python.bat"' in lifecycle
        assert ":find_python" not in lifecycle

    assert "status --porcelain --untracked-files=all" in update
    assert "merge-base --is-ancestor" in update
    assert "merge --ff-only" in update
