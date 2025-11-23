"""Database bootstrap helpers."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from . import models
from .config import DB_PATH, ensure_app_dir


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine(db_path: Path | str | None = None):
    """Return a singleton engine for the configured DB path."""
    global _engine, _SessionLocal
    if _engine is None:
        ensure_app_dir()
        path = Path(db_path or DB_PATH)
        _engine = create_engine(f"sqlite:///{path}", future=True)
        event.listen(_engine, "connect", _enable_foreign_keys)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
        _bootstrap_schema(_engine)
    return _engine


def init_db(db_path: Path | str | None = None) -> None:
    """Create all tables."""
    engine = get_engine(db_path=db_path)
    models.Base.metadata.create_all(engine)


@contextmanager
def session_scope(db_path: Path | str | None = None) -> Iterator[Session]:
    """Provide a transactional scope."""
    if _SessionLocal is None:
        get_engine(db_path=db_path)
    assert _SessionLocal is not None  # safety
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:  # pragma: no cover
        session.rollback()
        raise
    finally:
        session.close()


def _bootstrap_schema(engine) -> None:
    """Create tables and apply lightweight migrations when needed."""
    models.Base.metadata.create_all(engine)
    _ensure_schema_version(engine)


def _enable_foreign_keys(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


def _ensure_schema_version(engine) -> None:
    with engine.begin() as conn:
        columns = _table_columns(engine, "meta")
        if columns is None:
            return
        result = conn.execute(text("SELECT value FROM meta WHERE key = 'schema_version'"))
        if result.fetchone() is None:
            conn.execute(
                text(
                    "INSERT INTO meta (key, value, updated_at) "
                    "VALUES (:key, :value, :updated_at)"
                ),
                {
                    "key": "schema_version",
                    "value": "2",
                    "updated_at": datetime.now(timezone.utc),
                },
            )


def _table_columns(engine, table_name: str) -> set[str] | None:
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name = :name"
            ),
            {"name": table_name},
        )
        if result.fetchone() is None:
            return None
        columns = conn.execute(text(f"PRAGMA table_info({table_name})"))
        return {row[1] for row in columns}
