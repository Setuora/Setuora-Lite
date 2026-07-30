"""Durable Lite-to-Master synchronization.

User transactions only call the enqueue/apply helpers in this module.  Network
I/O is intentionally isolated in ``push_pending_events`` and
``poll_master_commands`` so it can be run by a background worker without
holding an inventory transaction open.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import re
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Batch,
    BatchStatus,
    MasterInboxCommand,
    MasterInboxStatus,
    MasterOutboxEvent,
    MasterOutboxStatus,
    Serial,
    SerialStatus,
    utc_now,
)


EVENT_SCHEMA_VERSION = 1
COMMAND_SCHEMA_VERSION = 1
INITIAL_INVENTORY_AGGREGATE_TYPE = "INITIAL_INVENTORY"
INITIAL_INVENTORY_REFERENCE_SEGMENT = "INITIAL-INVENTORY"
MASTER_EVENT_MAX_ITEMS = 5000
MASTER_EVENT_MAX_BODY_BYTES = 5 * 1024 * 1024
MASTER_ERROR_MAX_BODY_BYTES = 64 * 1024
MASTER_ERROR_MAX_TEXT_CHARS = 500
MASTER_ERROR_MAX_DETAILS_CHARS = 700
_MAX_SQLITE_SEQUENCE = (1 << 63) - 1
_EVENT_ID_SIZE_PLACEHOLDER = "00000000-0000-0000-0000-000000000000"
RETRYABLE_HTTP_STATUSES = {408, 425, 429}
# Authentication failures are retried because credentials can be rotated
# without changing the frozen event. Transient HTTP/network failures and
# Master's documented concurrent-ingest conflict are also safe to replay.
# Sequence/domain conflicts and other 4xx responses require explicit admin
# reconciliation and retry.
RETRYABLE_CREDENTIAL_STATUSES = {401, 403}
RETRYABLE_CONFLICT_ERROR_CODES = {"CONCURRENT_EVENT_CONFLICT"}
FRANCHISE_CODE_PLACEHOLDERS = {
    "CHANGE-ME",
    "CHANGEME",
    "DEFAULT",
    "FRANCHISE",
    "FRANCHISE-CODE",
    "LITE",
    "MASTER",
    "SETUORA",
    "YOUR-FRANCHISE",
    "YOUR-FRANCHISE-CODE",
}


class MasterSyncError(ValueError):
    pass


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _setting(name: str, default: Any = None) -> Any:
    return getattr(get_settings(), name, default)


def master_sync_enabled() -> bool:
    return _as_bool(_setting("master_sync_enabled", False))


def normalize_franchise_code(value: Any) -> str:
    raw = str(value or "").strip().upper()
    return re.sub(r"[^A-Z0-9]+", "-", raw).strip("-")


def configured_franchise_code(*, required: bool | None = None) -> str:
    code = normalize_franchise_code(_setting("franchise_code", ""))
    must_exist = master_sync_enabled() if required is None else required
    if must_exist and (not code or code in FRANCHISE_CODE_PLACEHOLDERS):
        raise MasterSyncError(
            "Configure a unique FRANCHISE_CODE before enabling Setuora Master sync."
        )
    if code and len(code) > 40:
        raise MasterSyncError("FRANCHISE_CODE must be 40 characters or fewer.")
    return code


def _initial_inventory_reference_prefix(franchise_code: str) -> str:
    return f"{franchise_code}:{INITIAL_INVENTORY_REFERENCE_SEGMENT}:"


def initial_inventory_is_queued(
    db: Session,
    *,
    franchise_code: str | None = None,
) -> bool:
    """Return whether this permanent franchise identity has a baseline marker."""

    code = franchise_code or configured_franchise_code(required=True)
    marker_id = db.scalar(
        select(MasterOutboxEvent.id)
        .where(
            MasterOutboxEvent.aggregate_type
            == INITIAL_INVENTORY_AGGREGATE_TYPE,
            MasterOutboxEvent.aggregate_id.startswith(
                _initial_inventory_reference_prefix(code)
            ),
        )
        .limit(1)
    )
    return marker_id is not None


def require_initial_inventory_queued(db: Session) -> str:
    """Fail closed until the baseline for the configured identity is durable."""

    franchise_code = configured_franchise_code(required=True)
    if not initial_inventory_is_queued(db, franchise_code=franchise_code):
        raise MasterSyncError(
            f"Initialize inventory for franchise {franchise_code} from Master "
            "Connection before recording Lite stock changes."
        )
    return franchise_code


def _json_ready(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        _json_ready(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def payload_sha256(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def _utf8_size(value: str) -> int:
    return len(value.encode("utf-8"))


def _frozen_event_json(
    *,
    event_id: str,
    sequence: int,
    event_type: str,
    aggregate_id: str,
    payload: dict[str, Any],
    occurred_at: datetime,
    schema_version: int,
) -> str:
    event = dict(payload)
    event.update(
        {
            "schema_version": schema_version,
            "event_id": event_id,
            "sequence": sequence,
            "type": event_type,
            "occurred_at": occurred_at,
        }
    )
    event.setdefault("reference", aggregate_id)
    event.setdefault("actor", None)
    event.setdefault("items", [])
    return canonical_json({"events": [event]})


def _validate_frozen_event_request(
    payload_json: str,
    *,
    item_count: int,
) -> None:
    body_size = _utf8_size(payload_json)
    if item_count > MASTER_EVENT_MAX_ITEMS:
        raise MasterSyncError(
            f"Master accepts at most {MASTER_EVENT_MAX_ITEMS:,} items in one "
            "event. Split this transaction into smaller transactions and retry."
        )
    if body_size > MASTER_EVENT_MAX_BODY_BYTES:
        raise MasterSyncError(
            f"Master event request is {body_size:,} UTF-8 bytes; the limit is "
            f"{MASTER_EVENT_MAX_BODY_BYTES:,} bytes. Split this transaction "
            "into smaller transactions and retry."
        )


def _iso(value: datetime | date | None) -> str | None:
    return _json_ready(value) if value is not None else None


def network_event_item(
    serial: Serial,
    *,
    rate: float | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Flatten a product/serial snapshot to Master's strict v1 item schema."""

    product = serial.product
    return {
        "serial_number": serial.serial_number,
        "product_code": product.product_code,
        "product_name": product.product_name,
        "tally_stock_item_name": product.tally_stock_item_name,
        "hsn": product.hsn,
        "gst_rate": float(product.gst_rate or 0),
        "unit": product.unit,
        "rate": float(rate if rate is not None else product.default_rate or 0),
        "status": status or serial.status,
        "product_batch_number": serial.product_batch_number,
        "mfg_date": _iso(serial.mfg_date),
        "expiry_date": _iso(serial.expiry_date),
        "warehouse": serial.warehouse,
    }


