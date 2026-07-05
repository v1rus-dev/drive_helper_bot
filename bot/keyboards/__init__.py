"""Keyboard builders and menu label constants (Russian UI)."""

from __future__ import annotations

from datetime import date
from typing import Iterable, Sequence

from aiogram.types import (
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from bot.callbacks import BookingActionCB, DateCB, ReminderCB, SlotCB
from bot.db.models import Booking, Slot
from bot.utils import format_date, format_time

# --- Main-menu button labels (also used as text filters in handlers) ------
BTN_BOOK = "Записаться"
BTN_MY = "Мои записи"
BTN_HELP = "Помощь"
BTN_ADD_SLOTS = "Добавить слоты"
BTN_DAY_SCHEDULE = "Расписание на день"
BTN_ASSIGN_MOD = "Назначить модератора"
BTN_REMOVE_MOD = "Снять модератора"

# Reminder options: (label, offset-in-minutes). ``None`` -> no reminder.
REMINDER_OPTIONS: tuple[tuple[str, int | None], ...] = (
    ("За 30 минут", 30),
    ("За 1 час", 60),
    ("За 2 часа", 120),
    ("За 4 часа", 240),
    ("За 8 часов", 480),
    ("За сутки", 1440),
    ("Не напоминать", None),
)

# Sentinel offset used on the wire for "no reminder" (CallbackData needs an int).
NO_REMINDER = -1


def main_menu(is_moderator: bool = False, is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Build the main reply keyboard tailored to the user's role."""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text=BTN_BOOK))
    builder.row(KeyboardButton(text=BTN_MY), KeyboardButton(text=BTN_HELP))
    if is_moderator or is_admin:
        builder.row(
            KeyboardButton(text=BTN_ADD_SLOTS),
            KeyboardButton(text=BTN_DAY_SCHEDULE),
        )
    if is_admin:
        builder.row(
            KeyboardButton(text=BTN_ASSIGN_MOD),
            KeyboardButton(text=BTN_REMOVE_MOD),
        )
    return builder.as_markup(resize_keyboard=True)


def phone_request() -> ReplyKeyboardMarkup:
    """Reply keyboard offering to share the phone via a contact button."""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="Поделиться телефоном", request_contact=True))
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def dates_inline(local_dates: Sequence[date]) -> InlineKeyboardMarkup:
    """Inline keyboard of distinct dates that have free slots."""
    builder = InlineKeyboardBuilder()
    for d in local_dates:
        builder.button(
            text=d.strftime("%d.%m.%Y"),
            callback_data=DateCB(value=d.isoformat()),
        )
    builder.adjust(2)
    return builder.as_markup()


def times_inline(slots: Iterable[Slot]) -> InlineKeyboardMarkup:
    """Inline keyboard of start times for the free slots of one date."""
    builder = InlineKeyboardBuilder()
    for slot in slots:
        builder.button(
            text=format_time(slot.starts_at),
            callback_data=SlotCB(slot_id=slot.id),
        )
    builder.adjust(3)
    return builder.as_markup()


def reminder_inline(booking_id: int) -> InlineKeyboardMarkup:
    """Inline keyboard for choosing a reminder offset for a booking."""
    builder = InlineKeyboardBuilder()
    for label, offset in REMINDER_OPTIONS:
        wire_offset = NO_REMINDER if offset is None else offset
        builder.button(
            text=label,
            callback_data=ReminderCB(booking_id=booking_id, offset=wire_offset),
        )
    builder.adjust(2)
    return builder.as_markup()


def booking_actions_inline(booking: Booking) -> InlineKeyboardMarkup:
    """Inline actions (cancel / reschedule) for one active booking."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Отменить",
        callback_data=BookingActionCB(action="cancel", booking_id=booking.id),
    )
    builder.button(
        text="Перенести",
        callback_data=BookingActionCB(action="resched", booking_id=booking.id),
    )
    builder.adjust(2)
    return builder.as_markup()


def distinct_local_dates(slots: Iterable[Slot]) -> list[date]:
    """Ordered distinct local calendar dates present among the slots."""
    seen: list[date] = []
    for slot in slots:
        # format_date converts to local; derive the local date for grouping.
        local_date = _local_date(slot)
        if local_date not in seen:
            seen.append(local_date)
    return seen


def _local_date(slot: Slot) -> date:
    from bot.utils import to_local

    return to_local(slot.starts_at).date()


__all__ = [
    "BTN_BOOK",
    "BTN_MY",
    "BTN_HELP",
    "BTN_ADD_SLOTS",
    "BTN_DAY_SCHEDULE",
    "BTN_ASSIGN_MOD",
    "BTN_REMOVE_MOD",
    "NO_REMINDER",
    "REMINDER_OPTIONS",
    "main_menu",
    "phone_request",
    "dates_inline",
    "times_inline",
    "reminder_inline",
    "booking_actions_inline",
    "distinct_local_dates",
    "format_date",
]
