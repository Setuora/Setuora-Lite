import base64
import hashlib
import io
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_client_package_builder_creates_safe_complete_platform_archives(tmp_path):
    version = "test-1.2.3"
    result = subprocess.run(  # nosec B603
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "build_client_packages.py"),
            "--version",
            version,
            "--output",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    linux_path = tmp_path / f"Setuora-Lite-{version}-linux.run"
    windows_path = tmp_path / f"Setuora-Lite-{version}-windows.cmd"
    linux_delivery = tmp_path / f"Setuora-Lite-{version}-Linux.zip"
    windows_delivery = tmp_path / f"Setuora-Lite-{version}-Windows.zip"
    checksum_path = tmp_path / f"Setuora-Lite-{version}-SHA256SUMS.txt"
    assert str(linux_path) in result.stdout
    assert linux_path.is_file()
    assert windows_path.is_file()
    assert linux_delivery.is_file()
    assert windows_delivery.is_file()
    assert checksum_path.is_file()
    assert os.access(linux_path, os.X_OK)

    linux_root = "Setuora-Lite-linux"
    _linux_header, linux_payload = linux_path.read_bytes().split(
        b"\n__SETUORA_PAYLOAD_BELOW__\n",
        1,
    )
    assert linux_path.read_bytes().startswith(b"#!/usr/bin/env bash")
    with tarfile.open(fileobj=io.BytesIO(linux_payload), mode="r:gz") as archive:
        linux_members = {member.name: member for member in archive.getmembers()}
    assert f"{linux_root}/setuora" in linux_members
    assert f"{linux_root}/app/main.py" in linux_members
    assert f"{linux_root}/compose.yaml" in linux_members
    assert f"{linux_root}/deployment/caddy/Caddyfile.container" in linux_members
    assert linux_members[f"{linux_root}/setuora"].mode == 0o755

    windows_root = "Setuora-Lite-windows"
    windows_header, encoded_payload = windows_path.read_bytes().split(
        b"\n__SETUORA_PAYLOAD_BELOW__\n",
        1,
    )
    assert windows_header.startswith(b"@echo off")
    windows_payload = base64.b64decode(encoded_payload)
    with zipfile.ZipFile(io.BytesIO(windows_payload)) as archive:
        windows_members = set(archive.namelist())
    assert f"{windows_root}/setuora.ps1" in windows_members
    assert f"{windows_root}/app/main.py" in windows_members
    assert f"{windows_root}/compose.yaml" in windows_members
    assert f"{windows_root}/deployment/caddy/Caddyfile.container" in windows_members
    assert not any(member_name.lower().endswith(".exe") for member_name in windows_members)

    with zipfile.ZipFile(linux_delivery) as archive:
        assert set(archive.namelist()) == {
            "Install Setuora Lite.run",
            "START HERE.txt",
        }
        installer_mode = archive.getinfo("Install Setuora Lite.run").external_attr >> 16
        assert installer_mode & 0o111
    with zipfile.ZipFile(windows_delivery) as archive:
        assert set(archive.namelist()) == {
            "Install Setuora Lite.cmd",
            "START HERE.txt",
        }

    for member_name in set(linux_members) | windows_members:
        parts = set(PurePosixPath(member_name).parts)
        assert ".env" not in parts
        assert ".git" not in parts
        assert "data" not in parts
        assert "__pycache__" not in parts

    expected_checksums = {
        linux_path.name: hashlib.sha256(linux_path.read_bytes()).hexdigest(),
        windows_path.name: hashlib.sha256(windows_path.read_bytes()).hexdigest(),
        linux_delivery.name: hashlib.sha256(linux_delivery.read_bytes()).hexdigest(),
        windows_delivery.name: hashlib.sha256(windows_delivery.read_bytes()).hexdigest(),
    }
    checksum_lines = checksum_path.read_text(encoding="utf-8").splitlines()
    actual_checksums = {
        file_name: checksum
        for checksum, file_name in (line.split("  ", 1) for line in checksum_lines)
    }
    assert actual_checksums == expected_checksums


