import base64
import hashlib
import io
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_package_builder_creates_windows_only_installer(tmp_path):
    version = "test-1.2.3"
    subprocess.run(
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
    installer = tmp_path / f"Setuora-Lite-{version}-windows.cmd"
    checksum_path = tmp_path / f"Setuora-Lite-{version}-SHA256SUMS.txt"
    assert installer.is_file()
    assert not list(tmp_path.glob("*linux*"))

    _, encoded_payload = installer.read_bytes().split(
        b"\n__SETUORA_PAYLOAD_BELOW__\n", 1
    )
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(encoded_payload))) as archive:
        members = set(archive.namelist())
    root = "Setuora-Lite-windows"
    assert f"{root}/setuora.ps1" in members
    assert f"{root}/app/services/sftp_tally_sync.py" in members
    assert f"{root}/scripts/windows/run-server.cmd" in members
    assert f"{root}/compose.yaml" not in members
    assert f"{root}/Dockerfile" not in members
    for member in members:
        assert not set(PurePosixPath(member).parts) & {
            ".env",
            ".git",
            "archive",
            "data",
            "__pycache__",
        }
    checksum, filename = (
        checksum_path.read_text(encoding="utf-8").strip().split("  ", 1)
    )
    assert filename == installer.name
    assert checksum == hashlib.sha256(installer.read_bytes()).hexdigest()


def test_windows_launcher_has_no_container_prerequisites():
    launcher = (
        (PROJECT_ROOT / "client" / "windows" / "setuora.ps1")
        .read_text(encoding="utf-8")
        .lower()
    )
    header = (
        PROJECT_ROOT / "client" / "windows" / "self-extract-header.cmd"
    ).read_text(encoding="utf-8")
    assert "docker" not in launcher
    assert "wsl" not in launcher
    assert "$env:ProgramData" in header
    assert "-Verb RunAs" in header
