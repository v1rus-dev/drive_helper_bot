"""Booking handlers: book a slot, choose a reminder, list / cancel / reschedule."""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.callbacks import (
    BookingActionCB,
    DateCB,
    ReminderCB,
    SlotCB,
    WeekNavCB,
)
from bot.db.models import User
from bot.db.repositories import (
    get_active_bookings_for_user,
    get_booking,
    get_free_slots_in_range,
    get_free_slots_on_date,
    get_show_weekends,
    get_slot,
    has_free_slots_after,
)
from bot.keyboards import (
    BTN_BOOK,
    BTN_MY,
    NO_REMINDER,
    booking_actions_inline,
    reminder_inline,
    times_inline,
    week_booking_markup,
)
from bot.services.booking_service import apply_reminder, book_slot, cancel_booking
from bot.utils import (
    format_dt,
    format_week_label,
    get_week_bounds,
    humanize_offset,
    to_local,
    visible_weekdays,
)

logger = logging.getLogger(__name__)

router = Router(name="booking")


async def _booking_week_view(
    session: AsyncSession, offset: int
) -> tuple[str, InlineKeyboardMarkup]:
    """Build the ``(text, keyboard)`` for a week's free-day picker.

    Days are the LOCAL (school-TZ) dates within the week that have at least one
    free upcoming slot; grouping is done on ``to_local`` so a 23:00-UTC Sunday
    slot lands on its correct local day. ``offset`` is clamped ``>= 0`` by callers.
    """
    start_utc, end_utc = get_week_bounds(offset)
    free = await get_free_slots_in_range(session, start_utc, end_utc)
    # Only offer visible weekdays; weekend free slots stay hidden while weekends
    # are off (acceptable by design — see the weekend setting).
    visible = set(visible_weekdays(await get_show_weekends(session)))
    days = sorted(
        {
            d
            for slot in free
            if (d := to_local(slot.starts_at).date()).weekday() in visible
        }
    )
    label = format_week_label(start_utc, end_utc)
    show_prev = offset > 0
    show_next = await has_free_slots_after(session, end_utc)
    if days:
        text = f"Свободные дни на неделе {label}. Выберите день:"
    else:
        text = f"На неделе {label} свободных слотов нет."
    return text, week_booking_markup(days, offset, show_prev, show_next, label)


@router.message(StateFilter("*"), F.text == BTN_BOOK)
async def start_booking(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """«Записаться» — show the current week's free days (cancels any in-progress flow)."""
    await state.clear()
    text, markup = await _booking_week_view(session, 0)
    await message.answer(text, reply_markup=markup)


@router.callback_query(WeekNavCB.filter(F.mode == "book"))
async def navigate_booking_week(
    callback: CallbackQuery, callback_data: WeekNavCB, session: AsyncSession
) -> None:
    """Re-render the free-day picker for another week (clamped to the current week)."""
    offset = max(0, callback_data.offset)
    text, markup = await _booking_week_view(session, offset)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(DateCB.filter())
async def pick_date(
    callback: CallbackQuery, callback_data: DateCB, session: AsyncSession
) -> None:
    """After a day is chosen, show that day's free time slots."""
    try:
        chosen = date.fromisoformat(callback_data.value)
    except ValueError:
        await callback.answer("Некорректная дата", show_alert=True)
        return

    slots = await get_free_slots_on_date(session, chosen)
    if not slots:
        await callback.message.edit_text(
            "На эту дату свободных слотов не осталось. Выберите другой день."
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        f"Свободное время на {chosen.strftime('%d.%m.%Y')}:",
        reply_markup=times_inline(slots),
    )
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


@router.message(StateFilter("*"), F.text == BTN_MY)
async def my_bookings(
    message: Message, state: FSMContext, session: AsyncSession, user: Optional[User]
) -> None:
    """List the user's active bookings, each with cancel / reschedule actions.

    Cancels any in-progress flow first so the menu tap always shows the bookings.
    """
    await state.clear()
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
    text, markup = await _booking_week_view(session, 0)
    await callback.message.answer(text, reply_markup=markup)
    await callback.answer()
