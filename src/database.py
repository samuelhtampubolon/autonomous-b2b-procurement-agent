"""Database connection and session management using SQLAlchemy 2.0.

Supports local SQLite with strict foreign key pragma enforcement,
and production PostgreSQL through standard connection pooling.
"""

from __future__ import annotations

from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

from src.config import settings

connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine: Engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)

# Enforce foreign key constraints for SQLite
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if settings.DATABASE_URL.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
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


def init_db() -> None:
    """Initialize all schema tables in the database."""
    Base.metadata.create_all(bind=engine)
