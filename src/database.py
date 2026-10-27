"""Database connection and session management using SQLAlchemy 2.0.

Supports local SQLite with strict foreign key / secure-delete pragma enforcement,
and production PostgreSQL through standard connection pooling. `init_db` also applies
idempotent, additive schema upgrades so existing local databases keep working.
"""

from __future__ import annotations

import logging
from typing import Generator, List, Tuple

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from src.config import settings

logger = logging.getLogger("procurement.database")

IS_SQLITE = settings.DATABASE_URL.startswith("sqlite")

connect_args = {}
if IS_SQLITE:
    connect_args["check_same_thread"] = False

engine: Engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Enforce referential integrity and overwrite deleted content on disk (SQLite)."""
    if IS_SQLITE:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.execute("PRAGMA secure_delete=ON;")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models with unmapped attribute compatibility."""
    __allow_unmapped__ = True


def get_db() -> Generator[Session, None, None]:
    """FastAPI database session dependency with deterministic closure."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Columns introduced after v1.0.0: (table, column, DDL type). Added with ALTER TABLE when
# an older local database is opened, so upgrades never require deleting data.
_ADDITIVE_COLUMNS: List[Tuple[str, str, str]] = [
    ("invoices", "risk_flags", "VARCHAR(255)"),
    ("invoices", "decided_at", "TIMESTAMP"),
    ("agent_audit_logs", "actor", "VARCHAR(128)"),
    ("agent_audit_logs", "prev_hash", "VARCHAR(64)"),
    ("agent_audit_logs", "entry_hash", "VARCHAR(64)"),
]

_SQLITE_AUDIT_TRIGGERS = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_audit_logs_append_only
    BEFORE UPDATE OF tenant_id, action_taken, details, tokens_used, compute_cost_usd,
                     labor_saved_usd, actor, prev_hash, entry_hash, created_at
    ON agent_audit_logs
    WHEN OLD.entry_hash IS NOT NULL
    BEGIN
        SELECT RAISE(ABORT, 'agent_audit_logs is append-only');
    END;
    """,
)


def _apply_additive_migrations() -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, column, ddl_type in _ADDITIVE_COLUMNS:
            if table not in existing_tables:
                continue
            columns = {c["name"] for c in inspector.get_columns(table)}
            if column not in columns:
                # Identifiers come from the static allow-list above, never from input.
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
                logger.info("Schema upgrade: added %s.%s", table, column)

        try:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_invoices_tenant_number "
                    "ON invoices (tenant_id, invoice_number)"
                )
            )
        except SQLAlchemyError:
            logger.warning(
                "Could not enforce unique (tenant_id, invoice_number): duplicates already exist. "
                "Application-level duplicate detection remains active."
            )


def _install_audit_triggers() -> None:
    if not IS_SQLITE:
        return
    with engine.begin() as conn:
        for ddl in _SQLITE_AUDIT_TRIGGERS:
            conn.execute(text(ddl))


def init_db() -> None:
    """Initialize schema tables, apply additive upgrades, and seal the audit ledger."""
    # Import models so they are registered on Base.metadata before create_all.
    from src import models  # noqa: F401
    from src.services.audit_ledger import backfill_missing_hashes

    Base.metadata.create_all(bind=engine)
    _apply_additive_migrations()

    db = SessionLocal()
    try:
        backfill_missing_hashes(db)
    finally:
        db.close()

    _install_audit_triggers()
