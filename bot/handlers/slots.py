"""Teacher/admin handlers: button-driven weekly slot editor + settings.

Slot management is fully button-driven now: a weekly editor («Ведение
расписания») renders a week, offers a per-day time grid and a
"set-whole-week" grid, and applies each edit as a day *override*. Removing a
time that has an ACTIVE booking now CANCELS that booking (frees + removes the
slot, drops its reminder, notifies the student directly) — but only after an
explicit confirmation step listing the affected bookings. A default-times preset
(«Времена по умолчанию») and a global weekend-visibility toggle («Настройки»)
round it out. There is no text date entry anywhere.

Every entry point *and* every continuation step re-checks :func:`can_manage_slots`
server-side, so a role change mid-flow cannot be bypassed through a stale FSM.
(The read-only occupancy view is the weekly «Расписание», available to everyone —
see ``handlers/schedule.py``.)
"""

from __future__ import annotations

import logging
from datetime import date as date_cls, timedelta
from html import escape
from typing import Optional

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.callbacks import (
    EditDayCB,
    SettingsCB,
    SetWeekCB,
    SlotOverrideConfirmCB,
    TimeCtrlCB,
    TimeToggleCB,
    WeekNavCB,
)
from bot.config import Settings, get_settings
from bot.db.models import SlotStatus, User, can_manage_slots
from bot.db.repositories import (
    get_setting,
    get_show_weekends,
    get_slots_in_range,
    get_slots_on_date,
    set_setting,
    set_show_weekends,
)
from bot.handlers.common import show_main_menu
from bot.keyboards import (
    BTN_DEFAULT_TIMES,
    BTN_MANAGE_SCHEDULE,
    BTN_SETTINGS,
    override_confirm_markup,
    settings_inline,
    time_grid,
    week_editor_markup,
)
from bot.services.booking_service import (
    SlotCancellation,
    apply_day_override,
    compute_day_override,
)
from bot.states import DefaultTimes, SlotEditor
from bot.utils import (
    format_day_short,
    format_dt,
    format_time,
    format_week_label,
    get_week_bounds,
    to_local,
    visible_weekdays,
)

logger = logging.getLogger(__name__)

router = Router(name="slots")

# Key under which the default time preset is stored (comma-separated HH:MM).
_DEFAULT_TIMES_KEY = "default_slot_times"

# The two states in which the button-based time picker is active.
_PICKING_STATES = (DefaultTimes.picking, SlotEditor.picking)

# Bounded future horizon for the editor's «›» arrow. The teacher creates future
# weeks, so «›» is ALWAYS offered (even for empty weeks) up to this offset — not
# gated on "are there slots ahead" like the read-only schedule/booking views —
# so empty future weeks can still be populated. «‹» is clamped to offset >= 0.
_EDITOR_MAX_OFFSET = 8


def _load_preset(raw: Optional[str]) -> list[str]:
    """Split a stored comma-separated preset into a sorted list of times."""
    if not raw:
        return []
    return sorted(t for t in (part.strip() for part in raw.split(",")) if t)


def _send_grid_markup(settings: Settings, selected: list[str]) -> InlineKeyboardMarkup:
    """Time-picker grid pre-selected with ``selected``."""
    return time_grid(settings.candidate_slot_times(), set(selected))


# --- Time picker: toggle / clear (shared by the preset and editor grids) ---

@router.callback_query(StateFilter(*_PICKING_STATES), TimeToggleCB.filter())
async def time_toggle(
    callback: CallbackQuery,
    callback_data: TimeToggleCB,
    state: FSMContext,
    user: Optional[User],
    settings: Settings,
) -> None:
    """Flip one time's membership, persist to FSM data, re-render the grid."""
    if not can_manage_slots(user, settings):
        await state.clear()
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    data = await state.get_data()
    selected = set(data.get("selected", []))
    if callback_data.t in selected:
        selected.discard(callback_data.t)
    else:
        selected.add(callback_data.t)
    await state.update_data(selected=sorted(selected))
    await callback.message.edit_reply_markup(
        reply_markup=time_grid(settings.candidate_slot_times(), selected)
    )
    await callback.answer()


