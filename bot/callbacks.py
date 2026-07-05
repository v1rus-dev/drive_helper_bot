"""CallbackData factories (aiogram 3.x) with unique prefixes."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class DateCB(CallbackData, prefix="date"):
    """A calendar date, encoded as ``YYYY-MM-DD`` (local school-TZ date)."""

    value: str


class CalendarNavCB(CallbackData, prefix="cal_nav"):
    """Month navigation in the inline calendar: target ``year`` + ``month``."""

    year: int
    month: int


class ScheduleDateCB(CallbackData, prefix="sch_date"):
    """A calendar date in the read-only schedule (distinct from booking's DateCB)."""

    value: str


class ScheduleNavCB(CallbackData, prefix="sch_nav"):
    """Month navigation in the read-only schedule calendar."""

    year: int
    month: int


class ProfileCB(CallbackData, prefix="profile"):
    """Profile edit action. ``action`` is one of ``"name"`` / ``"phone"``."""

    action: str


class CalendarIgnoreCB(CallbackData, prefix="cal_ignore"):
    """No-op tap (placeholder cell, disabled arrow, or a non-tappable label)."""


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
