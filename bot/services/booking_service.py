"""Booking orchestration: atomic capture + reminder scheduling decisions."""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Booking, User
from bot.db.repositories import (
    capture_slot_and_book,
    cancel_booking as repo_cancel_booking,
    get_slot,
    set_booking_reminder,
)
from bot.services.reminders import remove_reminder, reminder_text, schedule_reminder
from bot.utils import utcnow


async def book_slot(
    session: AsyncSession,
    slot_id: int,
    user: User,
) -> Optional[Booking]:
    """Atomically capture ``slot_id`` for ``user``.

    Returns the active booking (reminder offset NULL for now — chosen next),
    or ``None`` if the slot was already taken.
    """
    return await capture_slot_and_book(session, slot_id, user.tg_id, offset=None)


async def apply_reminder(
    session: AsyncSession,
    booking: Booking,
    offset: Optional[int],
    bot: Bot,
    chat_id: int,
) -> bool:
    """Persist the reminder preference and schedule it if still in the future.

    Returns ``True`` if a reminder job was scheduled, ``False`` otherwise
    (offset disabled, or the reminder time already passed).
    """
    await set_booking_reminder(session, booking.id, offset)
    if offset is None:
        remove_reminder(booking.id)
        return False

    slot = await get_slot(session, booking.slot_id)
    if slot is None:
        return False

    run_at = slot.starts_at - timedelta(minutes=offset)
    if run_at > utcnow():
        schedule_reminder(
            bot, booking.id, chat_id, run_at, reminder_text(slot.starts_at)
        )
        return True

    # Too soon to remind — booking still stands, just no job scheduled.
    remove_reminder(booking.id)
    return False


async def cancel_booking(
    session: AsyncSession,
    booking_id: int,
    expected_user_id: Optional[int] = None,
) -> Optional[Booking]:
    """Cancel a booking, free its slot and drop any scheduled reminder.

    ``expected_user_id`` is passed through as an ownership guard: the booking
    is only cancelled when it belongs to that user.
    """
    booking = await repo_cancel_booking(session, booking_id, expected_user_id)
    if booking is not None:
        remove_reminder(booking_id)
    return booking
