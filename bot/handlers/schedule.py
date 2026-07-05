"""Read-only schedule for students: pick a date, see all slots with status.

This view deliberately hides *who* booked an occupied slot — it shows only the
time and «занято». The staff day-schedule (with student ФИО+телефон) is a
separate renderer gated by ``can_view_staff_schedule`` and is never reused here.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.callbacks import ScheduleDateCB, ScheduleNavCB
from bot.db.models import SlotStatus
from bot.db.repositories import get_all_slot_dates, get_all_slots_on_date
from bot.keyboards import BTN_SCHEDULE, build_calendar
from bot.utils import format_time

logger = logging.getLogger(__name__)

router = Router(name="schedule")

_NO_SLOTS = "Слотов пока нет. Загляните позже."


async def _calendar_params(
    session: AsyncSession,
) -> Optional[tuple[set[date], tuple[int, int], tuple[int, int]]]:
    """Dates that have *any* slot plus the (year, month) bounds, or ``None``."""
    dates = await get_all_slot_dates(session)
    if not dates:
        return None
    earliest, latest = min(dates), max(dates)
    return dates, (earliest.year, earliest.month), (latest.year, latest.month)


def _calendar_markup(
    dates: set[date], min_month: tuple[int, int], max_month: tuple[int, int]
):
    year, month = min_month  # first month that has a slot
    return build_calendar(
        year,
        month,
        dates,
        min_month,
        max_month,
        date_cb=ScheduleDateCB,
        nav_cb=ScheduleNavCB,
    )


@router.message(StateFilter(None), F.text == BTN_SCHEDULE)
async def show_schedule(message: Message, session: AsyncSession) -> None:
    """«Расписание» — open the read-only date picker over all days with slots."""
    params = await _calendar_params(session)
    if params is None:
        await message.answer(_NO_SLOTS)
        return
    await message.answer("Выберите дату:", reply_markup=_calendar_markup(*params))


@router.callback_query(ScheduleNavCB.filter())
async def navigate_schedule(
    callback: CallbackQuery, callback_data: ScheduleNavCB, session: AsyncSession
) -> None:
    """Re-render the schedule calendar for the requested month, clamped to range."""
    params = await _calendar_params(session)
    if params is None:
        await callback.message.edit_text(_NO_SLOTS)
        await callback.answer()
        return
    dates, min_month, max_month = params
    # Clamp defensively: callback data is client-supplied and the range may shift.
    target = min(max((callback_data.year, callback_data.month), min_month), max_month)
    year, month = target
    await callback.message.edit_reply_markup(
        reply_markup=build_calendar(
            year,
            month,
            dates,
            min_month,
            max_month,
            date_cb=ScheduleDateCB,
            nav_cb=ScheduleNavCB,
        )
    )
    await callback.answer()


@router.callback_query(ScheduleDateCB.filter())
async def show_day(
    callback: CallbackQuery, callback_data: ScheduleDateCB, session: AsyncSession
) -> None:
    """List every slot on the chosen day with status only (no student PII)."""
    try:
        chosen = date.fromisoformat(callback_data.value)
    except ValueError:
        await callback.answer("Некорректная дата", show_alert=True)
        return

    slots = await get_all_slots_on_date(session, chosen)
    if not slots:
        await callback.message.edit_text(
            "На эту дату слотов нет. Выберите другую дату."
        )
        await callback.answer()
        return

    lines = [f"<b>Расписание на {chosen.strftime('%d.%m.%Y')}:</b>", ""]
    for slot in slots:
        status = "🟢 свободно" if slot.status == SlotStatus.free else "🔴 занято"
        lines.append(f"{format_time(slot.starts_at)} — {status}")
    await callback.message.edit_text("\n".join(lines))
    await callback.answer()