def test_single_file_installers_detect_setup_or_update():
    linux = (PROJECT_ROOT / "client" / "linux" / "self-extract-header.sh").read_text(
        encoding="utf-8"
    )
    windows = (
        PROJECT_ROOT / "client" / "windows" / "self-extract-header.cmd"
    ).read_text(encoding="utf-8")

    assert 'ACTION="update"' in linux
    assert 'ACTION="setup"' in linux
    assert '"$INSTALL_DIR/setuora" preflight' in linux
    assert '"$INSTALL_DIR/setuora" update' in linux
    assert '"$INSTALL_DIR/setuora" setup' in linux
    assert "$isUpdate = Test-Path" in windows
    assert "$legacyLauncher" in windows
    assert "Remove-Item -LiteralPath $legacyLauncher" in windows
    assert "-File $launcher preflight" in windows
    assert "-File $launcher update" in windows
    assert "-File $launcher setup" in windows


def test_windows_launcher_matches_master_powershell_flow():
    launcher = (
        PROJECT_ROOT / "client" / "windows" / "setuora.ps1"
    ).read_text(encoding="utf-8")

    assert 'ValidateSet(' in launcher
    assert '"verify-sync"' in launcher
    assert '"export-ca"' in launcher
    assert "Install-SetuoraDockerDesktop" in launcher
    assert "Ensure-SetuoraWsl" in launcher
    assert "Get-AuthenticodeSignature" in launcher
    assert "Start-SetuoraDockerDesktop" in launcher
    assert "New-NetFirewallRule" in launcher
    assert 'sys.version_info < (3, 11)' in launcher
    assert '$PSScriptRoot\\deploy.py' in launcher


def test_linux_launcher_installs_and_finishes_prerequisites():
    launcher = (PROJECT_ROOT / "client" / "linux" / "setuora").read_text(
        encoding="utf-8"
    )

    assert "https://get.docker.com" in launcher
    assert "apt-get install -y ca-certificates curl python3" in launcher
    assert "usermod -aG docker" in launcher
    assert "update-ca-certificates" in launcher
    assert "ufw allow from" in launcher
    assert "firewall-cmd --permanent --add-rich-rule" in launcher


def test_linux_single_file_runs_setup_then_update_without_real_docker(tmp_path):
    version = "integration-test"
    output_directory = tmp_path / "output"
    subprocess.run(  # nosec B603
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "build_client_packages.py"),
            "--version",
            version,
            "--output",
            str(output_directory),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_docker.chmod(0o755)
    fake_python = fake_bin / "python3.11"
    fake_python.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$SETUORA_TEST_LOG"\nexit 0\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    log_path = tmp_path / "commands.log"
    data_home = tmp_path / "data-home"
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "SETUORA_TEST_LOG": str(log_path),
        "XDG_DATA_HOME": str(data_home),
    }
    installer = output_directory / f"Setuora-Lite-{version}-linux.run"

    subprocess.run(  # nosec B603
        [str(installer)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    installation = data_home / "setuora" / "Setuora-Lite-linux"
    assert (installation / "app" / "main.py").is_file()
    commands = log_path.read_text(encoding="utf-8")
    assert str(installation / "deploy.py") + " setup" in commands

    (installation / ".env").write_text(
        "SETUORA_APP_MODE=lite\n",
        encoding="utf-8",
    )
    subprocess.run(  # nosec B603
        [str(installer)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    commands = log_path.read_text(encoding="utf-8")
    assert str(installation / "deploy.py") + " stop" in commands
    assert str(installation / "deploy.py") + " preflight" in commands
    assert str(installation / "deploy.py") + " update" in commands


def test_root_shortcuts_launch_the_newest_platform_installer():
    linux = (PROJECT_ROOT / "Linux — Setuora Lite.run").read_text(encoding="utf-8")
    windows = (PROJECT_ROOT / "Windows — Setuora Lite.cmd").read_text(encoding="utf-8")

    assert "dist/Setuora-Lite-*-linux.run" in linux
    assert '-nt "$LATEST_INSTALLER"' in linux
    assert 'exec "$LATEST_INSTALLER"' in linux
    assert "Setuora-Lite-*-windows.cmd" in windows
    assert "/o:-d" in windows
    assert "scripts\\build_client_packages.py" in windows
    assert "No built Windows package was found. Building version" in windows
    assert "Do not copy \"Windows — Setuora Lite.cmd\" by itself." in windows
    assert 'call "%SETUORA_INSTALLER%"' in windows
