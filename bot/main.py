"""Application entry point: wiring, startup and polling."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import get_settings
from bot.db.database import get_sessionmaker, init_db
from bot.handlers import admin, booking, common, moderator, profile, schedule
from bot.middlewares import AuthMiddleware
from bot.services.reminders import (
    init_scheduler,
    restore_reminders,
    shutdown_scheduler,
    start_scheduler,
)

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )


async def main() -> None:
    """Configure everything and start long polling."""
    _configure_logging()
    settings = get_settings()

    await init_db()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # Auth / session middleware for both messages and callbacks.
    auth = AuthMiddleware()
    dp.message.middleware(auth)
    dp.callback_query.middleware(auth)

    # Routers — common first so /start and registration take priority.
    dp.include_router(common.router)
    dp.include_router(booking.router)
    dp.include_router(schedule.router)
    dp.include_router(profile.router)
    dp.include_router(moderator.router)
    dp.include_router(admin.router)

    init_scheduler()
    start_scheduler()
    await restore_reminders(get_sessionmaker(), bot)

    logger.info("DriveHelper bot started; beginning polling")
    try:
        await dp.start_polling(bot)
    finally:
        shutdown_scheduler()
        await bot.session.close()
        logger.info("DriveHelper bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
