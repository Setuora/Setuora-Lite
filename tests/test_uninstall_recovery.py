"""Recovery bundles must be usable after uninstall removes the application folder."""

from __future__ import annotations

import importlib.util
import os
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/windows/prepare-recovery.py"
SPEC = importlib.util.spec_from_file_location("prepare_recovery", SCRIPT)
assert SPEC and SPEC.loader
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)


def _database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO users (name) VALUES ('original')")


def test_uninstall_snapshot_is_verified_and_independent_of_live_database(tmp_path: Path) -> None:
    live = tmp_path / "live.db"
    kept = tmp_path / "recovery.db"
    _database(live)

    assert recovery.create_snapshot(live, None, kept)
    with sqlite3.connect(live) as connection:
        connection.execute("UPDATE users SET name='changed'")
    with sqlite3.connect(kept) as connection:
        assert connection.execute("SELECT name FROM users").fetchone() == ("original",)
    recovery.verify_snapshot(kept)
    with pytest.raises(RuntimeError, match="already exists"):
        recovery.create_snapshot(live, None, kept)


def test_uninstall_rejects_corrupt_backup_and_keeps_settings(tmp_path: Path) -> None:
    bad = tmp_path / "broken.db"
    bad.write_bytes(b"not sqlite")
    with pytest.raises(RuntimeError, match="SQLite"):
        recovery.create_snapshot(bad, None, tmp_path / "recovery.db")

    root = tmp_path / "installed"
    (root / "data/backups").mkdir(parents=True)
    (root / ".env").write_text("APP_SECRET_KEY=private\n", encoding="utf-8")
    (root / "data/master-connection.env").write_text("TOKEN=private\n", encoding="utf-8")
    (root / "data/backups/old.db").write_bytes(b"old")
    (root / "custom").mkdir()
    (root / "custom/node-credentials.env").write_text("NODE=private\n", encoding="utf-8")
    live = root / "data/setuora.db"
    _database(live)
    target = tmp_path / "kept"
    target.mkdir()

    recovery.copy_recovery_settings(
        root, target, live,
        {"MASTER_CONNECTION_SETTINGS_FILE": "custom/node-credentials.env"},
    )
    assert (target / ".env").read_text(encoding="utf-8") == "APP_SECRET_KEY=private\n"
    assert (target / "data/master-connection.env").is_file()
    assert not (target / "data/setuora.db").exists()
    assert not (target / "data/backups").exists()
    assert (target / "configured/MASTER_CONNECTION_SETTINGS_FILE/node-credentials.env").read_text(
        encoding="utf-8"
    ) == "NODE=private\n"


def test_uninstall_keeps_latest_existing_backup_when_live_database_is_missing(tmp_path: Path) -> None:
    previous = tmp_path / "previous.db"
    _database(previous)
    kept = tmp_path / "recovery.db"

    assert recovery.create_snapshot(tmp_path / "missing.db", previous, kept)
    recovery.verify_snapshot(kept)
    assert kept.read_bytes() == previous.read_bytes()


def test_uninstall_requires_a_valid_backup_and_selects_newest_verified_copy(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="No live database"):
        recovery.create_snapshot(None, None, tmp_path / "recovery.db")

    older = tmp_path / "older.db"
    newer = tmp_path / "newer.db"
    corrupt = tmp_path / "corrupt.db"
    for path in (older, newer):
        _database(path)
    corrupt.write_bytes(b"broken")
    os.utime(older, ns=(1_000_000_000, 1_000_000_000))
    os.utime(newer, ns=(2_000_000_000, 2_000_000_000))
    os.utime(corrupt, ns=(3_000_000_000, 3_000_000_000))
    assert recovery.newest_verified_backup([older, corrupt, newer]) == newer
