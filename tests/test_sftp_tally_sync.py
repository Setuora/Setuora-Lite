from io import BytesIO
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import TallySftpDirection, TallySftpExchange, TallySftpStatus
from app.services import sftp_tally_sync as sync
from app.services.tally import TallySyncError

PARTY_EXPORT = b"""<ENVELOPE><BODY><DATA><COLLECTION>
<LEDGER NAME="Customer One"><NAME>Customer One</NAME><PARENT>Sundry Debtors</PARENT></LEDGER>
</COLLECTION></DATA></BODY></ENVELOPE>"""
MASTER_IMPORT = b"""<?xml version="1.0" encoding="utf-8"?>
<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
<BODY><IMPORTDATA><REQUESTDATA><TALLYMESSAGE>
<LEDGER NAME="Customer One" ACTION="Create"><NAME>Customer One</NAME>
<PARENT>Sundry Debtors</PARENT></LEDGER>
</TALLYMESSAGE></REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>"""


class FakeSftp:
    def __init__(self, files=None):
        self.files = dict(files or {})

    def listdir_attr(self, directory):
        prefix = directory.rstrip("/") + "/"
        return [
            SimpleNamespace(filename=path.removeprefix(prefix))
            for path in sorted(self.files)
            if path.startswith(prefix) and "/" not in path.removeprefix(prefix)
        ]

    def stat(self, path):
        return SimpleNamespace(st_size=len(self.files[path]))

    def open(self, path, _mode):
        return BytesIO(self.files[path])

    def putfo(self, source, path, *, file_size, confirm):
        payload = source.read()
        assert len(payload) == file_size
        assert confirm is True
        self.files[path] = payload

    def rename(self, source, destination):
        self.files[destination] = self.files.pop(source)

    def remove(self, path):
        if path not in self.files:
            raise OSError(path)
        del self.files[path]


@pytest.fixture()
def settings(monkeypatch):
    value = SimpleNamespace(
        franchise_code="BLR-01",
        sftp_sync_enabled=True,
        sftp_sync_configuration_error=None,
        sftp_max_xml_bytes=1024 * 1024,
    )
    monkeypatch.setattr(sync, "get_settings", lambda: value)
    monkeypatch.setattr(
        sync,
        "get_all_settings",
        lambda _db: {
            "company_name": "Franchise Books",
            "tally_host": "127.0.0.1",
            "tally_port": "9000",
        },
    )
    return value


def test_uploads_tally_export_atomically_and_skips_unchanged(
    db_session,
    monkeypatch,
    settings,
):
    client = FakeSftp()
    monkeypatch.setattr(sync, "export_tally_parties_xml", lambda *_args: PARTY_EXPORT)

    assert sync.run_sftp_tally_sync_cycle(db_session, client=client) == "uploaded"
    uploaded = [path for path in client.files if path.startswith("/inbox/")]
    assert len(uploaded) == 1
    assert uploaded[0].endswith(".xml")
    assert not any(path.endswith(".part") for path in client.files)

    record = db_session.scalar(select(TallySftpExchange))
    assert record.direction == TallySftpDirection.UPLOAD.value
    assert record.status == TallySftpStatus.UPLOADED.value
    client.files.clear()  # Master consumed the upload and has not published a change.
    assert sync.run_sftp_tally_sync_cycle(db_session, client=client) == "idle_unchanged"


def test_master_outbox_is_imported_then_acknowledged_before_next_upload(
    db_session,
    monkeypatch,
    settings,
):
    filename = "setuora-BLR-01-debtors-creditors-20260822T010000Z.xml"
    client = FakeSftp({f"/outbox/{filename}": MASTER_IMPORT})
    calls = []
    monkeypatch.setattr(
        sync,
        "post_to_tally",
        lambda xml, tally_settings: (
            calls.append((xml, tally_settings))
            or SimpleNamespace(reference="CREATED=1; ALTERED=0")
        ),
    )
    monkeypatch.setattr(
        sync,
        "export_tally_parties_xml",
        lambda *_args: pytest.fail("a new export must remain gated"),
    )

    assert sync.run_sftp_tally_sync_cycle(db_session, client=client) == (
        "imported_and_acknowledged"
    )
    assert len(calls) == 1
    assert f"/ack/{filename.removesuffix('.xml')}.ack" in client.files
    record = db_session.scalar(select(TallySftpExchange))
    assert record.status == TallySftpStatus.ACKNOWLEDGED.value
    assert record.tally_reference == "CREATED=1; ALTERED=0"


def test_tally_import_failure_leaves_master_unacknowledged_and_pauses(
    db_session,
    monkeypatch,
    settings,
):
    filename = "setuora-BLR-01-debtors-creditors-20260822T020000Z.xml"
    client = FakeSftp({f"/outbox/{filename}": MASTER_IMPORT})

    def reject(*_args):
        raise TallySyncError("Tally rejected Customer One", retryable=False)

    monkeypatch.setattr(sync, "post_to_tally", reject)
    with pytest.raises(sync.SftpTallySyncError, match="waiting"):
        sync.run_sftp_tally_sync_cycle(db_session, client=client)

    assert not any(path.startswith("/ack/") for path in client.files)
    record = db_session.scalar(select(TallySftpExchange))
    assert record.status == TallySftpStatus.FAILED.value
    assert "Tally rejected" in record.last_error


def test_pending_master_inbox_blocks_another_franchise_export(
    db_session,
    monkeypatch,
    settings,
):
    client = FakeSftp({"/inbox/already-uploaded.xml": PARTY_EXPORT})
    monkeypatch.setattr(
        sync,
        "export_tally_parties_xml",
        lambda *_args: pytest.fail("pending upload must be gated"),
    )
    assert sync.run_sftp_tally_sync_cycle(db_session, client=client) == (
        "waiting_for_master"
    )


def test_master_xml_rejects_entities():
    unsafe = b'<!DOCTYPE x [<!ENTITY file SYSTEM "file:///etc/passwd">]><ENVELOPE>&file;</ENVELOPE>'
    with pytest.raises(sync.SftpTallySyncError, match="unsafe"):
        sync._validate_master_import_xml(unsafe)
