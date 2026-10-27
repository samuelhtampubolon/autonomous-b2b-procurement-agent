"""Configuration settings for Autonomous B2B Supply Chain & Procurement Agent.

Uses pathlib for cross-platform deterministic paths and environment-based overrides.
Security-sensitive values (JWT secret, CORS origins, trusted hosts) are validated at
import time so a misconfigured production deployment fails fast instead of running
with insecure defaults.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from pathlib import Path
from typing import List

BASE_DIR: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_SQLITE_PATH: Path = DATA_DIR / "procurement_local.db"
SECRET_KEY_FILE: Path = DATA_DIR / ".jwt_secret"

# Minimum entropy for the HS256 signing key (RFC 7518 section 3.2: key >= hash output size).
MIN_SECRET_KEY_BYTES: int = 32

# SHA-256 digests of signing keys that are public knowledge (including the key published
# in v1.0 of this repository). Only digests are stored so the leaked values never appear
# in the source again; a configured key matching any digest is refused at startup.
KNOWN_LEAKED_SECRET_SHA256 = frozenset(
    {
        "6d9eb221a59ed7bd8863418b0a0362934bb7ba66ad5fcb7d967ed00323708a2f",
        "057ba03d6c44104863dc7361fe4578965d1887360f90a0895882e58a6248fc86",
        "2bb80d537b1da3e38bd30361aa855686bde0eacd7162fef6a25fe97bf527a25b",
    }
)


def is_known_leaked_secret(value: str) -> bool:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() in KNOWN_LEAKED_SECRET_SHA256

logger = logging.getLogger("procurement.config")


class ConfigurationError(RuntimeError):
    """Raised when the runtime configuration is insecure or invalid."""


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: List[str]) -> List[str]:
    raw = os.getenv(name)
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _load_or_create_local_secret() -> str:
    """Return a per-installation random signing key for local development.

    The key is generated once with `secrets.token_urlsafe` and persisted to
    `data/.jwt_secret` (owner read/write only) so that the API and the preflight
    checks share it across restarts. It never leaves the machine and is ignored by git.
    """
    if SECRET_KEY_FILE.exists():
        stored = SECRET_KEY_FILE.read_text(encoding="utf-8").strip()
        if len(stored.encode("utf-8")) >= MIN_SECRET_KEY_BYTES:
            return stored

    generated = secrets.token_urlsafe(64)
    SECRET_KEY_FILE.write_text(generated, encoding="utf-8")
    try:
        SECRET_KEY_FILE.chmod(0o600)
    except OSError:  # pragma: no cover - Windows ACLs do not map to chmod
        pass
    return generated


def _resolve_secret_key(env: str) -> str:
    configured = os.getenv("SECRET_KEY", "").strip()

    if configured:
        if is_known_leaked_secret(configured):
            raise ConfigurationError(
                "SECRET_KEY uses a publicly known value. Generate a new one with: "
                "python -c \"import secrets; print(secrets.token_urlsafe(64))\""
            )
        if len(configured.encode("utf-8")) < MIN_SECRET_KEY_BYTES:
            raise ConfigurationError(
                f"SECRET_KEY must be at least {MIN_SECRET_KEY_BYTES} bytes for HS256."
            )
        return configured

    if env == "production":
        raise ConfigurationError("SECRET_KEY must be set explicitly when ENV=production.")

    return _load_or_create_local_secret()


class Settings:
    """Enterprise application settings with local zero-friction, secure-by-default values."""

    APP_NAME: str = "Autonomous B2B Supply Chain & Procurement Agent"
    APP_VERSION: str = "1.1.0"
    ENV: str = os.getenv("ENV", "development").strip().lower()

    # API Routing
    API_V1_STR: str = "/api/v1"

    # Security & JWT Authentication (OWASP API2)
    SECRET_KEY: str = _resolve_secret_key(ENV)
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = min(
        int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30")), 60
    )
    JWT_ISSUER: str = os.getenv("JWT_ISSUER", "procurement-agent-api")
    JWT_AUDIENCE: str = os.getenv("JWT_AUDIENCE", "procurement-agent-clients")
    JWT_LEEWAY_SECONDS: int = 10

    # Brute-force protection (per username, in addition to per-IP rate limits)
    LOGIN_MAX_FAILED_ATTEMPTS: int = int(os.getenv("LOGIN_MAX_FAILED_ATTEMPTS", "5"))
    LOGIN_LOCKOUT_MINUTES: int = int(os.getenv("LOGIN_LOCKOUT_MINUTES", "15"))

    # Demo data: seeded personas are convenient locally but must be opt-in in production.
    DEMO_MODE: bool = _env_bool("DEMO_MODE", ENV != "production")

    # Database: SQLite default fallback for instant zero-dependency execution
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"
    )

    # Local Network Bindings (Strict loopback 127.0.0.1)
    HOST: str = os.getenv("HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORT", "8000"))
    UI_PORT: int = int(os.getenv("UI_PORT", "8501"))

    # CORS Lockdown: strictly localhost/127.0.0.1 for Streamlit UI
    CORS_ORIGINS: List[str] = _env_list(
        "CORS_ORIGINS",
        [
            "http://127.0.0.1:8501",
            "http://localhost:8501",
        ],
    )
    # Host header allow-list (prevents host header injection / DNS rebinding)
    TRUSTED_HOSTS: List[str] = _env_list(
        "TRUSTED_HOSTS", ["127.0.0.1", "localhost", "testserver", "api"]
    )

    # Rate Limiting (Slowapi) - OWASP API4
    RATE_LIMIT_AUTH: str = os.getenv("RATE_LIMIT_AUTH", "5/minute")
    RATE_LIMIT_RECONCILE: str = os.getenv("RATE_LIMIT_RECONCILE", "60/minute")
    RATE_LIMIT_DECISION: str = os.getenv("RATE_LIMIT_DECISION", "30/minute")
    RATE_LIMIT_SEARCH: str = os.getenv("RATE_LIMIT_SEARCH", "30/minute")
    RATE_LIMIT_DEFAULT: str = os.getenv("RATE_LIMIT_DEFAULT", "100/minute")
    RATE_LIMIT_ENABLED: bool = _env_bool("RATE_LIMIT_ENABLED", True)
    MAX_REQUEST_BODY_BYTES: int = int(os.getenv("MAX_REQUEST_BODY_BYTES", str(1024 * 1024)))

    # Deterministic 3-Way Matching & Risk Guardrails (FR-04)
    VARIANCE_TOLERANCE_PCT: float = float(os.getenv("VARIANCE_TOLERANCE_PCT", "2.0"))
    HIGH_VALUE_THRESHOLD_USD: float = float(os.getenv("HIGH_VALUE_THRESHOLD_USD", "10000.0"))
    # Segregation of duties: invoices above this value can only be approved by a CPO.
    CPO_APPROVAL_THRESHOLD_USD: float = float(
        os.getenv("CPO_APPROVAL_THRESHOLD_USD", "25000.0")
    )
    # Delivery timestamps further in the future than this are rejected as implausible.
    MAX_FUTURE_DELIVERY_DAYS: int = int(os.getenv("MAX_FUTURE_DELIVERY_DAYS", "1"))

    # Unit Economics & ROI Baseline Parameters (FR-06)
    MANUAL_MINUTES_PER_INVOICE: float = float(os.getenv("MANUAL_MINUTES_PER_INVOICE", "18.0"))
    HOURLY_ANALYST_WAGE_USD: float = float(os.getenv("HOURLY_ANALYST_WAGE_USD", "45.0"))
    TOKEN_COST_PER_1K_USD: float = float(os.getenv("TOKEN_COST_PER_1K_USD", "0.002"))
    INFRA_COST_USD: float = float(os.getenv("INFRA_COST_USD", "15.00"))

    @property
    def is_production(self) -> bool:
        return self.ENV == "production"

    @property
    def docs_enabled(self) -> bool:
        return not self.is_production


settings = Settings()

if settings.is_production and any(
    origin.startswith("http://") and "localhost" not in origin and "127.0.0.1" not in origin
    for origin in settings.CORS_ORIGINS
):
    raise ConfigurationError("CORS_ORIGINS must use https:// for non-local origins in production.")
if "*" in settings.CORS_ORIGINS:
    raise ConfigurationError("Wildcard CORS origins are not allowed with credentialed requests.")
