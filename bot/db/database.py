"""Async engine / sessionmaker wiring and schema initialization."""

from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from bot.config import get_settings
from bot.db.models import Base


@lru_cache
def get_engine() -> AsyncEngine:
    """Return a lazily-created async engine (single cached instance)."""
    engine = create_async_engine(
        get_settings().database_url,
        # Wait up to 15s for the write lock instead of failing immediately.
        connect_args={"timeout": 15},
    )

    # Apply SQLite PRAGMAs on every new DBAPI connection. The listener attaches
    # to ``engine.sync_engine`` because aiosqlite drives a real sync DBAPI
    # connection under the async facade — this is the supported hook point for
    # per-connection PRAGMAs with an async engine in SQLAlchemy 2.0.
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.close()

    return engine


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return a lazily-created async sessionmaker."""
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def init_db() -> None:
    """Create the DB parent directory (if any) and all tables."""
    db_path = get_settings().db_path
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Legacy migration: the 'moderator' role was removed and folded into
        # 'teacher'. A stored 'moderator' would fail to load once the enum value
        # is gone, so rewrite any such rows to 'teacher'. Harmless (0 rows) when
        # no legacy moderators exist.
        await conn.exec_driver_sql(
            "UPDATE users SET role='teacher' WHERE role='moderator'"
        )

    # Legacy migration: the 'phone' column was removed from User. An existing DB
    # still carries a NOT NULL 'phone' column; drop it (SQLite 3.35+ supports
    # DROP COLUMN). Run in its own transaction so a failure — the column is
    # already gone (fresh DB) or the SQLite build is too old — cannot roll back
    # schema creation. Idempotent: a second run simply fails the ALTER and is
    # ignored, so no-op on a DB that never had the column.
    try:
        async with engine.begin() as conn:
            await conn.exec_driver_sql("ALTER TABLE users DROP COLUMN phone")
    except Exception:
        pass