def enqueue_outbox_event(
    db: Session,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict[str, Any],
    occurred_at: datetime | None = None,
    schema_version: int = EVENT_SCHEMA_VERSION,
    batch_id: int | None = None,
    _initial_inventory: bool = False,
) -> MasterOutboxEvent:
    """Insert an event in the caller's transaction.

    A placeholder insert reserves the AUTOINCREMENT sequence.  The complete
    canonical envelope, including that sequence, is then frozen before the
    caller commits.
    """

    normalized_aggregate_type = aggregate_type.strip().upper()
    configured_franchise_code(required=True)
    if _initial_inventory:
        if normalized_aggregate_type != INITIAL_INVENTORY_AGGREGATE_TYPE:
            raise MasterSyncError(
                "Only an initial-inventory event may bypass Lite initialization."
            )
    else:
        require_initial_inventory_queued(db)

    event_id = str(uuid4())
    event_type_value = event_type.strip().upper()
    row = MasterOutboxEvent(
        event_id=event_id,
        event_type=event_type_value,
        schema_version=schema_version,
        aggregate_type=normalized_aggregate_type,
        aggregate_id=str(aggregate_id),
        batch_id=batch_id,
        payload_json="",
        payload_sha256="",
        status=MasterOutboxStatus.PENDING.value,
    )
    db.add(row)
    db.flush()
    # Authentication identifies the franchise.  The frozen body below is the
    # exact request accepted by Master's strict EventBatchRequest schema.
    payload_json = _frozen_event_json(
        event_id=event_id,
        sequence=row.id,
        event_type=event_type_value,
        aggregate_id=row.aggregate_id,
        payload=payload,
        occurred_at=occurred_at or utc_now(),
        schema_version=schema_version,
    )
    items = payload.get("items", [])
    _validate_frozen_event_request(
        payload_json,
        item_count=len(items) if isinstance(items, (list, tuple, set)) else 0,
    )
    row.payload_json = payload_json
    row.payload_sha256 = payload_sha256(row.payload_json)
    db.flush()
    return row


