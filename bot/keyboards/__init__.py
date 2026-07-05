"""Keyboard builders and menu label constants (Russian UI)."""

from __future__ import annotations

import calendar
from datetime import date
from typing import Iterable

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from bot.callbacks import (
    BookingActionCB,
    CalendarIgnoreCB,
    CalendarNavCB,
    DateCB,
    ProfileCB,
    ReminderCB,
    SlotCB,
)
from bot.db.models import Booking, Slot, UserRole
from bot.utils import format_date, format_time

# Russian month names indexed by ``month - 1`` (nominative, as shown in the header).
RU_MONTHS: tuple[str, ...] = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)
# Monday-first weekday labels for the calendar header row.
RU_WEEKDAYS: tuple[str, ...] = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
# Text of a non-actionable placeholder cell (padding / disabled arrow / no free slots).
_CAL_PLACEHOLDER = "·"

# --- Main-menu button labels (also used as text filters in handlers) ------
BTN_BOOK = "Записаться"
BTN_MY = "Мои записи"
BTN_HELP = "Помощь"
BTN_PROFILE = "Мой профиль"
BTN_SCHEDULE = "Расписание"
BTN_ADD_SLOTS = "Добавить слоты"
BTN_DAY_SCHEDULE = "Расписание на день"
BTN_ALL_SLOTS = "Все слоты"
BTN_ASSIGN_MOD = "Назначить модератора"
BTN_REMOVE_MOD = "Снять модератора"
BTN_ASSIGN_TEACHER = "Назначить преподавателя"
BTN_REMOVE_TEACHER = "Снять преподавателя"
BTN_EDIT_NAME = "Изменить ФИО"
BTN_EDIT_PHONE = "Изменить телефон"

# Localized role labels — never show a raw enum value to the user.
ROLE_LABELS: dict[UserRole, str] = {
    UserRole.student: "Ученик",
    UserRole.moderator: "Модератор",
    UserRole.teacher: "Преподаватель",
    UserRole.admin: "Администратор",
}

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


def main_menu(role: UserRole = UserRole.student) -> ReplyKeyboardMarkup:
    """Build the main reply keyboard tailored to the user's effective role.

    Student — book / my bookings / schedule; teacher — staff schedule views;
    moderator — add slots + day schedule; admin — moderator caps + role
    management. «Мой профиль» и «Помощь» показываются всем.
    """
    builder = ReplyKeyboardBuilder()
    if role == UserRole.teacher:
        builder.row(
            KeyboardButton(text=BTN_DAY_SCHEDULE),
            KeyboardButton(text=BTN_ALL_SLOTS),
        )
    elif role == UserRole.moderator:
        builder.row(
            KeyboardButton(text=BTN_ADD_SLOTS),
            KeyboardButton(text=BTN_DAY_SCHEDULE),
        )
    elif role == UserRole.admin:
        builder.row(
            KeyboardButton(text=BTN_ADD_SLOTS),
            KeyboardButton(text=BTN_DAY_SCHEDULE),
        )
        builder.row(KeyboardButton(text=BTN_ALL_SLOTS))
        builder.row(
            KeyboardButton(text=BTN_ASSIGN_MOD),
            KeyboardButton(text=BTN_REMOVE_MOD),
        )
        builder.row(
            KeyboardButton(text=BTN_ASSIGN_TEACHER),
            KeyboardButton(text=BTN_REMOVE_TEACHER),
        )
    else:  # student (and any unknown role) — booking-facing menu
        builder.row(KeyboardButton(text=BTN_BOOK))
        builder.row(KeyboardButton(text=BTN_MY), KeyboardButton(text=BTN_SCHEDULE))
    builder.row(KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_HELP))
    return builder.as_markup(resize_keyboard=True)


def profile_actions_inline() -> InlineKeyboardMarkup:
    """Inline actions for the profile view: edit name / edit phone."""
    builder = InlineKeyboardBuilder()
    builder.button(text=BTN_EDIT_NAME, callback_data=ProfileCB(action="name"))
    builder.button(text=BTN_EDIT_PHONE, callback_data=ProfileCB(action="phone"))
    builder.adjust(1)
    return builder.as_markup()


