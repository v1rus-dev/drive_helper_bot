"""Async engine / sessionmaker wiring and schema initialization."""

from __future__ import annotations

import os
from functools import lru_cache

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
    return create_async_engine(get_settings().database_url)


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
