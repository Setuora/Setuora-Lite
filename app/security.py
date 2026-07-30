from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os

from app.config import get_settings

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024
PASSWORD_HASH_ITERATIONS = 600_000
LEGACY_PASSWORD_HASH_ITERATIONS = 260_000


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        salt,
        PASSWORD_HASH_ITERATIONS,
    )
    return (
        f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}"
        f"${_b64(salt)}${_b64(digest)}"
    )


def _password_hash_parts(
    password_hash: str,
) -> tuple[int, str, str] | None:
    parts = password_hash.split("$")
    if len(parts) == 3:
        algorithm, salt_b64, digest_b64 = parts
        iterations = LEGACY_PASSWORD_HASH_ITERATIONS
    elif len(parts) == 4:
        algorithm, raw_iterations, salt_b64, digest_b64 = parts
        try:
            iterations = int(raw_iterations)
        except ValueError:
            return None
    else:
        return None
    if (
        algorithm != "pbkdf2_sha256"
        or iterations < LEGACY_PASSWORD_HASH_ITERATIONS
        or iterations > 10_000_000
    ):
        return None
    return iterations, salt_b64, digest_b64


def verify_password(password: str, password_hash: str) -> bool:
    parts = _password_hash_parts(password_hash)
    if parts is None:
        return False
    iterations, salt_b64, digest_b64 = parts
    try:
        salt = _unb64(salt_b64)
        expected = _unb64(digest_b64)
    except (TypeError, ValueError):
        return False
    if len(salt) < 16 or len(expected) != hashlib.sha256().digest_size:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return hmac.compare_digest(actual, expected)


def password_hash_needs_upgrade(password_hash: str) -> bool:
    parts = _password_hash_parts(password_hash)
    return parts is None or parts[0] != PASSWORD_HASH_ITERATIONS


def password_policy_error(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Password must be at most {MAX_PASSWORD_LENGTH} characters."
    return None


def create_session_token(user_id: int, session_version: int = 0) -> str:
    settings = get_settings()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.session_timeout_minutes)
    payload = {
        "sub": user_id,
        "exp": int(expires_at.timestamp()),
        "ver": int(session_version),
    }
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = _b64(hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


def read_session_claims(token: str | None) -> tuple[int, int] | None:
    if not token or "." not in token:
        return None
    body, signature = token.rsplit(".", 1)
    settings = get_settings()
    expected = _b64(hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_unb64(body))
    except (UnicodeError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        expires_at = int(payload.get("exp", 0))
        user_id = int(payload["sub"])
        session_version = int(payload.get("ver", 0))
    except (KeyError, TypeError, ValueError):
        return None
    if (
        user_id < 1
        or session_version < 0
        or expires_at <= int(datetime.now(timezone.utc).timestamp())
    ):
        return None
    return user_id, session_version


def read_session_token(token: str | None) -> int | None:
    claims = read_session_claims(token)
    return claims[0] if claims else None
