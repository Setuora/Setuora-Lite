import os
import re
import secrets
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit


DEFAULT_SECRET_KEY = "dev-change-me"
PLACEHOLDER_SECRET_KEYS = {
    DEFAULT_SECRET_KEY,
    "change-this-before-production",
    "replace-with-a-long-random-secret",
}
INSECURE_BOOTSTRAP_PASSWORDS = {
    "",
    "admin123",
    "change-this-password",
    "change-this-before-first-start",
}
FRANCHISE_CODE_PLACEHOLDERS = {
    "",
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
FRANCHISE_CODE_PATTERN = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$")
DNS_NAME_PATTERN = re.compile(
    r"^(?=.{1,253}\.?$)"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?$"
)
MASTER_API_KEY_PATTERN = re.compile(
    r"^setuora-node\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{32,}$"
)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SECRET_KEY_FILE = PROJECT_ROOT / "data" / "secret_key"
BACKUP_RUNTIME_ENV_KEYS = {
    "AUTOMATIC_BACKUPS_ENABLED",
    "BACKUP_DIRECTORY",
    "BACKUP_OFFSITE_DIRECTORY",
    "BACKUP_INTERVAL_HOURS",
    "BACKUP_RETENTION_COUNT",
}
MASTER_CONNECTION_RUNTIME_ENV_KEYS = {
    "FRANCHISE_CODE",
    "MASTER_SYNC_ENABLED",
    "MASTER_URL",
    "MASTER_API_KEY",
    "MASTER_SYNC_INTERVAL_SECONDS",
    "MASTER_REQUEST_TIMEOUT_SECONDS",
    "MASTER_TLS_VERIFY",
}


def _read_env_file(
    path: Path,
    *,
    allowed_keys: set[str] | None = None,
) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        if key.startswith("export "):
            key = key.removeprefix("export ").strip()
        if not key or (allowed_keys is not None and key not in allowed_keys):
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _load_env_file(
    path: Path,
    *,
    allowed_keys: set[str] | None = None,
    override: bool = False,
) -> None:
    for key, value in _read_env_file(path, allowed_keys=allowed_keys).items():
        if override:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)


_load_env_file(PROJECT_ROOT / ".env")
_backup_settings_file = Path(
    os.getenv(
        "BACKUP_SETTINGS_FILE",
        str(PROJECT_ROOT / "data" / "backup-settings.env"),
    )
).expanduser()
if not _backup_settings_file.is_absolute():
    _backup_settings_file = (PROJECT_ROOT / _backup_settings_file).resolve()
