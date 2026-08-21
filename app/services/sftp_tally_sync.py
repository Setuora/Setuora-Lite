from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import socket
from contextlib import contextmanager
from datetime import UTC, datetime
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any, Iterator
from xml.etree import ElementTree as ET  # nosec B405

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring as safe_fromstring
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    TallySftpDirection,
    TallySftpExchange,
    TallySftpStatus,
    utc_now,
)
from app.services.settings import get_all_settings
from app.services.tally import TallySyncError, post_to_tally
from app.services.tally_masters import TallyDataError, export_tally_parties_xml

logger = logging.getLogger(__name__)
REMOTE_INBOX = "/inbox"
REMOTE_OUTBOX = "/outbox"
REMOTE_ACK = "/ack"


class SftpTallySyncError(RuntimeError):
    pass


def host_key_sha256(key: Any) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


@contextmanager
def sftp_session() -> Iterator[Any]:
    """Open a password-authenticated SFTP session with an exact host-key pin."""

    settings = get_settings()
    configuration_error = settings.sftp_sync_configuration_error
    if configuration_error:
        raise SftpTallySyncError(configuration_error)

    try:
        import paramiko
    except ImportError as exc:  # pragma: no cover - deployment lock installs it
        raise SftpTallySyncError(
            "The Paramiko SFTP runtime dependency is missing."
        ) from exc

    sock: socket.socket | None = None
    transport = None
    client = None
    try:
        sock = socket.create_connection(
            (settings.sftp_host, settings.sftp_port),
            timeout=settings.sftp_connect_timeout_seconds,
        )
        transport = paramiko.Transport(sock)
        transport.banner_timeout = settings.sftp_connect_timeout_seconds
        transport.auth_timeout = settings.sftp_connect_timeout_seconds
        transport.start_client(timeout=settings.sftp_connect_timeout_seconds)
        actual_fingerprint = host_key_sha256(transport.get_remote_server_key())
        if not hmac.compare_digest(
            actual_fingerprint,
            settings.sftp_host_key_sha256,
        ):
            raise SftpTallySyncError(
                "Master SFTP host-key verification failed. Stop and confirm the "
                "server fingerprint with the Master administrator."
            )
        transport.auth_password(
            username=settings.sftp_username,
            password=settings.sftp_password,
        )
        client = paramiko.SFTPClient.from_transport(transport)
        yield client
    except SftpTallySyncError:
        raise
    except (OSError, EOFError, paramiko.SSHException) as exc:
        raise SftpTallySyncError(
            f"Could not connect to the Master SFTP endpoint: {type(exc).__name__}."
        ) from exc
    finally:
        if client is not None:
            client.close()
        if transport is not None:
            transport.close()
        elif sock is not None:
            sock.close()


def _remote_path(directory: str, filename: str) -> str:
    if (
        not filename
        or PurePosixPath(filename).name != filename
        or filename in {".", ".."}
    ):
        raise SftpTallySyncError("Master returned an unsafe SFTP filename.")
    return f"{directory}/{filename}"


def _xml_names(client: Any, directory: str) -> list[str]:
    names = [
        str(item.filename)
        for item in client.listdir_attr(directory)
        if str(item.filename).lower().endswith(".xml")
        and not str(item.filename).startswith(".")
    ]
    for name in names:
        _remote_path(directory, name)
    return sorted(names, key=str.casefold)


def _read_remote_xml(client: Any, remote_path: str, maximum_bytes: int) -> bytes:
    stat = client.stat(remote_path)
    if stat.st_size > maximum_bytes:
        raise SftpTallySyncError(
            f"Master XML exceeds the configured {maximum_bytes}-byte limit."
        )
    with client.open(remote_path, "rb") as source:
        payload = source.read(maximum_bytes + 1)
    if len(payload) > maximum_bytes:
        raise SftpTallySyncError(
            f"Master XML exceeds the configured {maximum_bytes}-byte limit."
        )
    if not payload:
        raise SftpTallySyncError("Master published an empty XML file.")
    return payload


def _write_remote_atomic(client: Any, destination: str, payload: bytes) -> None:
    temporary = f"{destination}.part"
    try:
        client.remove(temporary)
    except OSError:
        pass
    client.putfo(BytesIO(payload), temporary, file_size=len(payload), confirm=True)
    try:
        client.rename(temporary, destination)
    except Exception:
        try:
            client.remove(temporary)
        except OSError:
            pass
        raise


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _validate_master_import_xml(payload: bytes) -> None:
    try:
        root = safe_fromstring(payload)
    except (DefusedXmlException, ET.ParseError, ValueError) as exc:
        raise SftpTallySyncError("Master published unsafe or invalid XML.") from exc
    if root.tag.rsplit("}", 1)[-1].upper() != "ENVELOPE":
        raise SftpTallySyncError("Master XML is not a Tally ENVELOPE.")
    request = next(
        (
            (node.text or "").strip().casefold()
            for node in root.iter()
            if node.tag.rsplit("}", 1)[-1].upper() == "TALLYREQUEST"
        ),
        "",
    )
    if request != "import data":
        raise SftpTallySyncError("Master XML is not a Tally Import Data request.")


