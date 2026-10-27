"""Shared pytest configuration.

Runs every test against an isolated, throwaway SQLite database and an ephemeral
signing key so the suite never touches `data/procurement_local.db` or `data/.jwt_secret`.
These variables must be set before any `src` module is imported.
"""

from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path

_TEST_DIR = Path(tempfile.mkdtemp(prefix="procurement-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TEST_DIR / 'test.db').as_posix()}"
os.environ.setdefault("SECRET_KEY", secrets.token_urlsafe(48))
os.environ.setdefault("ENV", "test")
# Random per-run demo passwords: no credential is hardcoded anywhere in the test suite.
for _var in ("DEMO_ANALYST_PASSWORD", "DEMO_CPO_PASSWORD", "DEMO_AUDITOR_PASSWORD", "DEMO_BETA_PASSWORD"):
    os.environ[_var] = secrets.token_urlsafe(18)

import pytest  # noqa: E402

from src.database import SessionLocal, init_db  # noqa: E402
from src.security import limiter, login_tracker  # noqa: E402
from src.seed_data import seed_database  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def seeded_database():
    init_db()
    db = SessionLocal()
    try:
        seed_database(db, force=False)
    finally:
        db.close()
    yield


@pytest.fixture(autouse=True)
def reset_abuse_controls():
    """Rate-limit and lockout state is process-global; isolate it per test."""
    limiter.reset()
    login_tracker.reset()
    yield
    limiter.reset()
    login_tracker.reset()
