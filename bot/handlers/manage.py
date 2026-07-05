"""Teacher/admin «Управление записями»: force-free a booked slot or manually
book a registered user onto a free slot.

This is an ADDITIONAL, staff-only capability layered on top of the student
self-service booking (``handlers/booking.py``) — it does NOT touch the student
ownership checks there. Force-free reuses the STAFF cancel path (the service
:func:`cancel_booking` with no ``expected_user_id`` — cancels regardless of
owner, frees the slot and drops the reminder job); manual booking reuses the
atomic :func:`capture_slot_and_book` (no read-then-write race, no reminder for
staff bookings). Notifications to the affected user are best-effort: they are
sent AFTER the DB change is committed and every send is wrapped so a blocked
user can never break the staff action or the transaction.

The flow is entirely button-driven (no text input), so there is no FSM
text-trap to guard. Every entry point AND every callback re-checks
:func:`can_manage_slots` server-side, so a role change mid-flow cannot be
bypassed through stale callback data.
"""

from __future__ import annotations

import logging
from datetime import date as date_cls
from html import escape
from typing import Optional

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.callbacks import (
    ForceFreeCB,
    ManageBookCB,
    ManageDayCB,
    SlotManageCB,
    WeekNavCB,
)
from bot.config import Settings
from bot.db.models import BookingStatus, SlotStatus, User, can_manage_slots
from bot.db.repositories import (
    capture_slot_and_book,
    get_active_booking_for_slot,
    get_all_users,
    get_booking,
    get_slot,
    get_slots_in_range,
    get_slots_on_date,
    get_user,
)
from bot.keyboards import (
    BTN_MANAGE_BOOKINGS,
    force_free_confirm_markup,
    manage_day_markup,
    manage_users_markup,
    manage_week_markup,
)
from bot.services.booking_service import broadcast_cancellation, cancel_booking
from bot.utils import (
    format_day_short,
    format_dt,
    format_time,
    format_week_label,
    get_week_bounds,
    to_local,
)

logger = logging.getLogger(__name__)

router = Router(name="manage")

# Bounded future horizon for the «›» arrow, matching the slot editor. «‹» is
# clamped to offset >= 0.
_MANAGE_MAX_OFFSET = 8

# Cap on the manual-booking user list so a huge roster never overflows the
# inline keyboard; a note is shown when more users exist.
_USER_LIST_CAP = 50


async def _week_view(
    session: AsyncSession, offset: int
) -> tuple[str, InlineKeyboardMarkup]:
    """Build the ``(text, keyboard)`` for the manage week view at ``offset``.

    Slots are grouped by LOCAL day (a 23:00-UTC Sunday slot belongs to its local
    day). Booker ФИО is HTML-escaped (the bot sends ``parse_mode=HTML``).

    Deliberate deviation from the editor / schedule weekend filter: this view
    shows ALL days that HAVE slots regardless of the global weekend-visibility
    setting, so a booking made on a (now-hidden) weekend slot stays manageable
    — otherwise a force-free would be unreachable for it.
    """
    start_utc, end_utc = get_week_bounds(offset)
    rows = await get_slots_in_range(session, start_utc, end_utc)

    by_day: dict[date_cls, list] = {}
    for slot, _booking, booker in rows:
        by_day.setdefault(to_local(slot.starts_at).date(), []).append((slot, booker))

    label = format_week_label(start_utc, end_utc)
    days = sorted(by_day.keys())

    lines = [f"<b>Управление записями {label}</b>", ""]
    if not days:
        lines.append("На этой неделе слотов нет.")
    for d in days:
        lines.append(f"<b>{format_day_short(d)}</b>")
        # ``rows`` come ordered by starts_at, so per-day slots stay ascending.
        for slot, booker in by_day[d]:
            t = format_time(slot.starts_at)
            if slot.status == SlotStatus.booked and booker is not None:
                lines.append(f"{t} — занято: {escape(booker.full_name)}")
            elif slot.status == SlotStatus.booked:
                lines.append(f"{t} — занято")
            else:
                lines.append(f"{t} — свободно")
        lines.append("")
    text = "\n".join(lines).rstrip()

    show_prev = offset > 0
    show_next = offset < _MANAGE_MAX_OFFSET
    markup = manage_week_markup(days, offset, show_prev, show_next, label)
    return text, markup


