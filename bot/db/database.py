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
