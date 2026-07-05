"""CallbackData factories (aiogram 3.x) with unique prefixes."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class DateCB(CallbackData, prefix="date"):
    """A calendar date, encoded as ``YYYY-MM-DD``."""

    value: str


class SlotCB(CallbackData, prefix="slot"):
    """A specific free slot chosen by the student."""

    slot_id: int


class ReminderCB(CallbackData, prefix="rem"):
    """Reminder preference. ``offset == -1`` means "no reminder" (stored NULL)."""

    booking_id: int
    offset: int


class BookingActionCB(CallbackData, prefix="bk"):
    """Action on one of the student's active bookings.

    ``action`` is one of ``"cancel"`` / ``"resched"``.
    """

    action: str
    booking_id: int