def phone_request() -> ReplyKeyboardMarkup:
    """Reply keyboard offering to share the phone via a contact button."""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="Поделиться телефоном", request_contact=True))
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def _ignore_button(text: str = _CAL_PLACEHOLDER) -> InlineKeyboardButton:
    """A non-actionable calendar cell whose tap is a no-op (spinner suppressed)."""
    return InlineKeyboardButton(text=text, callback_data=CalendarIgnoreCB().pack())


def build_calendar(
    year: int,
    month: int,
    available_dates: set[date],
    min_month: tuple[int, int],
    max_month: tuple[int, int],
    date_cb: type[DateCB] = DateCB,
    nav_cb: type[CalendarNavCB] = CalendarNavCB,
) -> InlineKeyboardMarkup:
    """Month-grid inline calendar (Monday-first) for the date-selection step.

    Only dates in ``available_dates`` are tappable. ``date_cb`` / ``nav_cb`` let a
    second consumer (the read-only schedule) reuse this grid with its own
    callback prefixes so its taps don't collide with the booking flow; both
    factories share the ``value`` / ``year``+``month`` shape. Padding cells, days
    without slots, and out-of-range arrows render as :func:`_ignore_button`
    placeholders. ``‹``/``›`` are clamped to ``[min_month, max_month]`` so the
    user cannot page into empty months.
    """
    builder = InlineKeyboardBuilder()

    # Row 1 — month navigation + non-tappable label. Tuple comparison orders
    # (year, month) chronologically, so it also handles the year rollover.
    if (year, month) > min_month:
        py, pm = _shift_month(year, month, -1)
        prev_btn = InlineKeyboardButton(
            text="‹", callback_data=nav_cb(year=py, month=pm).pack()
        )
    else:
        prev_btn = _ignore_button("‹")
    if (year, month) < max_month:
        ny, nm = _shift_month(year, month, 1)
        next_btn = InlineKeyboardButton(
            text="›", callback_data=nav_cb(year=ny, month=nm).pack()
        )
    else:
        next_btn = _ignore_button("›")
    builder.row(prev_btn, _ignore_button(f"{RU_MONTHS[month - 1]} {year}"), next_btn)

    # Row 2 — weekday labels (Monday-first, non-tappable).
    builder.row(*(_ignore_button(label) for label in RU_WEEKDAYS))

    # Rows 3.. — day grid. ``0`` marks leading/trailing padding cells.
    for week in calendar.Calendar(firstweekday=0).monthdayscalendar(year, month):
        cells: list[InlineKeyboardButton] = []
        for day in week:
            if day == 0:
                cells.append(_ignore_button())
            elif date(year, month, day) in available_dates:
                cells.append(
                    InlineKeyboardButton(
                        text=str(day),
                        callback_data=date_cb(
                            value=date(year, month, day).isoformat()
                        ).pack(),
                    )
                )
            else:
                cells.append(_ignore_button())
        builder.row(*cells)

    return builder.as_markup()


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """Return the (year, month) ``delta`` months away from the given one."""
    index = (year * 12 + (month - 1)) + delta
    return index // 12, index % 12 + 1


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


__all__ = [
    "BTN_BOOK",
    "BTN_MY",
    "BTN_HELP",
    "BTN_PROFILE",
    "BTN_SCHEDULE",
    "BTN_ADD_SLOTS",
    "BTN_DAY_SCHEDULE",
    "BTN_ALL_SLOTS",
    "BTN_ASSIGN_MOD",
    "BTN_REMOVE_MOD",
    "BTN_ASSIGN_TEACHER",
    "BTN_REMOVE_TEACHER",
    "BTN_EDIT_NAME",
    "BTN_EDIT_PHONE",
    "ROLE_LABELS",
    "NO_REMINDER",
    "REMINDER_OPTIONS",
    "main_menu",
    "profile_actions_inline",
    "phone_request",
    "build_calendar",
    "times_inline",
    "reminder_inline",
    "booking_actions_inline",
    "format_date",
]
