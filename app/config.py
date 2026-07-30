import os
import secrets
from functools import lru_cache
from pathlib import Path


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
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SECRET_KEY_FILE = PROJECT_ROOT / "data" / "secret_key"
BACKUP_RUNTIME_ENV_KEYS = {
    "AUTOMATIC_BACKUPS_ENABLED",
    "BACKUP_DIRECTORY",
    "BACKUP_OFFSITE_DIRECTORY",
    "BACKUP_INTERVAL_HOURS",
    "BACKUP_RETENTION_COUNT",
}


def _load_env_file(
    path: Path,
    *,
    allowed_keys: set[str] | None = None,
    override: bool = False,
) -> None:
    if not path.exists():
        return

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


class Settings:
    def __init__(self) -> None:
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
        self.franchise_code: str = os.getenv("FRANCHISE_CODE", "").strip().upper()
        self.master_url: str = os.getenv("MASTER_URL", "").strip().rstrip("/")
        self.master_api_key: str = os.getenv("MASTER_API_KEY", "").strip()
        self.master_sync_enabled: bool = _flag("MASTER_SYNC_ENABLED", "false")
        self.master_sync_interval_seconds: int = max(
            15,
            int(os.getenv("MASTER_SYNC_INTERVAL_SECONDS", "30")),
        )
        self.master_request_timeout_seconds: int = max(
            3,
            int(os.getenv("MASTER_REQUEST_TIMEOUT_SECONDS", "15")),
        )
        self.master_tls_verify: bool = _flag("MASTER_TLS_VERIFY", "true")

    @property
    def using_default_secret(self) -> bool:
        return self.secret_key in PLACEHOLDER_SECRET_KEYS or len(self.secret_key.strip()) < 32

    @property
    def master_sync_configuration_error(self) -> str | None:
        if not self.master_sync_enabled:
            return None
        if not self.franchise_code:
            return "FRANCHISE_CODE is required when MASTER_SYNC_ENABLED=true."
        if not self.master_url.startswith("https://"):
            return "MASTER_URL must use https:// when MASTER_SYNC_ENABLED=true."
        if not self.master_api_key:
            return "MASTER_API_KEY is required when MASTER_SYNC_ENABLED=true."
        if not self.master_tls_verify:
            return "MASTER_TLS_VERIFY must remain true for Master synchronization."
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
