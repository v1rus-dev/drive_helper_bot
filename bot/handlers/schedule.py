"""Weekly schedule for everyone: current week, day-by-day, with booker names.

Available to ALL registered users (student / teacher / admin). Free slots show
«свободно»; booked slots reveal the booker's ФИО (a product decision by the
owner). All names are HTML-escaped — the bot sends ``parse_mode=HTML`` bot-wide.
The week is navigated with ‹/› buttons; ``offset`` is clamped to the current week.
"""

from __future__ import annotations

import logging
from datetime import datetime
from html import escape
from typing import Optional

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.callbacks import WeekNavCB
from bot.db.models import Booking, Slot, SlotStatus, User
from bot.db.repositories import get_show_weekends, get_slots_in_range, has_slots_after
from bot.keyboards import BTN_SCHEDULE, week_schedule_markup
from bot.utils import (
    format_day_full,
    format_time,
    format_week_label,
    get_week_bounds,
    to_local,
    visible_weekdays,
)

logger = logging.getLogger(__name__)

router = Router(name="schedule")


def render_week_schedule(
    rows: list[tuple[Slot, Optional[Booking], Optional[User]]],
    start_utc: datetime,
    end_utc: datetime,
    visible: set[int],
) -> str:
    """Render a week's slots grouped by LOCAL day, with booker ФИО for busy slots.

    ``rows`` come ordered by ``starts_at`` (see :func:`get_slots_in_range`), so
    per-day times stay ascending. Grouping is on ``to_local`` — a 23:00-UTC Sunday
    slot belongs to its local day, not the naive-UTC one. Booker names are escaped
    (the bot sends ``parse_mode=HTML``). Days with no slots are omitted.

    ``visible`` is the set of ``date.weekday()`` ints allowed by the weekend
    setting; days outside it (Sat/Sun when weekends are off) are dropped. Edge
    note: weekend slots that exist while weekends are off stay hidden until the
    setting is re-enabled — acceptable by design.
    """
    label = format_week_label(start_utc, end_utc)
    by_day: dict = {}
    for slot, _booking, booker in rows:
        day = to_local(slot.starts_at).date()
        if day.weekday() not in visible:
            continue
        by_day.setdefault(day, []).append((slot, booker))

    if not by_day:
        return f"<b>Расписание {label}</b>\n\nНа этой неделе слотов нет."

    lines = [f"<b>Расписание {label}</b>", ""]
    for day in sorted(by_day):
        lines.append(f"<b>{format_day_full(day)}</b>")
        for slot, booker in by_day[day]:
            t = format_time(slot.starts_at)
            if slot.status == SlotStatus.booked and booker is not None:
                lines.append(f"{t} — занято: {escape(booker.full_name)}")
            elif slot.status == SlotStatus.booked:
                lines.append(f"{t} — занято")
            else:
                lines.append(f"{t} — свободно")
        lines.append("")
    return "\n".join(lines).rstrip()


async def _week_view(
    session: AsyncSession, offset: int
) -> tuple[str, InlineKeyboardMarkup]:
    """Build the ``(text, keyboard)`` for the schedule of the week at ``offset``."""
    start_utc, end_utc = get_week_bounds(offset)
    rows = await get_slots_in_range(session, start_utc, end_utc)
    visible = set(visible_weekdays(await get_show_weekends(session)))
    text = render_week_schedule(rows, start_utc, end_utc, visible)
    show_prev = offset > 0
    show_next = await has_slots_after(session, end_utc)
    markup = week_schedule_markup(
        offset, show_prev, show_next, format_week_label(start_utc, end_utc)
    )
    return text, markup


@router.message(StateFilter("*"), F.text == BTN_SCHEDULE)
async def show_schedule(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """«Расписание» — render the current week (cancels any in-progress flow)."""
    await state.clear()
    text, markup = await _week_view(session, 0)
    await message.answer(text, reply_markup=markup)


@router.callback_query(WeekNavCB.filter(F.mode == "schedule"))
async def navigate_schedule(
    callback: CallbackQuery, callback_data: WeekNavCB, session: AsyncSession
) -> None:
    """Re-render the schedule for another week (clamped to the current week)."""
    offset = max(0, callback_data.offset)
    text, markup = await _week_view(session, offset)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()