def _record_for_remote_file(
    db: Session,
    *,
    filename: str,
    digest: str,
) -> TallySftpExchange:
    record = db.scalar(
        select(TallySftpExchange).where(
            TallySftpExchange.direction == TallySftpDirection.DOWNLOAD.value,
            TallySftpExchange.remote_filename == filename,
        )
    )
    if record is not None:
        if not hmac.compare_digest(record.file_sha256, digest):
            raise SftpTallySyncError(
                "Master reused an outbound filename with different content. "
                "The exchange is paused for operator review."
            )
        return record
    record = TallySftpExchange(
        direction=TallySftpDirection.DOWNLOAD.value,
        remote_filename=filename,
        file_sha256=digest,
        status=TallySftpStatus.PENDING.value,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def _upload_ack(client: Any, filename: str) -> None:
    acknowledgement = f"{PurePosixPath(filename).stem}.ack"
    destination = _remote_path(REMOTE_ACK, acknowledgement)
    _write_remote_atomic(client, destination, b"")


def _import_master_file(
    db: Session,
    client: Any,
    filename: str,
    tally_settings: dict[str, str],
) -> str:
    settings = get_settings()
    remote_path = _remote_path(REMOTE_OUTBOX, filename)
    payload = _read_remote_xml(client, remote_path, settings.sftp_max_xml_bytes)
    _validate_master_import_xml(payload)
    digest = _digest(payload)
    record = _record_for_remote_file(db, filename=filename, digest=digest)

    if record.status != TallySftpStatus.ACKNOWLEDGED.value:
        if record.status != TallySftpStatus.IMPORTED.value:
            record.attempts += 1
            record.last_error = None
            db.commit()
            try:
                result = post_to_tally(payload.decode("utf-8"), tally_settings)
            except (UnicodeDecodeError, TallySyncError) as exc:
                record.status = TallySftpStatus.FAILED.value
                record.last_error = str(exc)[:2000]
                db.commit()
                raise SftpTallySyncError(
                    "Master XML is waiting because Tally did not import it: "
                    f"{str(exc)[:500]}"
                ) from exc
            record.status = TallySftpStatus.IMPORTED.value
            record.tally_reference = result.reference[:255]
            record.last_error = None
            db.commit()

        try:
            _upload_ack(client, filename)
        except Exception as exc:
            record.last_error = (
                f"Tally import succeeded, but acknowledgement upload failed: "
                f"{type(exc).__name__}"
            )[:2000]
            db.commit()
            raise SftpTallySyncError(record.last_error) from exc
        record.status = TallySftpStatus.ACKNOWLEDGED.value
        record.completed_at = utc_now()
        record.last_error = None
        latest_upload = db.scalar(
            select(TallySftpExchange)
            .where(TallySftpExchange.direction == TallySftpDirection.UPLOAD.value)
            .order_by(TallySftpExchange.id.desc())
            .limit(1)
        )
        if latest_upload is not None:
            latest_upload.status = TallySftpStatus.ACKNOWLEDGED.value
            latest_upload.completed_at = utc_now()
        db.commit()
    return "imported_and_acknowledged"


def _upload_tally_export(
    db: Session,
    client: Any,
    tally_settings: dict[str, str],
) -> str:
    settings = get_settings()
    company_name = tally_settings.get("company_name", "").strip()
    try:
        payload = export_tally_parties_xml(tally_settings, company_name)
    except TallyDataError as exc:
        raise SftpTallySyncError(str(exc)) from exc
    if len(payload) > settings.sftp_max_xml_bytes:
        raise SftpTallySyncError(
            f"Tally export exceeds the configured {settings.sftp_max_xml_bytes}-byte limit."
        )
    digest = _digest(payload)
    previous = db.scalar(
        select(TallySftpExchange)
        .where(TallySftpExchange.direction == TallySftpDirection.UPLOAD.value)
        .order_by(TallySftpExchange.id.desc())
        .limit(1)
    )
    if previous is not None and hmac.compare_digest(previous.file_sha256, digest):
        return "idle_unchanged"

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = (
        f"setuora-{settings.franchise_code}-debtors-creditors-{stamp}-{digest[:8]}.xml"
    )
    record = TallySftpExchange(
        direction=TallySftpDirection.UPLOAD.value,
        remote_filename=filename,
        file_sha256=digest,
        status=TallySftpStatus.PENDING.value,
        attempts=1,
    )
    db.add(record)
    db.commit()
    try:
        _write_remote_atomic(
            client,
            _remote_path(REMOTE_INBOX, filename),
            payload,
        )
    except Exception as exc:
        record.status = TallySftpStatus.FAILED.value
        record.last_error = f"SFTP upload failed: {type(exc).__name__}"[:2000]
        db.commit()
        raise SftpTallySyncError(record.last_error) from exc
    record.status = TallySftpStatus.UPLOADED.value
    record.completed_at = utc_now()
    db.commit()
    return "uploaded"


def run_sftp_tally_sync_cycle(
    db: Session,
    *,
    client: Any | None = None,
) -> str:
    """Run one gated Master/Tally exchange cycle for this franchise."""

    settings = get_settings()
    if not settings.sftp_sync_enabled:
        return "disabled"
    if settings.sftp_sync_configuration_error:
        raise SftpTallySyncError(settings.sftp_sync_configuration_error)
    tally_settings = get_all_settings(db)

    def run(connected: Any) -> str:
        outbound = _xml_names(connected, REMOTE_OUTBOX)
        if outbound:
            return _import_master_file(
                db,
                connected,
                outbound[0],
                tally_settings,
            )
        if _xml_names(connected, REMOTE_INBOX):
            return "waiting_for_master"
        return _upload_tally_export(db, connected, tally_settings)

    if client is not None:
        return run(client)
    with sftp_session() as connected:
        return run(connected)


def recent_sftp_exchanges(db: Session, limit: int = 25) -> list[TallySftpExchange]:
    return list(
        db.scalars(
            select(TallySftpExchange).order_by(TallySftpExchange.id.desc()).limit(limit)
        ).all()
    )
