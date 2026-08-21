"""Build the shareable Windows Setuora Lite installer."""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import re
import stat
import tempfile
import textwrap
import zipfile
from pathlib import Path, PurePosixPath

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "dist"
RELEASE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
COMMON_FILES = (
    ".env.example",
    "deploy.py",
    "requirements-runtime.lock",
    "client/CLIENT-README.md",
)
COMMON_DIRECTORIES = (
    "app",
    "docs/deployment",
    "scripts/windows",
)
WINDOWS_FILES = {"client/windows/setuora.ps1": "setuora.ps1"}
WINDOWS_HEADER = PROJECT_ROOT / "client/windows/self-extract-header.cmd"


def _payload() -> dict[str, Path]:
    payload = {name: PROJECT_ROOT / name for name in COMMON_FILES}
    for directory_name in COMMON_DIRECTORIES:
        directory = PROJECT_ROOT / directory_name
        for source in sorted(directory.rglob("*")):
            if source.is_file() and "__pycache__" not in source.parts:
                payload[source.relative_to(PROJECT_ROOT).as_posix()] = source
    payload["CLIENT-README.md"] = payload.pop("client/CLIENT-README.md")
    for source_name, archive_name in WINDOWS_FILES.items():
        payload[archive_name] = PROJECT_ROOT / source_name
    return payload


def _validate_payload(payload: dict[str, Path]) -> None:
    forbidden = {".env", ".git", "data", "archive", "__pycache__"}
    for archive_name, source in payload.items():
        if set(PurePosixPath(archive_name).parts) & forbidden:
            raise ValueError(
                f"Refusing to package private or archived path: {archive_name}"
            )
        if not source.is_file():
            raise FileNotFoundError(source)


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
        archive.writestr(
            info,
            (
                f"Setuora Lite\nVersion: {version}\nPlatform: Windows\n"
                "Persistent data is stored under C:\\ProgramData\\Setuora.\n"
            ).encode(),
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_package(
    version: str,
    output_directory: Path = DEFAULT_OUTPUT,
) -> Path:
    if not RELEASE_NAME.fullmatch(version):
        raise ValueError(
            "Version may contain only letters, numbers, dots, underscores, and hyphens."
        )
    output_directory.mkdir(parents=True, exist_ok=True)
    payload = _payload()
    _validate_payload(payload)
    root_name = "Setuora-Lite-windows"
    windows_path = output_directory / f"Setuora-Lite-{version}-windows.cmd"
    with tempfile.TemporaryDirectory(prefix="setuora-package-") as temporary:
        payload_path = Path(temporary) / "payload.zip"
        _write_zip(payload_path, root_name, payload, version)
        encoded = base64.b64encode(payload_path.read_bytes()).decode("ascii")
        wrapped = "\n".join(textwrap.wrap(encoded, width=76)) + "\n"
        windows_path.write_bytes(WINDOWS_HEADER.read_bytes() + wrapped.encode("ascii"))
    checksum_path = output_directory / f"Setuora-Lite-{version}-SHA256SUMS.txt"
    checksum_path.write_text(
        f"{_sha256(windows_path)}  {windows_path.name}\n",
        encoding="utf-8",
    )
    return windows_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        default=os.environ.get("SETUORA_RELEASE_VERSION", "pilot"),
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    windows_path = build_package(args.version, args.output.resolve())
    print(windows_path)
    print(windows_path.parent / f"Setuora-Lite-{args.version}-SHA256SUMS.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
