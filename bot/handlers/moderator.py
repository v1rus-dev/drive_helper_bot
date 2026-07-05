"""Moderator (+admin) handlers: add slots, view the day schedule."""

from __future__ import annotations

import logging
import re
from typing import Optional

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings, get_settings
from bot.db.models import SlotStatus, User, is_moderator_or_admin
from bot.db.repositories import create_slots, get_slots_on_date
from bot.keyboards import BTN_ADD_SLOTS, BTN_DAY_SCHEDULE
from bot.states import AddSlots, DaySchedule
from bot.utils import (
    combine_local_to_utc,
    format_time,
    parse_local_date,
    parse_local_time,
    utcnow,
)

logger = logging.getLogger(__name__)

router = Router(name="moderator")

_TIME_SEP = re.compile(r"[\s,]+")


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
async def add_slots_date(message: Message, state: FSMContext) -> None:
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


@router.message(AddSlots.times, F.text)
async def add_slots_times(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    from datetime import date as date_cls

    data = await state.get_data()
    local_date = date_cls.fromisoformat(data["date"])

    tokens = [t for t in _TIME_SEP.split((message.text or "").strip()) if t]
    if not tokens:
        await message.answer("Не найдено ни одного времени. Попробуйте ещё раз:")
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


# --- Day schedule --------------------------------------------------------

@router.message(StateFilter(None), F.text == BTN_DAY_SCHEDULE)
async def day_schedule_start(
    message: Message, state: FSMContext, user: Optional[User], settings: Settings
) -> None:
    if not _authorized(user, settings):
        await message.answer("Недостаточно прав.")
        return
    await message.answer("Введите дату в формате ДД.ММ.ГГГГ:")
    await state.set_state(DaySchedule.date)


@router.message(DaySchedule.date, F.text)
async def day_schedule_show(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
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

    lines = [f"<b>Расписание на {local_date.strftime('%d.%m.%Y')}:</b>", ""]
    for slot, booking, student in rows:
        line = f"{format_time(slot.starts_at)} — "
        if slot.status == SlotStatus.booked and student is not None:
            line += f"занято: {student.full_name}, {student.phone}"
        elif slot.status == SlotStatus.booked:
            line += "занято"
        else:
            line += "свободно"
        lines.append(line)

    await message.answer("\n".join(lines))