async def _day_view(
    session: AsyncSession, local_date: date_cls, offset: int
) -> tuple[str, InlineKeyboardMarkup]:
    """Build the ``(text, keyboard)`` for one day's slots in the manage flow."""
    rows = await get_slots_on_date(session, local_date)
    if rows:
        text = (
            f"Слоты на {format_day_short(local_date)}. "
            "Выберите слот, чтобы освободить или записать ученика."
        )
    else:
        text = f"На {format_day_short(local_date)} слотов нет."
    return text, manage_day_markup(rows, offset)


@router.message(StateFilter("*"), F.text == BTN_MANAGE_BOOKINGS)
async def manage_start(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    """«Управление записями» — render the current week (cancels any in-progress flow)."""
    # Cancel any in-progress flow so a menu tap always (re)starts this one; the
    # permission re-check below still gates the action server-side.
    await state.clear()
    if not can_manage_slots(user, settings):
        await message.answer("Недостаточно прав.")
        return
    text, markup = await _week_view(session, 0)
    await message.answer(text, reply_markup=markup)


@router.callback_query(WeekNavCB.filter(F.mode == "manage"))
async def manage_navigate(
    callback: CallbackQuery,
    callback_data: WeekNavCB,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    """Re-render the manage week view for another week (clamped to ``[0, MAX]``).

    Doubles as the «‹ Назад к неделе» target from the day view.
    """
    if not can_manage_slots(user, settings):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    offset = max(0, min(callback_data.offset, _MANAGE_MAX_OFFSET))
    text, markup = await _week_view(session, offset)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(ManageDayCB.filter())
async def manage_day(
    callback: CallbackQuery,
    callback_data: ManageDayCB,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    """Show a day's slots as inline buttons (also the «Назад» target)."""
    if not can_manage_slots(user, settings):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    try:
        local_date = date_cls.fromisoformat(callback_data.date)
    except ValueError:
        await callback.answer("Некорректная дата", show_alert=True)
        return
    offset = max(0, min(callback_data.offset, _MANAGE_MAX_OFFSET))
    text, markup = await _day_view(session, local_date, offset)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(SlotManageCB.filter())
async def manage_slot(
    callback: CallbackQuery,
    callback_data: SlotManageCB,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    """Tap a slot: confirm freeing a booked one, or offer users for a free one."""
    if not can_manage_slots(user, settings):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    offset = max(0, min(callback_data.offset, _MANAGE_MAX_OFFSET))
    slot = await get_slot(session, callback_data.slot_id)
    if slot is None:
        # Slot vanished (e.g. deleted meanwhile) — fall back to the week view.
        await callback.answer("Слот не найден", show_alert=True)
        text, markup = await _week_view(session, offset)
        await callback.message.edit_text(text, reply_markup=markup)
        return

    local_date = to_local(slot.starts_at).date()
    when = format_dt(slot.starts_at)

    if slot.status == SlotStatus.booked:
        found = await get_active_booking_for_slot(session, slot.id)
        if found is None:
            # Booked flag but no active booking (race) — re-render the day.
            await callback.answer("Слот уже свободен", show_alert=True)
            text, markup = await _day_view(session, local_date, offset)
            await callback.message.edit_text(text, reply_markup=markup)
            return
        booking, booker = found
        name = escape(booker.full_name) if booker is not None else "неизвестно"
        await callback.message.edit_text(
            f"Слот {when}, записан: {name}. Освободить?",
            reply_markup=force_free_confirm_markup(booking.id, local_date, offset),
        )
        await callback.answer()
        return

    # Free slot -> pick a user to book manually.
    users = list(await get_all_users(session))
    if not users:
        await callback.message.edit_text(
            f"Записать ученика на {when}: нет пользователей.",
            reply_markup=manage_users_markup([], slot.id, local_date, offset),
        )
        await callback.answer()
        return
    note = ""
    if len(users) > _USER_LIST_CAP:
        note = f"\n\nПоказаны первые {_USER_LIST_CAP} из {len(users)}."
        users = users[:_USER_LIST_CAP]
    await callback.message.edit_text(
        f"Записать ученика на {when}. Выберите пользователя:{note}",
        reply_markup=manage_users_markup(users, slot.id, local_date, offset),
    )
    await callback.answer()


@router.callback_query(ForceFreeCB.filter())
async def manage_force_free(
    callback: CallbackQuery,
    callback_data: ForceFreeCB,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    """Staff-cancel a booking (no ownership check): frees the slot + drops the
    reminder, then notifies the affected user (best-effort)."""
    if not can_manage_slots(user, settings):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    offset = max(0, min(callback_data.offset, _MANAGE_MAX_OFFSET))

    booking = await get_booking(session, callback_data.booking_id)
    if booking is None:
        # Booking row gone entirely — fall back to the week view.
        await callback.answer("Запись уже отменена", show_alert=True)
        text, markup = await _week_view(session, offset)
        await callback.message.edit_text(text, reply_markup=markup)
        return

    slot = await get_slot(session, booking.slot_id)
    local_date = to_local(slot.starts_at).date() if slot is not None else None
    when = format_dt(slot.starts_at) if slot is not None else ""

    if booking.status != BookingStatus.active:
        await callback.answer("Запись уже отменена", show_alert=True)
        if local_date is not None:
            text, markup = await _day_view(session, local_date, offset)
        else:
            text, markup = await _week_view(session, offset)
        await callback.message.edit_text(text, reply_markup=markup)
        return

    target_tg_id = booking.user_id
    # Capture the booked student's ФИО BEFORE cancelling (for the broadcast). The
    # user row is untouched by the cancel, but load it while we hold the booking.
    booked_user = await get_user(session, target_tg_id)
    booked_name = booked_user.full_name if booked_user is not None else "ученик"
    # STAFF cancel: no expected_user_id -> cancels regardless of owner, frees the
    # slot and removes the reminder job. DB is committed inside the service.
    await cancel_booking(session, callback_data.booking_id)

    # Best-effort notification AFTER the DB change is committed; a blocked user
    # must never break the staff action or the transaction.
    try:
        await callback.bot.send_message(
            target_tg_id,
            f"Ваша запись на {when} была отменена преподавателем.",
        )
    except Exception:  # noqa: BLE001 - notification is best-effort
        logger.info("Could not notify user %s about force-free", target_tg_id)

    await callback.answer("Слот освобождён.")
    if local_date is not None:
        text, markup = await _day_view(session, local_date, offset)
    else:
        text, markup = await _week_view(session, offset)
    await callback.message.edit_text(text, reply_markup=markup)

    # IN ADDITION to the direct notice above: broadcast to every OTHER registered
    # user that the slot is free again. Best-effort, non-blocking. Excluded: the
    # staff actor (they performed the action) AND the affected student (already
    # notified directly above — the third-person broadcast would be redundant).
    if slot is not None:
        await broadcast_cancellation(
            callback.bot,
            session,
            actor_is_staff=True,
            booked_name=booked_name,
            slot_dt=slot.starts_at,
            exclude_tg_ids={callback.from_user.id, target_tg_id},
        )


@router.callback_query(ManageBookCB.filter())
async def manage_book_user(
    callback: CallbackQuery,
    callback_data: ManageBookCB,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    """Manually book the chosen user onto the slot (atomic, no reminder)."""
    if not can_manage_slots(user, settings):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    offset = max(0, min(callback_data.offset, _MANAGE_MAX_OFFSET))

    slot = await get_slot(session, callback_data.slot_id)
    if slot is None:
        await callback.answer("Слот не найден", show_alert=True)
        text, markup = await _week_view(session, offset)
        await callback.message.edit_text(text, reply_markup=markup)
        return

    local_date = to_local(slot.starts_at).date()
    when = format_dt(slot.starts_at)

    # Atomic capture: a single guarded UPDATE, never a read-then-write, so a
    # student booking the same slot concurrently cannot double-book. offset=None
    # -> no reminder for staff bookings.
    booking = await capture_slot_and_book(
        session, callback_data.slot_id, callback_data.tg_id, offset=None
    )
    if booking is None:
        await callback.answer("Слот уже занят", show_alert=True)
        text, markup = await _day_view(session, local_date, offset)
        await callback.message.edit_text(text, reply_markup=markup)
        return

    # Best-effort notification AFTER commit; ignore a blocked target.
    try:
        await callback.bot.send_message(
            callback_data.tg_id,
            f"Вы записаны на занятие: {when}.",
        )
    except Exception:  # noqa: BLE001 - notification is best-effort
        logger.info("Could not notify user %s about manual booking", callback_data.tg_id)

    await callback.answer("Ученик записан.")
    text, markup = await _day_view(session, local_date, offset)
    await callback.message.edit_text(text, reply_markup=markup)
