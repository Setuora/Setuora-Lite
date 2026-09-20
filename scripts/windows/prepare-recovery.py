"""Create a verified recovery bundle before the Windows app is removed."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def verify_snapshot(path: Path) -> None:
    if not path.is_file():
        raise RuntimeError(f"Recovery backup is missing: {path}")
    connection = sqlite3.connect(str(path))
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity != ("ok",):
            raise RuntimeError(f"Recovery backup failed SQLite integrity check: {integrity}")
        connection.execute("PRAGMA foreign_keys=ON")
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("Recovery backup failed SQLite foreign-key check")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not {"users", "settings"}.issubset(tables):
            raise RuntimeError("Recovery backup is not a Setuora database")
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(f"Recovery backup is not readable SQLite: {exc}") from exc
    finally:
        connection.close()


def create_snapshot(database: Path | None, existing_backup: Path | None, target: Path) -> bool:
    """Write and verify a fresh copy; do not replace a previous recovery bundle."""
    source = database if database and database.is_file() else existing_backup
    if source is None:
        raise RuntimeError("No live database or verified existing backup is available; removal was cancelled")
    if target.exists():
        raise RuntimeError(f"Recovery backup already exists: {target}")
    stage = target.with_name(f".{target.name}.tmp")
    if stage.exists():
        raise RuntimeError(f"Unfinished recovery backup already exists: {stage}")
    try:
        if database and database.is_file():
            source_connection = sqlite3.connect(str(database))
            try:
                target_connection = sqlite3.connect(str(stage))
                try:
                    source_connection.backup(target_connection)
                finally:
                    target_connection.close()
            finally:
                source_connection.close()
        else:
            verify_snapshot(source)
            shutil.copy2(source, stage)
        verify_snapshot(stage)
        stage.replace(target)
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(f"Cannot create a verified SQLite backup: {exc}") from exc
    finally:
        stage.unlink(missing_ok=True)
    return True


def _linked(path: Path) -> bool:
    if path.is_symlink():
        return True
    return bool(getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _copy_safe(source: Path, target: Path, excluded: set[Path]) -> None:
    if source.resolve() in excluded:
        return
    if _linked(source):
        raise RuntimeError(f"Recovery stopped at a linked data path: {source}")
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=False)
        for child in source.iterdir():
            _copy_safe(child, target / child.name, excluded)
    elif source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    else:
        raise RuntimeError(f"Recovery cannot copy this data path: {source}")


def copy_recovery_settings(
    root: Path,
    destination: Path,
    database: Path,
    configured_paths: dict[str, str] | None = None,
) -> list[str]:
    """Retain credentials and auxiliary data, but only the newly verified DB backup."""
    kept: list[str] = []
    env = root / ".env"
    if env.exists():
        _copy_safe(env, destination / ".env", set())
        kept.append(".env")
    data = root / "data"
    if data.exists():
        excluded = {
            database.resolve(),
            Path(str(database) + "-wal").resolve(),
            Path(str(database) + "-shm").resolve(),
            (data / "backups").resolve(),
        }
        _copy_safe(data, destination / "data", excluded)
        kept.append("data (excluding the live database and old local backups)")
    for key, raw in (configured_paths or {}).items():
        if not raw:
            continue
        source = Path(raw).expanduser()
        if not source.is_absolute():
            source = root / source
        resolved = source.resolve()
        if resolved == root:
            raise RuntimeError(f"{key} points at the entire application folder")
        if not resolved.is_relative_to(root) or not source.exists():
            continue  # External paths are left in place by application removal.
        _copy_safe(source, destination / "configured" / key / source.name, set())
        kept.append(f"{key}: {source.relative_to(root)}")
    return kept


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def newest_verified_backup(candidates: list[Path]) -> Path | None:
    for candidate in sorted(candidates, key=lambda path: path.stat().st_mtime_ns, reverse=True):
        try:
            verify_snapshot(candidate)
        except RuntimeError:
            continue
        return candidate
    return None


def prepare(root: Path, destination: Path, product: str) -> dict[str, object]:
    root = root.resolve()
    destination = destination.resolve()
    if destination == root or destination.is_relative_to(root):
        raise RuntimeError("Recovery must be outside the installation folder")
    if product not in {"Master", "Lite"}:
        raise RuntimeError("Unknown Setuora product")
    if not root.is_dir() or not destination.is_dir():
        raise RuntimeError("Installation or recovery folder is missing")

    os.chdir(root)
    sys.path.insert(0, str(root))
    from app.services.backup import list_backup_files, sqlite_database_path

    database = sqlite_database_path()
    previous = None
    if not database.is_file():
        previous = newest_verified_backup(list_backup_files())
    backup = destination / "setuora-latest-backup.db"
    has_backup = create_snapshot(database, previous, backup)
    path_keys = (
        "MASTER_CONNECTION_SETTINGS_FILE",
        "SFTP_CONNECTION_SETTINGS_FILE",
        "BACKUP_SETTINGS_FILE",
        "SFTP_EXCHANGE_ROOT",
        "BACKUP_DIRECTORY",
        "BACKUP_OFFSITE_DIRECTORY",
    )
    retained = copy_recovery_settings(
        root,
        destination,
        database,
        {key: os.getenv(key, "") for key in path_keys},
    )
    commit = None
    if (root / ".git").exists():
        try:
            result = subprocess.run(  # noqa: S603
                ["git", "-C", str(root), "rev-parse", "HEAD"],  # noqa: S607
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                commit = result.stdout.strip()
        except OSError:
            pass
    manifest: dict[str, object] = {
        "product": product,
        "created_utc": datetime.now(UTC).isoformat(),
        "source_root": str(root),
        "source_database": str(database),
        "source_commit": commit,
        "backup_file": backup.name if has_backup else None,
        "backup_sha256": _sha256(backup) if has_backup else None,
        "retained_settings": retained,
    }
    (destination / "recovery-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--product", choices=("Master", "Lite"), required=True)
    args = parser.parse_args()
    manifest = prepare(args.root, args.destination, args.product)
    print(f"Recovery bundle prepared: {args.destination}")
    if manifest["backup_file"]:
        print(f"Verified database backup: {manifest['backup_file']}")
    else:
        print("No database or existing backup was present to retain.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, sqlite3.DatabaseError) as exc:
        print(f"Recovery preparation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
