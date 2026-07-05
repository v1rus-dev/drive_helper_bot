"""Booking handlers: book a slot, choose a reminder, list / cancel / reschedule."""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.callbacks import (
    BookingActionCB,
    CalendarIgnoreCB,
    CalendarNavCB,
    DateCB,
    ReminderCB,
    SlotCB,
)
from bot.config import Settings
from bot.db.models import User
from bot.db.repositories import (
    get_active_bookings_for_user,
    get_booking,
    get_free_slot_dates,
    get_free_slots_on_date,
    get_slot,
)
from bot.keyboards import (
    BTN_BOOK,
    BTN_MY,
    NO_REMINDER,
    booking_actions_inline,
    build_calendar,
    reminder_inline,
    times_inline,
)
from bot.services.booking_service import apply_reminder, book_slot, cancel_booking
from bot.utils import format_dt, humanize_offset

logger = logging.getLogger(__name__)

router = Router(name="booking")


async def _calendar_params(
    session: AsyncSession,
) -> Optional[tuple[set[date], tuple[int, int], tuple[int, int]]]:
    """Available dates plus the (year, month) bounds, or ``None`` when there are none.

    Dates are school-TZ dates; the month bounds come from the earliest / latest
    available date so navigation can be clamped to months that actually have slots.
    """
    dates = await get_free_slot_dates(session)
    if not dates:
        return None
    earliest, latest = min(dates), max(dates)
    return dates, (earliest.year, earliest.month), (latest.year, latest.month)


async def _send_date_picker(message: Message, session: AsyncSession) -> None:
    """Show the inline calendar for the first month that has free slots."""
    params = await _calendar_params(session)
    if params is None:
        await message.answer("Сейчас нет свободных слотов. Загляните позже.")
        return
    dates, min_month, max_month = params
    year, month = min_month  # first available month
    await message.answer(
        "Выберите дату:",
        reply_markup=build_calendar(year, month, dates, min_month, max_month),
    )


@router.message(StateFilter(None), F.text == BTN_BOOK)
async def start_booking(message: Message, session: AsyncSession) -> None:
    """«Записаться» — begin the booking flow."""
    await _send_date_picker(message, session)


@router.callback_query(DateCB.filter())
async def pick_date(
    callback: CallbackQuery, callback_data: DateCB, session: AsyncSession
) -> None:
    """After a date is chosen, show the available time slots."""
    try:
        chosen = date.fromisoformat(callback_data.value)
    except ValueError:
        await callback.answer("Некорректная дата", show_alert=True)
        return

    slots = await get_free_slots_on_date(session, chosen)
    if not slots:
        await callback.message.edit_text(
            "На эту дату свободных слотов не осталось. Выберите другую дату."
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        f"Свободное время на {chosen.strftime('%d.%m.%Y')}:",
        reply_markup=times_inline(slots),
    )
    await callback.answer()


@router.callback_query(CalendarNavCB.filter())
async def navigate_calendar(
    callback: CallbackQuery, callback_data: CalendarNavCB, session: AsyncSession
) -> None:
    """Re-render the calendar for the requested month, clamped to the slot range."""
    params = await _calendar_params(session)
    if params is None:
        await callback.message.edit_text("Сейчас нет свободных слотов. Загляните позже.")
        await callback.answer()
        return
    dates, min_month, max_month = params
    # Clamp defensively: the arrows already hide out-of-range months, but callback
    # data is client-supplied and the available range may shift between renders.
    target = min(max((callback_data.year, callback_data.month), min_month), max_month)
    year, month = target
    await callback.message.edit_reply_markup(
        reply_markup=build_calendar(year, month, dates, min_month, max_month)
    )
    await callback.answer()


@router.callback_query(CalendarIgnoreCB.filter())
async def ignore_calendar_tap(callback: CallbackQuery) -> None:
    """No-op tap on a placeholder / disabled arrow / label — just clear the spinner."""
    await callback.answer()