@router.callback_query(StateFilter(*_PICKING_STATES), TimeCtrlCB.filter(F.action == "clear"))
async def time_clear(
    callback: CallbackQuery,
    state: FSMContext,
    user: Optional[User],
    settings: Settings,
) -> None:
    """Empty the selection and re-render (no-op if already empty)."""
    if not can_manage_slots(user, settings):
        await state.clear()
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    data = await state.get_data()
    if data.get("selected"):
        await state.update_data(selected=[])
        await callback.message.edit_reply_markup(
            reply_markup=time_grid(settings.candidate_slot_times(), set())
        )
    await callback.answer()


# --- Default time preset -------------------------------------------------

@router.message(StateFilter("*"), F.text == BTN_DEFAULT_TIMES)
async def default_times_start(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    # Cancel any in-progress flow so a menu tap always (re)starts this one; the
    # permission re-check below still gates the action server-side.
    await state.clear()
    if not can_manage_slots(user, settings):
        await message.answer("Недостаточно прав.")
        return
    preset = _load_preset(await get_setting(session, _DEFAULT_TIMES_KEY))
    await state.set_state(DefaultTimes.picking)
    await state.update_data(selected=preset)
    await message.answer(
        "Времена по умолчанию. Отметьте нужные времена и нажмите «Готово».",
        reply_markup=_send_grid_markup(settings, preset),
    )


@router.callback_query(DefaultTimes.picking, TimeCtrlCB.filter(F.action == "done"))
async def default_times_done(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    if not can_manage_slots(user, settings):
        await state.clear()
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    data = await state.get_data()
    selected = sorted(set(data.get("selected", [])))
    await set_setting(session, _DEFAULT_TIMES_KEY, ",".join(selected))
    await state.clear()
    if selected:
        await callback.message.edit_text(
            "Времена по умолчанию сохранены: " + ", ".join(selected)
        )
    else:
        await callback.message.edit_text("Времена по умолчанию очищены.")
    await show_main_menu(callback.message, user, settings)
    await callback.answer()


@router.callback_query(DefaultTimes.picking, TimeCtrlCB.filter(F.action == "cancel"))
async def default_times_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    user: Optional[User],
    settings: Settings,
) -> None:
    """Abort the preset flow and return to the main menu."""
    await state.clear()
    await callback.message.edit_text("Отменено.")
    await show_main_menu(callback.message, user, settings)
    await callback.answer()


# --- Settings: global weekend visibility ---------------------------------

def _settings_text(show_weekends: bool) -> str:
    """Settings screen body describing the current weekend-visibility state."""
    state = "Вкл" if show_weekends else "Выкл"
    return f"<b>Настройки</b>\n\nПоказывать выходные (сб/вс): {state}"


@router.message(StateFilter("*"), F.text == BTN_SETTINGS)
async def settings_start(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    # Cancel any in-progress flow; permission is still re-checked below.
    await state.clear()
    if not can_manage_slots(user, settings):
        await message.answer("Недостаточно прав.")
        return
    show = await get_show_weekends(session)
    await message.answer(_settings_text(show), reply_markup=settings_inline(show))


@router.callback_query(SettingsCB.filter(F.action == "toggle_weekends"))
async def settings_toggle_weekends(
    callback: CallbackQuery,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    """Flip the global weekend-visibility flag and re-render the settings view."""
    if not can_manage_slots(user, settings):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    new_value = not await get_show_weekends(session)
    await set_show_weekends(session, new_value)
    await callback.message.edit_text(
        _settings_text(new_value), reply_markup=settings_inline(new_value)
    )
    await callback.answer()


# --- Weekly slot editor --------------------------------------------------

async def _editor_week_view(
    session: AsyncSession, offset: int, settings: Settings
) -> tuple[str, InlineKeyboardMarkup]:
    """Build the ``(text, keyboard)`` for the slot editor at week ``offset``.

    Slots are grouped by LOCAL day (a 23:00-UTC Sunday slot belongs to its local
    day). Only VISIBLE weekdays are shown; booker ФИО is HTML-escaped (the bot
    sends ``parse_mode=HTML``). Days without slots still show a header + «нет
    слотов» so the teacher can add times there.

    Edge note: if weekends are turned off while weekend slots exist, those days
    are hidden here until weekends are re-enabled — acceptable by design.
    """
    start_utc, end_utc = get_week_bounds(offset)
    rows = await get_slots_in_range(session, start_utc, end_utc)
    visible = set(visible_weekdays(await get_show_weekends(session)))

    by_day: dict = {}
    for slot, _booking, booker in rows:
        by_day.setdefault(to_local(slot.starts_at).date(), []).append((slot, booker))

    monday = to_local(start_utc).date()
    label = format_week_label(start_utc, end_utc)
    days = [
        monday + timedelta(days=i)
        for i in range(7)
        if (monday + timedelta(days=i)).weekday() in visible
    ]

    lines = [f"<b>Ведение расписания {label}</b>", ""]
    for d in days:
        lines.append(f"<b>{format_day_short(d)}</b>")
        day_slots = by_day.get(d, [])
        if not day_slots:
            lines.append("нет слотов")
        else:
            for slot, booker in day_slots:
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
    show_next = offset < _EDITOR_MAX_OFFSET
    markup = week_editor_markup(days, offset, show_prev, show_next, label)
    return text, markup


@router.message(StateFilter("*"), F.text == BTN_MANAGE_SCHEDULE)
async def editor_start(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    """«Ведение расписания» — render the current week (cancels any in-progress flow)."""
    await state.clear()
    if not can_manage_slots(user, settings):
        await message.answer("Недостаточно прав.")
        return
    text, markup = await _editor_week_view(session, 0, settings)
    await message.answer(text, reply_markup=markup)


@router.callback_query(WeekNavCB.filter(F.mode == "edit"))
async def editor_navigate(
    callback: CallbackQuery,
    callback_data: WeekNavCB,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    """Re-render the editor for another week (clamped to ``[0, _EDITOR_MAX_OFFSET]``)."""
    if not can_manage_slots(user, settings):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    offset = max(0, min(callback_data.offset, _EDITOR_MAX_OFFSET))
    text, markup = await _editor_week_view(session, offset, settings)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(EditDayCB.filter())
async def editor_edit_day(
    callback: CallbackQuery,
    callback_data: EditDayCB,
    state: FSMContext,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    """Open the time grid for one day, pre-selected with that day's slot times."""
    if not can_manage_slots(user, settings):
        await state.clear()
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    try:
        local_date = date_cls.fromisoformat(callback_data.date)
    except ValueError:
        await callback.answer("Некорректная дата", show_alert=True)
        return
    offset = max(0, min(callback_data.offset, _EDITOR_MAX_OFFSET))

    rows = await get_slots_on_date(session, local_date)
    # Pre-select the times of the day's EXISTING slots (free and booked alike).
    existing_times = sorted({format_time(slot.starts_at) for slot, _b, _u in rows})
    await state.set_state(SlotEditor.picking)
    await state.update_data(
        mode="day",
        date=local_date.isoformat(),
        offset=offset,
        selected=existing_times,
    )
    await callback.message.edit_text(
        f"Слоты на {format_day_short(local_date)}. "
        "Отметьте нужные времена и нажмите «Готово».",
        reply_markup=_send_grid_markup(settings, existing_times),
    )
    await callback.answer()


@router.callback_query(SetWeekCB.filter())
async def editor_set_week(
    callback: CallbackQuery,
    callback_data: SetWeekCB,
    state: FSMContext,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    """Open the time grid pre-selected from the preset, to set the whole week."""
    if not can_manage_slots(user, settings):
        await state.clear()
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    offset = max(0, min(callback_data.offset, _EDITOR_MAX_OFFSET))
    preset = _load_preset(await get_setting(session, _DEFAULT_TIMES_KEY))
    start_utc, end_utc = get_week_bounds(offset)
    label = format_week_label(start_utc, end_utc)
    await state.set_state(SlotEditor.picking)
    await state.update_data(mode="week", offset=offset, selected=preset)
    await callback.message.edit_text(
        f"Задать времена на всю неделю ({label}). "
        "Отметьте нужные времена и нажмите «Готово».",
        reply_markup=_send_grid_markup(settings, preset),
    )
    await callback.answer()


# Cap on the number of bookings listed in the confirmation before collapsing the
# rest into «…и ещё N», so a huge week override never overflows the message.
_CONFIRM_LIST_CAP = 15


def _pending_offset(data: dict) -> int:
    """Clamped editor week offset from the pending FSM data."""
    return max(0, min(int(data.get("offset", 0)), _EDITOR_MAX_OFFSET))


async def _visible_week_days(
    session: AsyncSession, offset: int
) -> list[date_cls]:
    """Local dates of the target week's VISIBLE weekdays (weekend setting applied)."""
    visible = set(visible_weekdays(await get_show_weekends(session)))
    start_utc, _end = get_week_bounds(offset)
    monday = to_local(start_utc).date()
    return [
        monday + timedelta(days=i)
        for i in range(7)
        if (monday + timedelta(days=i)).weekday() in visible
    ]


async def _pending_cancellations(
    session: AsyncSession, data: dict
) -> list[SlotCancellation]:
    """Dry-run: which ACTIVE bookings the pending selection would cancel.

    Aggregated across all visible days for the whole-week mode. Read-only — used
    to decide whether a confirmation is needed and to render its list.
    """
    selected = sorted(set(data.get("selected", [])))
    cancellations: list[SlotCancellation] = []
    if data.get("mode", "day") == "week":
        for d in await _visible_week_days(session, _pending_offset(data)):
            diff = await compute_day_override(session, d, selected)
            cancellations.extend(diff.to_cancel)
    else:
        d = date_cls.fromisoformat(data["date"])
        diff = await compute_day_override(session, d, selected)
        cancellations.extend(diff.to_cancel)
    return cancellations


async def _run_override(
    session: AsyncSession, data: dict, actor_id: int, duration: int
) -> tuple[int, int, list[SlotCancellation]]:
    """Apply the pending override (day or whole-week). Returns aggregated
    ``(added, removed, cancelled)``; the caller notifies students after commit."""
    selected = sorted(set(data.get("selected", [])))
    if data.get("mode", "day") == "week":
        total_added = total_removed = 0
        cancelled: list[SlotCancellation] = []
        for d in await _visible_week_days(session, _pending_offset(data)):
            res = await apply_day_override(session, d, selected, actor_id, duration)
            total_added += res.added
            total_removed += res.removed
            cancelled.extend(res.cancelled)
        return total_added, total_removed, cancelled
    d = date_cls.fromisoformat(data["date"])
    res = await apply_day_override(session, d, selected, actor_id, duration)
    return res.added, res.removed, list(res.cancelled)


def _override_summary(data: dict, added: int, removed: int, cancelled: int) -> str:
    """Human summary of an applied override, tailored to day / week mode."""
    if data.get("mode", "day") == "week":
        summary = f"Задано на всю неделю: добавлено {added}, удалено {removed}"
    else:
        local_date = date_cls.fromisoformat(data["date"])
        summary = f"{format_day_short(local_date)}: добавлено {added}, удалено {removed}"
    if cancelled:
        summary += f", отменено броней {cancelled} (ученики уведомлены)"
    return summary + "."


def _confirm_text(mode: str, cancellations: list[SlotCancellation]) -> str:
    """Confirmation body listing the bookings that would be cancelled.

    Day mode lists ``HH:MM``; week mode lists the full ``DD.MM.YYYY HH:MM`` so the
    teacher sees which day each falls on. Names are HTML-escaped (parse_mode=HTML).
    Capped at ``_CONFIRM_LIST_CAP`` with a «…и ещё N» tail.
    """
    lines = ["Будут отменены брони:"]
    shown = cancellations[:_CONFIRM_LIST_CAP]
    for c in shown:
        when = format_time(c.slot_starts_at) if mode == "day" else format_dt(c.slot_starts_at)
        lines.append(f"• {when} — {escape(c.full_name)}")
    extra = len(cancellations) - len(shown)
    if extra > 0:
        lines.append(f"…и ещё {extra}")
    return "\n".join(lines)


async def _notify_cancelled(bot, cancellations: list[SlotCancellation]) -> None:
    """Notify each affected student that their booking was cancelled.

    Best-effort, AFTER commit: each send is isolated so a blocked user never stops
    the rest. No general broadcast — the slot is REMOVED (not freed for rebooking),
    so only the affected student is told, unlike the force-free flow.
    """
    for c in cancellations:
        try:
            await bot.send_message(
                c.tg_id,
                f"🔔 Ваша бронь на {format_dt(c.slot_starts_at)} была отменена преподавателем.",
            )
        except Exception:  # noqa: BLE001 - notification is best-effort
            logger.info(
                "Could not notify user %s about override cancellation", c.tg_id
            )


@router.callback_query(SlotEditor.picking, TimeCtrlCB.filter(F.action == "done"))
async def editor_grid_done(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    """Apply the grid selection as a day (or whole-week) override, then re-render.

    Both modes go through :func:`apply_day_override`: newly-selected free slots are
    created (skipping past/duplicate) and deselected FREE slots are deleted via the
    race-safe ``delete_free_slot``. If any deselected time has an ACTIVE booking,
    applying would CANCEL it — so this shows a confirmation FIRST (listing the
    affected bookings) and defers the apply to the confirm handler. With no booked
    time deselected, the override applies directly.
    """
    if not can_manage_slots(user, settings):
        await state.clear()
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    data = await state.get_data()
    offset = _pending_offset(data)
    cancellations = await _pending_cancellations(session, data)

    if cancellations:
        # Destructive: applying cancels other people's bookings -> confirm first.
        # The pending selection + context already live in FSM data; just switch
        # state (aiogram set_state keeps the data) and show the confirmation.
        await state.set_state(SlotEditor.confirming)
        await callback.message.edit_text(
            _confirm_text(data.get("mode", "day"), cancellations),
            reply_markup=override_confirm_markup(),
        )
        await callback.answer()
        return

    # No booking affected -> apply directly (create + delete-free only).
    duration = get_settings().default_slot_duration_min
    added, removed, _cancelled = await _run_override(
        session, data, callback.from_user.id, duration
    )
    await state.clear()
    summary = _override_summary(data, added, removed, 0)
    text, markup = await _editor_week_view(session, offset, settings)
    await callback.message.edit_text(f"{summary}\n\n{text}", reply_markup=markup)
    await callback.answer()


@router.callback_query(
    SlotEditor.confirming, SlotOverrideConfirmCB.filter(F.action == "confirm")
)
async def editor_confirm_override(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    """Apply the pending override INCLUDING the booking cancellations, then notify.

    Server-side ``can_manage_slots`` is re-checked here (a role change mid-flow
    cannot slip through stale callback data). Student notifications are sent AFTER
    the DB commit + after the teacher's own re-render, best-effort.
    """
    if not can_manage_slots(user, settings):
        await state.clear()
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    data = await state.get_data()
    offset = _pending_offset(data)
    duration = get_settings().default_slot_duration_min
    added, removed, cancelled = await _run_override(
        session, data, callback.from_user.id, duration
    )
    await state.clear()
    summary = _override_summary(data, added, removed, len(cancelled))
    text, markup = await _editor_week_view(session, offset, settings)
    await callback.message.edit_text(f"{summary}\n\n{text}", reply_markup=markup)
    await callback.answer("Готово.")

    # Best-effort, AFTER the DB commit: tell each affected student directly.
    await _notify_cancelled(callback.bot, cancelled)


@router.callback_query(
    SlotEditor.confirming, SlotOverrideConfirmCB.filter(F.action == "cancel")
)
async def editor_confirm_abort(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    """Discard the pending override without changes and return to the week editor."""
    if not can_manage_slots(user, settings):
        await state.clear()
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    data = await state.get_data()
    offset = _pending_offset(data)
    await state.clear()
    text, markup = await _editor_week_view(session, offset, settings)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(SlotEditor.picking, TimeCtrlCB.filter(F.action == "cancel"))
async def editor_grid_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    """Abort the grid without changes and return to the week editor."""
    if not can_manage_slots(user, settings):
        await state.clear()
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    data = await state.get_data()
    offset = max(0, min(int(data.get("offset", 0)), _EDITOR_MAX_OFFSET))
    await state.clear()
    text, markup = await _editor_week_view(session, offset, settings)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()