_load_env_file(
    _backup_settings_file,
    allowed_keys=BACKUP_RUNTIME_ENV_KEYS,
    override=True,
)


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _flag_value(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _comma_separated(name: str, default: str) -> list[str]:
    return [value.strip().lower() for value in os.getenv(name, default).split(",") if value.strip()]


def _resolve_secret_key() -> str:
    env_value = os.getenv("APP_SECRET_KEY", "").strip()
    if env_value and env_value not in PLACEHOLDER_SECRET_KEYS and len(env_value) >= 32:
        return env_value
    try:
        if SECRET_KEY_FILE.exists():
            stored = SECRET_KEY_FILE.read_text(encoding="utf-8").strip()
            if len(stored) >= 32:
                return stored
        SECRET_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        generated = secrets.token_urlsafe(48)
        SECRET_KEY_FILE.write_text(generated, encoding="utf-8")
        return generated
    except OSError:
        return env_value or DEFAULT_SECRET_KEY


def master_url_configuration_error(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return "MASTER_URL must be an exact https://*.ts.net URL."
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not hostname.endswith(".ts.net")
        or hostname == "ts.net"
        or "*" in hostname
        or parsed.username
        or parsed.password
        or port is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        return "MASTER_URL must be an exact https://*.ts.net URL without a path or port."
    if DNS_NAME_PATTERN.fullmatch(hostname) is None:
        return "MASTER_URL contains an invalid MagicDNS hostname."
    return None


def master_connection_settings_path() -> Path:
    configured = os.getenv("MASTER_CONNECTION_SETTINGS_FILE", "").strip()
    path = Path(configured) if configured else PROJECT_ROOT / "data" / "master-connection.env"
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def save_master_connection_settings(values: dict[str, str]) -> None:
    unexpected = set(values) - MASTER_CONNECTION_RUNTIME_ENV_KEYS
    if unexpected:
        raise ValueError(f"Unsupported Master setting(s): {', '.join(sorted(unexpected))}")
    if any("\n" in value or "\r" in value for value in values.values()):
        raise ValueError("Master settings cannot contain line breaks.")

    path = master_connection_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    content = "".join(f"{key}={values[key]}\n" for key in sorted(values))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if temporary.exists():
            temporary.unlink()
    get_settings.cache_clear()


class Settings:
    def __init__(self) -> None:
        master_runtime = _read_env_file(
            master_connection_settings_path(),
            allowed_keys=MASTER_CONNECTION_RUNTIME_ENV_KEYS,
        )

        def master_value(name: str, default: str = "") -> str:
            return master_runtime.get(name, os.getenv(name, default))

        self.app_mode: str = os.getenv("SETUORA_APP_MODE", "lite").strip().lower()
        self.allow_legacy_test_mode: bool = _flag(
            "SETUORA_ALLOW_LEGACY_TEST_MODE",
            "false",
        )
        if self.app_mode not in {"lite", "legacy"}:
            raise RuntimeError("Setuora-Lite only supports lite mode.")
        if self.app_mode == "legacy" and not self.allow_legacy_test_mode:
            raise RuntimeError(
                "Legacy direct-Tally mode is test-only in Setuora-Lite. "
                "Production must use SETUORA_APP_MODE=lite."
            )
        self.app_name: str = os.getenv(
            "APP_NAME",
            "Setuora Lite" if self.app_mode == "lite" else "Setuora",
        )
        self.secret_key: str = _resolve_secret_key()
        self.database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/setuora.db")
        self.session_timeout_minutes: int = int(os.getenv("SESSION_TIMEOUT_MINUTES", "480"))
        self.bootstrap_admin_username: str = os.getenv("BOOTSTRAP_ADMIN_USERNAME", "admin")
        self.bootstrap_admin_password: str = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
        self.cookie_secure: bool = _flag("SESSION_COOKIE_SECURE")
        self.trusted_hosts: list[str] = _comma_separated(
            "TRUSTED_HOSTS", "localhost,127.0.0.1,testserver"
        )
        self.login_max_attempts: int = int(os.getenv("LOGIN_MAX_ATTEMPTS", "8"))
        self.login_lockout_minutes: int = int(os.getenv("LOGIN_LOCKOUT_MINUTES", "15"))
        self.automatic_backups_enabled: bool = _flag("AUTOMATIC_BACKUPS_ENABLED", "true")
        self.backup_directory: str = os.getenv("BACKUP_DIRECTORY", "./data/backups")
        self.backup_offsite_directory: str = os.getenv("BACKUP_OFFSITE_DIRECTORY", "").strip()
        self.backup_interval_hours: int = int(os.getenv("BACKUP_INTERVAL_HOURS", "24"))
        self.backup_retention_count: int = int(os.getenv("BACKUP_RETENTION_COUNT", "14"))
        self.backup_startup_delay_seconds: int = int(os.getenv("BACKUP_STARTUP_DELAY_SECONDS", "60"))
        self.container_deployment: bool = _flag("SETUORA_CONTAINER_DEPLOYMENT", "false")
        self.franchise_code: str = master_value("FRANCHISE_CODE").strip().upper()
        self.master_url: str = master_value("MASTER_URL").strip().rstrip("/")
        self.master_api_key: str = master_value("MASTER_API_KEY").strip()
        self.master_sync_enabled: bool = _flag_value(
            master_value("MASTER_SYNC_ENABLED", "false")
        )
        self.master_sync_interval_seconds: int = max(
            15,
            int(master_value("MASTER_SYNC_INTERVAL_SECONDS", "30")),
        )
        self.master_request_timeout_seconds: int = max(
            3,
            int(master_value("MASTER_REQUEST_TIMEOUT_SECONDS", "15")),
        )
        self.master_tls_verify: bool = _flag_value(
            master_value("MASTER_TLS_VERIFY", "true")
        )

    @property
    def using_default_secret(self) -> bool:
        return self.secret_key in PLACEHOLDER_SECRET_KEYS or len(self.secret_key.strip()) < 32

    @property
    def master_sync_configuration_error(self) -> str | None:
        if not self.master_sync_enabled:
            return None
        if (
            self.franchise_code in FRANCHISE_CODE_PLACEHOLDERS
            or len(self.franchise_code) > 40
            or FRANCHISE_CODE_PATTERN.fullmatch(self.franchise_code) is None
        ):
            return "FRANCHISE_CODE must be a permanent, unique code of at most 40 characters."
        master_url_error = master_url_configuration_error(self.master_url)
        if master_url_error:
            return master_url_error
        if MASTER_API_KEY_PATTERN.fullmatch(self.master_api_key) is None:
            return "MASTER_API_KEY must use the setuora-node.<id>.<secret> format."
        if not self.master_tls_verify:
            return "MASTER_TLS_VERIFY must remain true for Master synchronization."
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