def enqueue_batch_submitted_event(
    db: Session,
    batch: Batch,
    *,
    user: Any | None = None,
) -> MasterOutboxEvent:
    franchise_code = configured_franchise_code(required=True)
    actor = user or batch.user
    items = sorted(batch.items, key=lambda item: (item.id or 0, item.serial.serial_number))
    # Master has no QR_ASSIGNMENT wire type; a completed assignment is the
    # initial authoritative stock snapshot for those newly activated QRs.
    network_event_type = (
        "STOCK_SNAPSHOT"
        if batch.batch_type == "QR_ASSIGNMENT"
        else batch.batch_type
    )
    payload = {
        "reference": batch.tally_reference or batch.batch_number,
        "actor": getattr(actor, "username", None),
        "items": [
            network_event_item(item.serial, rate=item.rate)
            for item in items
        ],
        "party_name": batch.party_name,
        "party_state": batch.party_state,
        "party_gst_registration_type": batch.party_gst_registration_type,
        "party_gst_name": batch.party_gst_name,
        "party_gstin": batch.party_gstin,
        "gst_treatment": batch.gst_treatment,
        "gst_cgst_rate": batch.gst_cgst_rate,
        "gst_sgst_rate": batch.gst_sgst_rate,
        "gst_igst_rate": batch.gst_igst_rate,
        "reason_code": batch.reason_code,
    }
    return enqueue_outbox_event(
        db,
        event_type=network_event_type,
        aggregate_type="BATCH",
        aggregate_id=f"{franchise_code}:{batch.batch_number}",
        payload=payload,
        occurred_at=batch.submitted_at,
        batch_id=batch.id,
    )


# Backwards-friendly name for worker/service code.
enqueue_submitted_batch = enqueue_batch_submitted_event


def _initial_inventory_reference(franchise_code: str, part_number: int) -> str:
    return (
        f"{franchise_code}:{INITIAL_INVENTORY_REFERENCE_SEGMENT}:"
        f"PART-{part_number}"
    )


def _initial_inventory_payload(
    *,
    reference: str,
    actor: Any,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "reference": reference,
        "actor": getattr(actor, "username", None),
        "reason_code": "INITIAL_ENROLLMENT",
        "items": items,
    }


def _conservative_initial_inventory_body_size(
    *,
    reference: str,
    actor: Any,
    occurred_at: datetime,
    item_json_sizes: list[int],
) -> int:
    """Exact UTF-8 size with a conservative maximum-width SQLite sequence."""

    empty_payload = _initial_inventory_payload(
        reference=reference,
        actor=actor,
        items=[],
    )
    envelope_size = _utf8_size(
        _frozen_event_json(
            event_id=_EVENT_ID_SIZE_PLACEHOLDER,
            sequence=_MAX_SQLITE_SEQUENCE,
            event_type="STOCK_SNAPSHOT",
            aggregate_id=reference,
            payload=empty_payload,
            occurred_at=occurred_at,
            schema_version=EVENT_SCHEMA_VERSION,
        )
    )
    return envelope_size + sum(item_json_sizes) + max(0, len(item_json_sizes) - 1)


def _partition_initial_inventory_items(
    items: list[dict[str, Any]],
    *,
    franchise_code: str,
    actor: Any,
    occurred_at: datetime,
) -> list[list[dict[str, Any]]]:
    """Pack baseline items under both Master v1 request limits."""

    chunks: list[list[dict[str, Any]]] = []
    current_items: list[dict[str, Any]] = []
    current_content_size = 0
    part_number = 1
    reference = _initial_inventory_reference(franchise_code, part_number)
    envelope_size = _conservative_initial_inventory_body_size(
        reference=reference,
        actor=actor,
        occurred_at=occurred_at,
        item_json_sizes=[],
    )

    for item in items:
        item_size = _utf8_size(canonical_json(item))
        item_addition = item_size + (1 if current_items else 0)
        candidate_fits = (
            len(current_items) + 1 <= MASTER_EVENT_MAX_ITEMS
            and envelope_size + current_content_size + item_addition
            <= MASTER_EVENT_MAX_BODY_BYTES
        )
        if not candidate_fits and current_items:
            chunks.append(current_items)
            current_items = []
            part_number += 1
            reference = _initial_inventory_reference(franchise_code, part_number)
            envelope_size = _conservative_initial_inventory_body_size(
                reference=reference,
                actor=actor,
                occurred_at=occurred_at,
                item_json_sizes=[],
            )
            current_content_size = 0
            item_addition = item_size

        if not current_items:
            single_item_size = envelope_size + item_size
            if single_item_size > MASTER_EVENT_MAX_BODY_BYTES:
                raise MasterSyncError(
                    "One initial-inventory item exceeds Master's UTF-8 request "
                    "size limit. Shorten its product or stock text before retrying."
                )

        current_items.append(item)
        current_content_size += item_addition

    if current_items:
        chunks.append(current_items)
    return chunks