@router.callback_query(SlotCB.filter())
async def pick_slot(
    callback: CallbackQuery,
    callback_data: SlotCB,
    session: AsyncSession,
    user: Optional[User],
) -> None:
    """Attempt an atomic booking of the chosen slot, then ask about reminders."""
    if user is None:
        await callback.answer("Пожалуйста, начните с команды /start", show_alert=True)
        return

    booking = await book_slot(session, callback_data.slot_id, user)
    if booking is None:
        await callback.message.edit_text("Слот уже занят, выберите другой.")
        await callback.answer()
        return

    slot = await get_slot(session, booking.slot_id)
    when = format_dt(slot.starts_at) if slot else ""
    await callback.message.edit_text(
        f"Слот {when} забронирован!\nКогда напомнить о занятии?",
        reply_markup=reminder_inline(booking.id),
    )
    await callback.answer()


@router.callback_query(ReminderCB.filter())
async def choose_reminder(
    callback: CallbackQuery,
    callback_data: ReminderCB,
    session: AsyncSession,
    bot,
    user: Optional[User],
) -> None:
    """Store the reminder preference and schedule it when it is still ahead."""
    booking = await get_booking(session, callback_data.booking_id)
    # Ownership guard: booking_id comes from attacker-controllable callback data.
    if booking is None or user is None or booking.user_id != user.tg_id:
        await callback.answer("Запись не найдена", show_alert=True)
        return

    slot = await get_slot(session, booking.slot_id)
    when = format_dt(slot.starts_at) if slot else ""
    offset: Optional[int] = None if callback_data.offset == NO_REMINDER else callback_data.offset

    scheduled = await apply_reminder(
        session, booking, offset, bot, chat_id=callback.from_user.id
    )

    if offset is None:
        text = f"Запись подтверждена на {when}. Напоминание отключено."
    elif scheduled:
        text = f"Запись подтверждена на {when}. Напомним за {humanize_offset(offset)}."
    else:
        text = (
            f"Запись подтверждена на {when}. Напоминание не установлено — "
            "занятие слишком скоро."
        )
    await callback.message.edit_text(text)
    await callback.answer()


@router.message(StateFilter(None), F.text == BTN_MY)
async def my_bookings(
    message: Message, session: AsyncSession, user: Optional[User]
) -> None:
    """List the user's active bookings, each with cancel / reschedule actions."""
    if user is None:
        await message.answer("Пожалуйста, начните с команды /start")
        return

    rows = await get_active_bookings_for_user(session, user.tg_id)
    if not rows:
        await message.answer("У вас нет активных записей.")
        return

    await message.answer("Ваши записи:")
    for booking, slot in rows:
        await message.answer(
            f"Занятие: {format_dt(slot.starts_at)}",
            reply_markup=booking_actions_inline(booking),
        )


@router.callback_query(BookingActionCB.filter(F.action == "cancel"))
async def cancel_action(
    callback: CallbackQuery,
    callback_data: BookingActionCB,
    session: AsyncSession,
    user: Optional[User],
) -> None:
    """Cancel a booking (owner only), free the slot and drop its reminder."""
    booking = await get_booking(session, callback_data.booking_id)
    # Ownership guard: booking_id comes from attacker-controllable callback data.
    if booking is None or user is None or booking.user_id != user.tg_id:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    # expected_user_id re-checks ownership at the data layer (defense in depth).
    await cancel_booking(session, callback_data.booking_id, expected_user_id=user.tg_id)
    await callback.message.edit_text("Запись отменена.")
    await callback.answer()


@router.callback_query(BookingActionCB.filter(F.action == "resched"))
async def reschedule_action(
    callback: CallbackQuery,
    callback_data: BookingActionCB,
    session: AsyncSession,
    user: Optional[User],
) -> None:
    """Reschedule (owner only): cancel the current booking, then pick a new slot."""
    booking = await get_booking(session, callback_data.booking_id)
    # Ownership guard: booking_id comes from attacker-controllable callback data.
    if booking is None or user is None or booking.user_id != user.tg_id:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    # expected_user_id re-checks ownership at the data layer (defense in depth).
    await cancel_booking(session, callback_data.booking_id, expected_user_id=user.tg_id)
    await callback.message.edit_text("Прежняя запись отменена. Выберите новое время.")
    await _send_date_picker(callback.message, session)
    await callback.answer()
