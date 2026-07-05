"""Reminder scheduling built on APScheduler's AsyncIOScheduler.

The scheduler uses UTC and is passed aware-UTC run dates. Jobs live in the
default in-memory job store, so passing the :class:`Bot` instance directly in
``kwargs`` is safe (nothing is serialized).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Bot
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.utils import format_time, utcnow

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None


def init_scheduler() -> AsyncIOScheduler:
    """Create the module-level scheduler if needed and return it."""
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
    return _scheduler


def get_scheduler() -> AsyncIOScheduler:
    """Return the initialized scheduler or raise if not initialized."""
    if _scheduler is None:
        raise RuntimeError("Scheduler is not initialized; call init_scheduler() first")
    return _scheduler


def start_scheduler() -> None:
    """Start the scheduler if it is not already running."""
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()


def shutdown_scheduler() -> None:
    """Shut the scheduler down gracefully (no-op if not running)."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)


def _job_id(booking_id: int) -> str:
    return f"reminder_{booking_id}"


async def _send_reminder(bot: Bot, chat_id: int, text: str) -> None:
    """Job body: deliver the reminder message to the student."""
    try:
        await bot.send_message(chat_id, text)
    except Exception:  # noqa: BLE001 - never let a delivery error crash the loop
        logger.exception("Failed to send reminder to chat_id=%s", chat_id)


def schedule_reminder(
    bot: Bot,
    booking_id: int,
    chat_id: int,
    run_at_utc: datetime,
    text: str,
) -> None:
    """Schedule (or replace) a one-off reminder job at ``run_at_utc``."""
    scheduler = get_scheduler()
    if run_at_utc.tzinfo is None:
        run_at_utc = run_at_utc.replace(tzinfo=timezone.utc)
    scheduler.add_job(
        _send_reminder,
        trigger="date",
        run_date=run_at_utc,
        id=_job_id(booking_id),
        replace_existing=True,
        kwargs={"bot": bot, "chat_id": chat_id, "text": text},
    )
    logger.info("Scheduled reminder for booking=%s at %s", booking_id, run_at_utc)


def remove_reminder(booking_id: int) -> None:
    """Remove a scheduled reminder job, ignoring a missing job."""
    scheduler = get_scheduler()
    try:
        scheduler.remove_job(_job_id(booking_id))
    except JobLookupError:
        pass


def reminder_text(slot_starts_at_utc: datetime) -> str:
    """User-facing reminder text with the local start time."""
    return f"Напоминание: занятие сегодня в {format_time(slot_starts_at_utc)}"


async def restore_reminders(
    session_factory: async_sessionmaker[AsyncSession], bot: Bot
) -> None:
    """Re-schedule reminders for active bookings whose reminder is still ahead."""
    # Imported here to avoid a circular import at module load time.
    from bot.db.repositories import get_active_bookings_with_reminders_future

    async with session_factory() as session:
        rows = await get_active_bookings_with_reminders_future(session)

    now = utcnow()
    restored = 0
    for booking, slot in rows:
        run_at = slot.starts_at - timedelta(minutes=booking.reminder_offset_min or 0)
        if run_at > now:
            schedule_reminder(
                bot,
                booking.id,
                booking.user_id,
                run_at,
                reminder_text(slot.starts_at),
            )
            restored += 1
    logger.info("Restored %s reminder job(s) on startup", restored)