def enqueue_initial_inventory_snapshot(
    db: Session,
    *,
    actor: Any,
    commit: bool = True,
) -> tuple[int, int]:
    """Queue the one-time available-stock baseline before normal v1 events."""

    if db.scalar(select(MasterOutboxEvent.id).limit(1)) is not None:
        raise MasterSyncError(
            "Initial inventory can only be queued before the first outbox event."
        )
    franchise_code = configured_franchise_code(required=True)
    serials = db.scalars(
        select(Serial)
        .where(
            Serial.active.is_(True),
            Serial.status.in_(
                {
                    SerialStatus.GENERATED.value,
                    SerialStatus.IN_STOCK.value,
                }
            ),
        )
        .order_by(Serial.id)
    ).all()
    initialization_occurred_at = utc_now()
    event_count = 0
    if not serials:
        event_count = 1
        reference = (
            f"{franchise_code}:{INITIAL_INVENTORY_REFERENCE_SEGMENT}:EMPTY"
        )
        enqueue_outbox_event(
            db,
            event_type="HEARTBEAT",
            aggregate_type=INITIAL_INVENTORY_AGGREGATE_TYPE,
            aggregate_id=reference,
            payload={
                "reference": reference,
                "actor": getattr(actor, "username", None),
                "reason_code": "INITIAL_ENROLLMENT",
                "items": [],
            },
            occurred_at=initialization_occurred_at,
            _initial_inventory=True,
        )
    else:
        chunks = _partition_initial_inventory_items(
            [network_event_item(serial) for serial in serials],
            franchise_code=franchise_code,
            actor=actor,
            occurred_at=initialization_occurred_at,
        )
        for event_count, chunk in enumerate(chunks, start=1):
            reference = _initial_inventory_reference(franchise_code, event_count)
            enqueue_outbox_event(
                db,
                event_type="STOCK_SNAPSHOT",
                aggregate_type=INITIAL_INVENTORY_AGGREGATE_TYPE,
                aggregate_id=reference,
                payload=_initial_inventory_payload(
                    reference=reference,
                    actor=actor,
                    items=chunk,
                ),
                occurred_at=initialization_occurred_at,
                _initial_inventory=True,
            )
    if commit:
        db.commit()
    else:
        db.flush()
    return event_count, len(serials)


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_error(exc: BaseException, *, api_key: str = "") -> str:
    if isinstance(exc, HTTPError):
        message = f"Master returned HTTP {exc.code}."
    elif isinstance(exc, URLError):
        message = f"Master connection failed: {exc.reason}"
    else:
        message = f"Master connection failed: {exc}"
    if api_key:
        message = message.replace(api_key, "[redacted]")
    return message[:1000]


def _bounded_remote_text(
    value: Any,
    *,
    limit: int,
    redact: str = "",
) -> str:
    if not isinstance(value, str):
        return ""
    normalized = " ".join(value.split())
    if redact:
        normalized = normalized.replace(redact, "[redacted]")
    return normalized[:limit]


_REMOTE_DETAIL_PRIORITY = (
    "expected_sequence",
    "received_sequence",
    "last_sequence",
    "next_sequence",
    "serial_number",
    "transfer_id",
    "status_from",
    "status_to",
    "current_status",
    "owner_franchise_code",
)


def _bounded_remote_value(
    value: Any,
    *,
    depth: int = 0,
    redact: str = "",
) -> Any:
    if value is None or type(value) in {bool, int, float}:
        return value
    if isinstance(value, str):
        return _bounded_remote_text(value, limit=240, redact=redact)
    if depth >= 2:
        return None
    if isinstance(value, dict):
        ordered_keys = [
            key for key in _REMOTE_DETAIL_PRIORITY if key in value
        ]
        ordered_keys.extend(
            sorted(
                str(key)
                for key in value
                if str(key) not in ordered_keys
            )
        )
        result: dict[str, Any] = {}
        for key in ordered_keys[:16]:
            bounded_key = _bounded_remote_text(
                str(key),
                limit=80,
                redact=redact,
            )
            if not bounded_key:
                continue
            bounded_value = _bounded_remote_value(
                value.get(key),
                depth=depth + 1,
                redact=redact,
            )
            if bounded_value is not None:
                result[bounded_key] = bounded_value
        return result
    if isinstance(value, list):
        return [
            bounded
            for item in value[:12]
            if (
                bounded := _bounded_remote_value(
                    item,
                    depth=depth + 1,
                    redact=redact,
                )
            )
            is not None
        ]
    return None


