"""Build shareable Linux and Windows Setuora Lite client packages."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import os
import re
import stat
import tarfile
import tempfile
import textwrap
import zipfile
from pathlib import Path, PurePosixPath

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "dist"
RELEASE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

COMMON_FILES = (
    ".dockerignore",
    ".env.example",
    "Dockerfile",
    "compose.yaml",
    "deploy.py",
    "deployment/caddy/Caddyfile.container",
    "requirements-runtime.lock",
    "client/CLIENT-README.md",
)
COMMON_DIRECTORIES = (
    "app",
    "docs/deployment",
)
LINUX_FILES = {
    "client/linux/setuora": "setuora",
}
WINDOWS_FILES = {
    "client/windows/setuora.ps1": "setuora.ps1",
}
LINUX_HEADER = PROJECT_ROOT / "client/linux/self-extract-header.sh"
WINDOWS_HEADER = PROJECT_ROOT / "client/windows/self-extract-header.cmd"


def _common_payload() -> dict[str, Path]:
    payload = {name: PROJECT_ROOT / name for name in COMMON_FILES}
    for directory_name in COMMON_DIRECTORIES:
        directory = PROJECT_ROOT / directory_name
        for source in sorted(directory.rglob("*")):
            if source.is_file() and "__pycache__" not in source.parts:
                payload[source.relative_to(PROJECT_ROOT).as_posix()] = source
    return payload


def _payload(platform_files: dict[str, str]) -> dict[str, Path]:
    payload = _common_payload()
    payload["CLIENT-README.md"] = payload.pop("client/CLIENT-README.md")
    for source_name, archive_name in platform_files.items():
        payload[archive_name] = PROJECT_ROOT / source_name
    return payload


def _release_text(version: str, platform: str) -> bytes:
    return (
        f"Setuora Lite\nVersion: {version}\nPlatform: {platform}\n"
        "Persistent data is stored in Docker volumes.\n"
    ).encode()


def _validate_payload(payload: dict[str, Path]) -> None:
    forbidden = {".env", ".git", "data", "__pycache__"}
    project_root = PROJECT_ROOT.resolve()
    for archive_name, source in payload.items():
        parts = set(PurePosixPath(archive_name).parts)
        if parts & forbidden:
            raise ValueError(
                f"Refusing to package private or generated path: {archive_name}"
            )
        if not source.is_file():
            raise FileNotFoundError(source)
        if source.is_symlink() or not source.resolve().is_relative_to(project_root):
            raise ValueError(f"Refusing to package an external path: {source}")


def _write_tar(
    path: Path,
    root_name: str,
    payload: dict[str, Path],
    version: str,
) -> None:
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for archive_name, source in sorted(payload.items()):
            member_name = f"{root_name}/{archive_name}"
            info = archive.gettarinfo(str(source), member_name)
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            info.mtime = 0
            if archive_name == "setuora":
                info.mode = 0o755
            with source.open("rb") as handle:
                archive.addfile(info, handle)

        release = _release_text(version, "Linux")
        info = tarfile.TarInfo(f"{root_name}/RELEASE.txt")
        info.size = len(release)
        info.mode = 0o644
        info.mtime = 0
        archive.addfile(info, fileobj=io.BytesIO(release))


def _write_zip(
    path: Path,
    root_name: str,
    payload: dict[str, Path],
    version: str,
) -> None:
    with zipfile.ZipFile(
        path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for archive_name, source in sorted(payload.items()):
            info = zipfile.ZipInfo(
                f"{root_name}/{archive_name}",
                (1980, 1, 1, 0, 0, 0),
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, source.read_bytes())

        info = zipfile.ZipInfo(
            f"{root_name}/RELEASE.txt",
            (1980, 1, 1, 0, 0, 0),
        )
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        archive.writestr(info, _release_text(version, "Windows"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_delivery_zip(
    destination: Path,
    installer: Path,
    *,
    installer_name: str,
    platform: str,
) -> None:
    instructions = (
        f"Setuora Lite - {platform}\n\n"
        "1. Extract this ZIP file.\n"
        f"2. Run '{installer_name}'.\n"
        "3. Approve administrator access and follow the guided prompts.\n\n"
        "Keep the window open until it reports that Setuora Lite is fully installed.\n"
    ).encode("utf-8")
    with zipfile.ZipFile(
        destination,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        installer_info = zipfile.ZipInfo(installer_name, (1980, 1, 1, 0, 0, 0))
        installer_info.compress_type = zipfile.ZIP_DEFLATED
        installer_mode = 0o755 if platform == "Linux" else 0o644
        installer_info.external_attr = (stat.S_IFREG | installer_mode) << 16
        archive.writestr(installer_info, installer.read_bytes())

        readme_info = zipfile.ZipInfo("START HERE.txt", (1980, 1, 1, 0, 0, 0))
        readme_info.compress_type = zipfile.ZIP_DEFLATED
        readme_info.external_attr = (stat.S_IFREG | 0o644) << 16
        archive.writestr(readme_info, instructions)


def build_packages(
    version: str,
    output_directory: Path = DEFAULT_OUTPUT,
) -> tuple[Path, Path]:
    if not RELEASE_NAME.fullmatch(version):
        raise ValueError(
            "Version may contain only letters, numbers, dots, underscores, and hyphens."
        )

    output_directory.mkdir(parents=True, exist_ok=True)
    linux_payload = _payload(LINUX_FILES)
    windows_payload = _payload(WINDOWS_FILES)
    _validate_payload(linux_payload)
    _validate_payload(windows_payload)

    linux_root = "Setuora-Lite-linux"
    windows_root = "Setuora-Lite-windows"
    linux_path = output_directory / f"Setuora-Lite-{version}-linux.run"
    windows_path = output_directory / f"Setuora-Lite-{version}-windows.cmd"

    with tempfile.TemporaryDirectory(
        prefix="setuora-lite-package-"
    ) as temporary_directory:
        temporary_path = Path(temporary_directory)
        linux_payload_path = temporary_path / "payload.tar.gz"
        windows_payload_path = temporary_path / "payload.zip"
        _write_tar(
            linux_payload_path,
            linux_root,
            linux_payload,
            version,
        )
        _write_zip(
            windows_payload_path,
            windows_root,
            windows_payload,
            version,
        )

        linux_path.write_bytes(
            LINUX_HEADER.read_bytes() + linux_payload_path.read_bytes()
        )
        linux_path.chmod(0o755)

        encoded_payload = base64.b64encode(windows_payload_path.read_bytes()).decode(
            "ascii"
        )
        wrapped_payload = "\n".join(textwrap.wrap(encoded_payload, width=76)) + "\n"
        windows_path.write_bytes(
            WINDOWS_HEADER.read_bytes() + wrapped_payload.encode("ascii")
        )

    checksum_path = output_directory / f"Setuora-Lite-{version}-SHA256SUMS.txt"
    linux_delivery = output_directory / f"Setuora-Lite-{version}-Linux.zip"
    windows_delivery = output_directory / f"Setuora-Lite-{version}-Windows.zip"
    _write_delivery_zip(
        linux_delivery,
        linux_path,
        installer_name="Install Setuora Lite.run",
        platform="Linux",
    )
    _write_delivery_zip(
        windows_delivery,
        windows_path,
        installer_name="Install Setuora Lite.cmd",
        platform="Windows",
    )
    checksum_path.write_text(
        f"{_sha256(linux_path)}  {linux_path.name}\n"
        f"{_sha256(windows_path)}  {windows_path.name}\n"
        f"{_sha256(linux_delivery)}  {linux_delivery.name}\n"
        f"{_sha256(windows_delivery)}  {windows_delivery.name}\n",
        encoding="utf-8",
    )
    return linux_path, windows_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        default=os.environ.get("SETUORA_RELEASE_VERSION", "pilot"),
        help="release label used in package names (default: pilot)",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    linux_path, windows_path = build_packages(
        args.version,
        args.output.resolve(),
    )
    print(linux_path)
    print(windows_path)
    print(linux_path.parent / f"Setuora-Lite-{args.version}-Linux.zip")
    print(linux_path.parent / f"Setuora-Lite-{args.version}-Windows.zip")
    print(linux_path.parent / f"Setuora-Lite-{args.version}-SHA256SUMS.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
