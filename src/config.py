"""Configuration settings for Autonomous B2B Supply Chain & Procurement Agent.

Uses pathlib for cross-platform deterministic paths and environment-based overrides.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List


BASE_DIR: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_SQLITE_PATH: Path = DATA_DIR / "procurement_local.db"


class Settings:
    """Enterprise application settings with local zero-friction defaults."""

    APP_NAME: str = "Autonomous B2B Supply Chain & Procurement Agent"
    APP_VERSION: str = "1.0.0"
    ENV: str = os.getenv("ENV", "development")

    # API Routing
    API_V1_STR: str = "/api/v1"

    # Security & JWT Authentication
    SECRET_KEY: str = os.getenv(
        "SECRET_KEY", "b2b-procurement-agent-enterprise-secret-key-hs256-deterministic-2026"
    )
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

    # Database: SQLite default fallback for instant zero-dependency execution
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"
    )

    # Local Network Bindings (Strict loopback 127.0.0.1)
    HOST: str = os.getenv("HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORT", "8000"))
    UI_PORT: int = int(os.getenv("UI_PORT", "8501"))

    # CORS Lockdown: strictly localhost/127.0.0.1 for Streamlit UI
    CORS_ORIGINS: List[str] = [
        "http://127.0.0.1:8501",
        "http://localhost:8501",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ]

    # Rate Limiting (Slowapi)
    RATE_LIMIT_AUTH: str = "5/minute"
    RATE_LIMIT_RECONCILE: str = "60/minute"
    RATE_LIMIT_DEFAULT: str = "100/minute"

    # Deterministic 3-Way Matching & Risk Guardrails (FR-04)
    VARIANCE_TOLERANCE_PCT: float = float(os.getenv("VARIANCE_TOLERANCE_PCT", "2.0"))
    HIGH_VALUE_THRESHOLD_USD: float = float(os.getenv("HIGH_VALUE_THRESHOLD_USD", "10000.0"))

    # Unit Economics & ROI Baseline Parameters (FR-06)
    MANUAL_MINUTES_PER_INVOICE: float = float(os.getenv("MANUAL_MINUTES_PER_INVOICE", "18.0"))
    HOURLY_ANALYST_WAGE_USD: float = float(os.getenv("HOURLY_ANALYST_WAGE_USD", "45.0"))
    TOKEN_COST_PER_1K_USD: float = float(os.getenv("TOKEN_COST_PER_1K_USD", "0.002"))
    INFRA_COST_USD: float = float(os.getenv("INFRA_COST_USD", "15.00"))


settings = Settings()