def _master_http_error_summary(
    status: int,
    body: bytes,
    *,
    api_key: str,
) -> str:
    summary = f"Master returned HTTP {status}."
    error = _bounded_master_error_envelope(body)
    if error is not None:
        code = _bounded_remote_text(
            error.get("code"),
            limit=80,
            redact=api_key,
        )
        message = _bounded_remote_text(
            error.get("message"),
            limit=MASTER_ERROR_MAX_TEXT_CHARS,
            redact=api_key,
        )
        heading = f"Master HTTP {status}"
        if code:
            heading += f" {code}"
        summary = f"{heading}: {message}" if message else f"{heading}."
        details = _bounded_remote_value(
            error.get("details"),
            redact=api_key,
        )
        if details is not None and details != {} and details != []:
            details_json = canonical_json(details)
            if len(details_json) > MASTER_ERROR_MAX_DETAILS_CHARS:
                details_json = (
                    details_json[:MASTER_ERROR_MAX_DETAILS_CHARS] + "..."
                )
            summary += f" Details: {details_json}"
    if api_key:
        summary = summary.replace(api_key, "[redacted]")
    return summary[:1000]


def _bounded_master_error_envelope(body: bytes) -> dict[str, Any] | None:
    bounded_body = body[: MASTER_ERROR_MAX_BODY_BYTES + 1]
    if not bounded_body or len(bounded_body) > MASTER_ERROR_MAX_BODY_BYTES:
        return None
    try:
        payload = json.loads(bounded_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    return error if isinstance(error, dict) else None


def _master_http_error_code(body: bytes) -> str:
    error = _bounded_master_error_envelope(body)
    if error is None:
        return ""
    return _bounded_remote_text(error.get("code"), limit=80).upper()


def _http_error_body(exc: HTTPError) -> bytes:
    try:
        body = exc.read(MASTER_ERROR_MAX_BODY_BYTES + 1)
    except Exception:
        return b""
    return body if isinstance(body, bytes) else b""


def _http_status_is_retryable(status: int, *, error_code: str = "") -> bool:
    if status in {409, 412}:
        return (
            status == 409
            and error_code.upper() in RETRYABLE_CONFLICT_ERROR_CODES
        )
    return (
        status in RETRYABLE_HTTP_STATUSES
        or status in RETRYABLE_CREDENTIAL_STATUSES
        or status >= 500
    )


def _request_timeout() -> int:
    try:
        return max(1, int(_setting("master_request_timeout_seconds", 15)))
    except (TypeError, ValueError):
        return 15


def _master_credentials() -> tuple[str, str]:
    configured_franchise_code(required=True)
    master_url = str(_setting("master_url", "") or "").strip().rstrip("/")
    api_key = str(_setting("master_api_key", "") or "").strip()
    if not master_url:
        raise MasterSyncError("Configure MASTER_URL before enabling Setuora Master sync.")
    if not api_key:
        raise MasterSyncError("Configure MASTER_API_KEY before enabling Setuora Master sync.")
    return master_url, api_key


def _open_request(
    request: Request,
    *,
    opener: Callable[..., Any],
) -> tuple[int, bytes]:
    response = opener(request, timeout=_request_timeout())
    try:
        status = getattr(response, "status", None)
        if status is None and hasattr(response, "getcode"):
            status = response.getcode()
        body = response.read()
        return int(status or 200), body
    finally:
        close = getattr(response, "close", None)
        if close:
            close()


def verify_master_enrollment_identity(
    *,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Require an exact, unused Master node before UI baseline initialization."""

    expected_code = configured_franchise_code(required=True)
    master_url, api_key = _master_credentials()
    request = Request(
        f"{master_url}/api/v1/node",
        method="GET",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    try:
        status, body = _open_request(
            request,
            opener=opener or urlopen,
        )
    except (
        HTTPError,
        URLError,
        TimeoutError,
        ConnectionError,
        OSError,
    ) as exc:
        raise MasterSyncError(_safe_error(exc, api_key=api_key)) from exc
    if not 200 <= status < 300:
        raise MasterSyncError(
            f"Master returned HTTP {status} while verifying franchise enrollment."
        )
    try:
        response = _decode_json_response(body)
    except MasterSyncError as exc:
        raise MasterSyncError(
            "Master returned a malformed GET /api/v1/node response."
        ) from exc
    if not isinstance(response, dict) or not isinstance(response.get("data"), dict):
        raise MasterSyncError(
            "Master returned a malformed GET /api/v1/node response."
        )
    data = response["data"]
    returned_code = data.get("code")
    last_sequence = data.get("last_sequence")
    next_sequence = data.get("next_sequence")
    if not isinstance(returned_code, str):
        raise MasterSyncError(
            "Master returned a malformed GET /api/v1/node response."
        )
    if returned_code != expected_code:
        raise MasterSyncError(
            "Master authenticated a different franchise code "
            f"(expected {expected_code}, returned {returned_code}). Correct "
            "MASTER_API_KEY before initializing inventory."
        )
    if type(last_sequence) is not int or type(next_sequence) is not int:
        raise MasterSyncError(
            "Master returned a malformed GET /api/v1/node response."
        )
    if last_sequence != 0 or next_sequence != 1:
        raise MasterSyncError(
            f"Master node {expected_code} is not empty (expected cursor 0/1, "
            f"returned {last_sequence}/{next_sequence}). Inventory "
            "initialization was blocked."
        )
    return data


def _retry_delay(attempts: int) -> timedelta:
    seconds = min(3600, 2 ** min(max(attempts, 1), 11))
    return timedelta(seconds=seconds)


def _fail_outbox_event(
    db: Session,
    event: MasterOutboxEvent,
    exc: BaseException,
    *,
    api_key: str,
    retryable: bool,
    error_message: str | None = None,
) -> None:
    event.status = MasterOutboxStatus.FAILED.value
    event.last_error = (
        error_message[:1000]
        if error_message is not None
        else _safe_error(exc, api_key=api_key)
    )
    event.sending_at = None
    event.next_attempt_at = (
        utc_now() + _retry_delay(event.attempts)
        if retryable
        else None
    )
    if event.aggregate_type == "BATCH" and event.batch_id is not None:
        batch = db.get(Batch, event.batch_id)
        if batch is not None:
            batch.status = BatchStatus.FAILED.value
            batch.last_error = event.last_error
    db.commit()


def _oldest_unsent_event(db: Session) -> MasterOutboxEvent | None:
    return db.scalar(
        select(MasterOutboxEvent)
        .where(MasterOutboxEvent.status != MasterOutboxStatus.SENT.value)
        .order_by(MasterOutboxEvent.id)
        .limit(1)
    )


def _validate_event_acknowledgement(
    body: bytes,
    event: MasterOutboxEvent,
) -> None:
    decoded = _decode_json_response(body)
    if not isinstance(decoded, dict) or not isinstance(decoded.get("data"), dict):
        raise MasterSyncError("Master returned a malformed event acknowledgement.")
    data = decoded["data"]
    acknowledgements = data.get("acknowledgements")
    last_sequence = data.get("last_sequence")
    if not isinstance(acknowledgements, list) or not isinstance(last_sequence, int):
        raise MasterSyncError("Master returned an incomplete event acknowledgement.")
    if last_sequence < event.id:
        raise MasterSyncError(
            "Master acknowledgement did not advance to the submitted sequence."
        )
    matching = any(
        isinstance(ack, dict)
        and str(ack.get("event_id") or "") == event.event_id
        and ack.get("sequence") == event.id
        for ack in acknowledgements
    )
    if not matching:
        raise MasterSyncError(
            "Master acknowledgement does not match the submitted event."
        )


def push_pending_events(
    db: Session,
    *,
    opener: Callable[..., Any] | None = None,
    limit: int = 100,
) -> int:
    """Push events in strict sequence and stop at the first blocked event."""

    if not master_sync_enabled() or limit < 1:
        return 0
    require_initial_inventory_queued(db)
    master_url, api_key = _master_credentials()
    open_url = opener or urlopen
    sent_count = 0

    while sent_count < limit:
        event = _oldest_unsent_event(db)
        if event is None:
            break

        now = utc_now()
        if event.status == MasterOutboxStatus.SENDING.value:
            sending_at = _aware_utc(event.sending_at)
            stale_after = timedelta(seconds=max(60, _request_timeout() * 2))
            if sending_at and sending_at + stale_after > now:
                break
            event.status = MasterOutboxStatus.FAILED.value
            event.last_error = "Recovered an interrupted Master delivery."
            event.next_attempt_at = now
            event.sending_at = None
            db.commit()

        if event.status == MasterOutboxStatus.FAILED.value:
            next_attempt_at = _aware_utc(event.next_attempt_at)
            if next_attempt_at is None or next_attempt_at > now:
                break

        if payload_sha256(event.payload_json) != event.payload_sha256:
            _fail_outbox_event(
                db,
                event,
                MasterSyncError("Master outbox payload integrity check failed."),
                api_key=api_key,
                retryable=False,
            )
            break

        event.status = MasterOutboxStatus.SENDING.value
        event.attempts = int(event.attempts or 0) + 1
        event.sending_at = now
        event.next_attempt_at = None
        event.last_error = None
        if event.aggregate_type == "BATCH" and event.batch_id is not None:
            batch = db.get(Batch, event.batch_id)
            if batch is not None:
                batch.status = BatchStatus.SYNCING.value
                batch.last_error = None
        db.commit()

        request = Request(
            f"{master_url}/api/v1/events",
            data=event.payload_json.encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Setuora-Event-Id": event.event_id,
                "X-Setuora-Event-Hash": event.payload_sha256,
                "X-Setuora-Sequence": str(event.id),
            },
        )
        try:
            status, body = _open_request(request, opener=open_url)
        except HTTPError as exc:
            error_body = _http_error_body(exc)
            error_code = _master_http_error_code(error_body)
            error_message = _master_http_error_summary(
                exc.code,
                error_body,
                api_key=api_key,
            )
            _fail_outbox_event(
                db,
                event,
                exc,
                api_key=api_key,
                retryable=_http_status_is_retryable(
                    exc.code,
                    error_code=error_code,
                ),
                error_message=error_message,
            )
            break
        except (URLError, TimeoutError, ConnectionError, OSError) as exc:
            _fail_outbox_event(db, event, exc, api_key=api_key, retryable=True)
            break
        except Exception as exc:
            _fail_outbox_event(db, event, exc, api_key=api_key, retryable=False)
            break

        if not 200 <= status < 300:
            error_code = _master_http_error_code(body)
            error_message = _master_http_error_summary(
                status,
                body,
                api_key=api_key,
            )
            _fail_outbox_event(
                db,
                event,
                MasterSyncError(error_message),
                api_key=api_key,
                retryable=_http_status_is_retryable(
                    status,
                    error_code=error_code,
                ),
                error_message=error_message,
            )
            break
        try:
            _validate_event_acknowledgement(body, event)
        except Exception as exc:
            # Master ingests by immutable event_id; a lost or corrupt success
            # response is safe to replay and may already have committed.
            _fail_outbox_event(db, event, exc, api_key=api_key, retryable=True)
            break

        event.status = MasterOutboxStatus.SENT.value
        event.sent_at = utc_now()
        event.sending_at = None
        event.next_attempt_at = None
        event.last_error = None
        if event.aggregate_type == "BATCH" and event.batch_id is not None:
            batch = db.get(Batch, event.batch_id)
            if batch is not None:
                batch.status = BatchStatus.SYNCED.value
                batch.synced_at = event.sent_at
                batch.last_error = None
        db.commit()
        sent_count += 1

    return sent_count


def _command_parts(command: dict[str, Any]) -> tuple[str, str, int, dict[str, Any]]:
    command_id = str(command.get("command_id") or command.get("id") or "").strip()
    command_type = str(command.get("command_type") or command.get("type") or "").strip().upper()
    if not command_id:
        raise MasterSyncError("Master command is missing command_id.")
    if not command_type:
        raise MasterSyncError(f"Master command {command_id} is missing command_type.")
    try:
        schema_version = int(command.get("schema_version", COMMAND_SCHEMA_VERSION))
    except (TypeError, ValueError) as exc:
        raise MasterSyncError(f"Master command {command_id} has an invalid schema version.") from exc
    payload = command.get("payload", command.get("data", {}))
    if not isinstance(payload, dict):
        raise MasterSyncError(f"Master command {command_id} payload must be an object.")
    return command_id, command_type, schema_version, payload


def apply_master_command(db: Session, command: dict[str, Any]) -> MasterInboxCommand:
    """Apply one command in the caller's database transaction."""

    command_id, command_type, schema_version, payload = _command_parts(command)
    frozen_payload = canonical_json(payload)
    frozen_hash = payload_sha256(frozen_payload)
    row = db.scalar(
        select(MasterInboxCommand).where(MasterInboxCommand.command_id == command_id)
    )
    if row is not None:
        if (
            row.payload_sha256 != frozen_hash
            or row.command_type != command_type
            or row.schema_version != schema_version
        ):
            raise MasterSyncError(
                f"Master command {command_id} conflicts with an existing command."
            )
        if row.status == MasterInboxStatus.APPLIED.value:
            return row
    else:
        row = MasterInboxCommand(
            command_id=command_id,
            command_type=command_type,
            schema_version=schema_version,
            payload_json=frozen_payload,
            payload_sha256=frozen_hash,
            status=MasterInboxStatus.RECEIVED.value,
        )
        db.add(row)
        db.flush()

    row.attempts = int(row.attempts or 0) + 1
    row.status = MasterInboxStatus.RECEIVED.value
    row.last_error = None
    try:
        # Local import avoids a module cycle: transfer event creation uses the
        # generic outbox helper above.
        from app.services.transfer import (
            apply_transfer_available_command,
            apply_transfer_receipt_command,
        )

        if command_type in {"TRANSFER_AVAILABLE", "TRANSFER_INCOMING"}:
            apply_transfer_available_command(db, payload)
        elif command_type in {"TRANSFER_RECEIPT", "TRANSFER_RECEIPT_STATUS"}:
            apply_transfer_receipt_command(db, payload)
        else:
            raise MasterSyncError(f"Unsupported Master command type: {command_type}.")
    except Exception as exc:
        row.status = MasterInboxStatus.FAILED.value
        row.last_error = _safe_error(exc)
        raise

    row.status = MasterInboxStatus.APPLIED.value
    row.applied_at = utc_now()
    return row


def _decode_json_response(body: bytes) -> Any:
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MasterSyncError("Master returned an invalid JSON response.") from exc


def _persist_failed_command(
    db: Session,
    command: dict[str, Any],
    exc: BaseException,
) -> None:
    command_id, command_type, schema_version, payload = _command_parts(command)
    frozen_payload = canonical_json(payload)
    frozen_hash = payload_sha256(frozen_payload)
    row = db.scalar(
        select(MasterInboxCommand).where(MasterInboxCommand.command_id == command_id)
    )
    if row is None:
        row = MasterInboxCommand(
            command_id=command_id,
            command_type=command_type,
            schema_version=schema_version,
            payload_json=frozen_payload,
            payload_sha256=frozen_hash,
        )
        db.add(row)
    row.status = MasterInboxStatus.FAILED.value
    row.attempts = int(row.attempts or 0) + 1
    row.last_error = _safe_error(exc)
    db.commit()


def poll_master_commands(
    db: Session,
    *,
    opener: Callable[..., Any] | None = None,
    limit: int = 100,
) -> int:
    """Fetch, idempotently apply, and acknowledge Master commands in order."""

    if not master_sync_enabled() or limit < 1:
        return 0
    require_initial_inventory_queued(db)
    master_url, api_key = _master_credentials()
    open_url = opener or urlopen
    query = urlencode({"limit": limit})
    request = Request(
        f"{master_url}/api/v1/commands?{query}",
        method="GET",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    try:
        status, body = _open_request(request, opener=open_url)
        if not 200 <= status < 300:
            raise HTTPError(request.full_url, status, "Master rejected command poll", {}, None)
    except (HTTPError, URLError, OSError, TimeoutError, RuntimeError) as exc:
        raise MasterSyncError(_safe_error(exc, api_key=api_key)) from exc

    decoded = _decode_json_response(body)
    if decoded is None:
        commands: list[dict[str, Any]] = []
    elif isinstance(decoded, list):
        commands = decoded
    elif isinstance(decoded, dict) and isinstance(decoded.get("commands"), list):
        commands = decoded["commands"]
    elif (
        isinstance(decoded, dict)
        and isinstance(decoded.get("data"), dict)
        and isinstance(decoded["data"].get("commands"), list)
    ):
        commands = decoded["data"]["commands"]
    else:
        raise MasterSyncError("Master command response must contain a commands list.")

    applied_count = 0
    for command in commands[:limit]:
        if not isinstance(command, dict):
            raise MasterSyncError("Master returned a malformed command.")
        try:
            row = apply_master_command(db, command)
            db.commit()
        except Exception as exc:
            db.rollback()
            _persist_failed_command(db, command, exc)
            break

        patch_url = f"{master_url}/api/v1/commands/{quote(row.command_id, safe='')}"
        patch_body = canonical_json(
            {"acknowledged": True}
        ).encode("utf-8")
        patch_request = Request(
            patch_url,
            data=patch_body,
            method="PATCH",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            patch_status, _patch_response = _open_request(patch_request, opener=open_url)
            if not 200 <= patch_status < 300:
                raise HTTPError(
                    patch_request.full_url,
                    patch_status,
                    "Master rejected command acknowledgement",
                    {},
                    None,
                )
        except (HTTPError, URLError, OSError, TimeoutError, RuntimeError) as exc:
            raise MasterSyncError(_safe_error(exc, api_key=api_key)) from exc
        applied_count += 1

    return applied_count
