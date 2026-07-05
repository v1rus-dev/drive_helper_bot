"""Moderator (+admin) handlers: add slots, view the day schedule."""

from __future__ import annotations

import logging
import re
from html import escape
from typing import Optional

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings, get_settings
from bot.db.models import (
    Booking,
    Slot,
    SlotStatus,
    User,
    can_view_staff_schedule,
    is_moderator_or_admin,
)
from bot.db.repositories import (
    create_slots,
    get_slots_on_date,
    get_upcoming_slots,
)
from bot.keyboards import BTN_ADD_SLOTS, BTN_ALL_SLOTS, BTN_DAY_SCHEDULE
from bot.states import AddSlots, DaySchedule
from bot.utils import (
    combine_local_to_utc,
    format_dt,
    format_time,
    parse_local_date,
    parse_local_time,
    utcnow,
)

logger = logging.getLogger(__name__)

router = Router(name="moderator")

_TIME_SEP = re.compile(r"[\s,]+")

# Cap the number of time tokens accepted in a single "add slots" submission.
_MAX_SLOT_TOKENS = 50


def _authorized(user: Optional[User], settings: Settings) -> bool:
    return is_moderator_or_admin(user, settings)


# --- Add slots -----------------------------------------------------------

@router.message(StateFilter(None), F.text == BTN_ADD_SLOTS)
async def add_slots_start(
    message: Message, state: FSMContext, user: Optional[User], settings: Settings
) -> None:
    if not _authorized(user, settings):
        await message.answer("Недостаточно прав.")
        return
    await message.answer("Введите дату для слотов в формате ДД.ММ.ГГГГ:")
    await state.set_state(AddSlots.date)


@router.message(AddSlots.date, F.text)
async def add_slots_date(
    message: Message,
    state: FSMContext,
    user: Optional[User],
    settings: Settings,
) -> None:
    # Re-check server-side: role is DB-backed and a moderator demoted mid-flow
    # must not be able to continue creating slots through a stale FSM state.
    if not _authorized(user, settings):
        await state.clear()
        await message.answer("Недостаточно прав.")
        return
    try:
        local_date = parse_local_date(message.text or "")
    except ValueError:
        await message.answer("Неверный формат даты. Введите как ДД.ММ.ГГГГ:")
        return
    await state.update_data(date=local_date.isoformat())
    await message.answer(
        "Введите время слотов через пробел или запятую, например: 09:00 10:30 12:00"
    )
    await state.set_state(AddSlots.times)


@router.message(AddSlots.date, ~F.text)
async def add_slots_date_invalid(message: Message) -> None:
    """Non-text input while entering the date — re-prompt (avoids a silent drop)."""
    await message.answer("Введите дату текстом в формате ДД.ММ.ГГГГ:")


@router.message(AddSlots.times, F.text)
async def add_slots_times(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    # Re-check server-side: role is DB-backed and a moderator demoted mid-flow
    # must not be able to continue creating slots through a stale FSM state.
    if not _authorized(user, settings):
        await state.clear()
        await message.answer("Недостаточно прав.")
        return

    from datetime import date as date_cls

    data = await state.get_data()
    local_date = date_cls.fromisoformat(data["date"])

    tokens = [t for t in _TIME_SEP.split((message.text or "").strip()) if t]
    if not tokens:
        await message.answer("Не найдено ни одного времени. Попробуйте ещё раз:")
        return

    if len(tokens) > _MAX_SLOT_TOKENS:
        await message.answer(
            f"Слишком много значений времени за один раз "
            f"(максимум {_MAX_SLOT_TOKENS}). Отправьте меньше и попробуйте снова:"
        )
        return

    starts_utc: list = []
    for token in tokens:
        try:
            local_time = parse_local_time(token)
        except ValueError:
            await message.answer(
                f"Неверный формат времени: «{token}». Используйте ЧЧ:ММ. "
                "Введите все значения заново:"
            )
            return
        starts_utc.append(combine_local_to_utc(local_date, local_time))

    now = utcnow()
    future = [dt for dt in starts_utc if dt > now]
    skipped_past = len(starts_utc) - len(future)

    duration = get_settings().default_slot_duration_min
    created, skipped_dup = await create_slots(
        session, future, created_by=message.from_user.id, duration_min=duration
    )

    await state.clear()
    await message.answer(
        f"Создано слотов: {created}.\n"
        f"Пропущено (в прошлом): {skipped_past}.\n"
        f"Пропущено (дубликаты): {skipped_dup}."
    )


@router.message(AddSlots.times, ~F.text)
async def add_slots_times_invalid(message: Message) -> None:
    """Non-text input while entering times — re-prompt (avoids a silent drop)."""
    await message.answer(
        "Введите время слотов текстом через пробел или запятую, "
        "например: 09:00 10:30 12:00"
    )


# --- Day schedule (staff view: shows student ФИО+телефон) ----------------

def render_staff_day_schedule(
    rows: list[tuple["Slot", Optional[Booking], Optional[User]]],
    local_date,
) -> str:
    """Render the staff day schedule, revealing the booking student's ФИО+телефон.

    Shared by moderators and teachers (both gated by
    :func:`can_view_staff_schedule`). All user-supplied text is HTML-escaped —
    the bot sends ``parse_mode=HTML`` bot-wide. Students must never see this
    renderer; the student schedule has its own PII-free version.
    """
    lines = [f"<b>Расписание на {local_date.strftime('%d.%m.%Y')}:</b>", ""]
    for slot, _booking, student in rows:
        line = f"{format_time(slot.starts_at)} — "
        if slot.status == SlotStatus.booked and student is not None:
            line += f"занято: {escape(student.full_name)}, {escape(student.phone)}"
        elif slot.status == SlotStatus.booked:
            line += "занято"
        else:
            line += "свободно"
        lines.append(line)
    return "\n".join(lines)


@router.message(StateFilter(None), F.text == BTN_DAY_SCHEDULE)
async def day_schedule_start(
    message: Message, state: FSMContext, user: Optional[User], settings: Settings
) -> None:
    # Widened from moderator-only: teachers may also view the staff schedule.
    if not can_view_staff_schedule(user, settings):
        await message.answer("Недостаточно прав.")
        return
    await message.answer("Введите дату в формате ДД.ММ.ГГГГ:")
    await state.set_state(DaySchedule.date)


@router.message(DaySchedule.date, F.text)
async def day_schedule_show(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    # Re-check server-side: only reachable via the gated start, but defense in
    # depth ensures a student cannot land here through a stale FSM state.
    if not can_view_staff_schedule(user, settings):
        await state.clear()
        await message.answer("Недостаточно прав.")
        return

    try:
        local_date = parse_local_date(message.text or "")
    except ValueError:
        await message.answer("Неверный формат даты. Введите как ДД.ММ.ГГГГ:")
        return

    rows = await get_slots_on_date(session, local_date)
    await state.clear()

    if not rows:
        await message.answer(
            f"На {local_date.strftime('%d.%m.%Y')} слотов нет."
        )
        return

    await message.answer(render_staff_day_schedule(rows, local_date))


@router.message(DaySchedule.date, ~F.text)
async def day_schedule_invalid(message: Message) -> None:
    """Non-text input while entering the date — re-prompt (avoids a silent drop)."""
    await message.answer("Введите дату текстом в формате ДД.ММ.ГГГГ:")


# --- All upcoming slots (staff overview: status only, no PII) ------------

@router.message(StateFilter(None), F.text == BTN_ALL_SLOTS)
async def all_slots(
    message: Message,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    """Overview of upcoming slots with occupancy status (no student PII)."""
    if not can_view_staff_schedule(user, settings):
        await message.answer("Недостаточно прав.")
        return

    slots = await get_upcoming_slots(session)
    if not slots:
        await message.answer("Ближайших слотов нет.")
        return

    lines = ["<b>Ближайшие слоты:</b>", ""]
    for slot in slots:
        status = "🟢 свободно" if slot.status == SlotStatus.free else "🔴 занято"
        lines.append(f"{format_dt(slot.starts_at)} — {status}")
    await message.answer("\n".join(lines))
