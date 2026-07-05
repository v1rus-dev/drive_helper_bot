"""Booking orchestration: atomic capture + reminder scheduling decisions."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date as date_cls, datetime, timedelta
from html import escape
from typing import Iterable, Optional, Sequence

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Booking, SlotStatus, User
from bot.db.repositories import (
    capture_slot_and_book,
    cancel_booking as repo_cancel_booking,
    create_slots,
    delete_free_slot,
    get_all_users,
    get_slot,
    get_slots_on_date,
    set_booking_reminder,
)
from bot.services.reminders import remove_reminder, reminder_text, schedule_reminder
from bot.utils import combine_local_to_utc, format_dt, format_time, parse_local_time, utcnow

logger = logging.getLogger(__name__)

# Keep a strong reference to detached broadcast tasks so the event loop does not
# garbage-collect them mid-flight (see asyncio.create_task docs); each removes
# itself on completion.
_broadcast_tasks: set[asyncio.Task] = set()


async def book_slot(
    session: AsyncSession,
    slot_id: int,
    user: User,
) -> Optional[Booking]:
    """Atomically capture ``slot_id`` for ``user``.

    Returns the active booking (reminder offset NULL for now — chosen next),
    or ``None`` if the slot was already taken.
    """
    return await capture_slot_and_book(session, slot_id, user.tg_id, offset=None)


async def apply_reminder(
    session: AsyncSession,
    booking: Booking,
    offset: Optional[int],
    bot: Bot,
    chat_id: int,
) -> bool:
    """Persist the reminder preference and schedule it if still in the future.

    Returns ``True`` if a reminder job was scheduled, ``False`` otherwise
    (offset disabled, or the reminder time already passed).
    """
    await set_booking_reminder(session, booking.id, offset)
    if offset is None:
        remove_reminder(booking.id)
        return False

    slot = await get_slot(session, booking.slot_id)
    if slot is None:
        return False

    run_at = slot.starts_at - timedelta(minutes=offset)
    if run_at > utcnow():
        schedule_reminder(
            bot, booking.id, chat_id, run_at, reminder_text(slot.starts_at)
        )
        return True

    # Too soon to remind — booking still stands, just no job scheduled.
    remove_reminder(booking.id)
    return False


async def cancel_booking(
    session: AsyncSession,
    booking_id: int,
    expected_user_id: Optional[int] = None,
) -> Optional[Booking]:
    """Cancel a booking, free its slot and drop any scheduled reminder.

    ``expected_user_id`` is passed through as an ownership guard: the booking
    is only cancelled when it belongs to that user.
    """
    booking = await repo_cancel_booking(session, booking_id, expected_user_id)
    if booking is not None:
        remove_reminder(booking_id)
    return booking


# --- Slot-editor day/week override (create / delete-free / cancel-booked) ----

@dataclass(frozen=True)
class SlotCancellation:
    """One ACTIVE booking that a slot-editor override will cancel.

    Carries everything needed both to render the confirmation line and to notify
    the affected student AFTER commit — all resolved while the DB session is
    alive, so the notification loop never touches the session again.
    ``slot_starts_at`` is the slot's naive-UTC start (render via ``format_dt`` /
    ``format_time`` in school-TZ).
    """

    booking_id: int
    tg_id: int
    full_name: str
    slot_starts_at: datetime


@dataclass
class DayOverrideDiff:
    """What a day override WOULD do, without mutating anything (dry-run)."""

    to_create: list[datetime]  # naive-UTC starts to create as free slots
    to_delete_free_slot_ids: list[int]  # deselected free slots to remove
    to_cancel: list[SlotCancellation]  # deselected booked slots -> cancel booking


@dataclass
class OverrideResult:
    """Outcome of applying a day override."""

    added: int
    removed: int  # deselected FREE slots actually removed
    cancelled: list[SlotCancellation]  # bookings cancelled (for after-commit notify)


async def compute_day_override(
    session: AsyncSession, local_date: date_cls, selected_times: Sequence[str]
) -> DayOverrideDiff:
    """Diff a local day's existing slots against ``selected_times`` (no mutation).

    Reads the day fresh (respecting a booking made mid-flow) and classifies:

    * selected times with no slot yet and a FUTURE start -> to create;
    * deselected times whose slot is genuinely free -> to delete;
    * deselected times whose slot has an ACTIVE booking -> to cancel (the booker's
      tg_id + ФИО + slot start are captured here for the later notification).

    A deselected slot flagged ``booked`` but carrying NO active booking (an orphan
    race state) is left untouched — it is neither safely free-deletable nor has a
    booking to cancel. Backs BOTH the confirmation dry-run and :func:`apply_day_override`.
    """
    selected = set(selected_times)
    rows = await get_slots_on_date(session, local_date)
    # get_slots_on_date joins only ACTIVE bookings, so ``booking`` is non-None
    # exactly when the slot is actively booked.
    existing = {
        format_time(slot.starts_at): (slot, booking, user)
        for slot, booking, user in rows
    }

    now = utcnow()
    to_create: list[datetime] = []
    for t in sorted(selected - existing.keys()):
        dt_utc = combine_local_to_utc(local_date, parse_local_time(t))
        if dt_utc > now:  # never create dead, unbookable past slots
            to_create.append(dt_utc)

    to_delete: list[int] = []
    to_cancel: list[SlotCancellation] = []
    for t in sorted(existing.keys() - selected):
        slot, booking, user = existing[t]
        if booking is not None:
            to_cancel.append(
                SlotCancellation(
                    booking_id=booking.id,
                    tg_id=booking.user_id,
                    full_name=user.full_name if user is not None else "ученик",
                    slot_starts_at=slot.starts_at,
                )
            )
        elif slot.status == SlotStatus.free:
            to_delete.append(slot.id)
        # else: booked flag with no active booking (orphan race) -> leave untouched.
    return DayOverrideDiff(to_create, to_delete, to_cancel)


async def apply_day_override(
    session: AsyncSession,
    local_date: date_cls,
    selected_times: Sequence[str],
    created_by: int,
    duration_min: int,
) -> OverrideResult:
    """Make a local day's slots match ``selected_times`` exactly, CANCELLING
    bookings on deselected booked times.

    Shared by the per-day edit and the set-whole-week flows. Behaviour:

    * newly-selected FUTURE times with no slot are created as FREE slots
      (past/duplicate skipped by :func:`create_slots`);
    * deselected FREE times are removed race-safely via :func:`delete_free_slot`
      (a slot booked concurrently in the await window is NEVER destroyed);
    * deselected BOOKED times: the active booking is cancelled via the STAFF path
      (:func:`cancel_booking` with no ownership check — frees the slot AND removes
      its reminder job), then the now-free slot is removed via
      :func:`delete_free_slot` (the teacher deselected this time, so unlike a
      force-free the slot is NOT left available for rebooking).

    NOTE: this REPLACES the previous "preserve booked slots" behaviour — a booked
    slot whose time is deselected is now cancelled, not kept. Cancelled bookings
    are returned (tg_id + slot start) so the caller notifies each affected student
    AFTER commit; no notification/broadcast is sent from here.
    """
    diff = await compute_day_override(session, local_date, selected_times)

    added, _dup = await create_slots(
        session, diff.to_create, created_by=created_by, duration_min=duration_min
    )

    cancelled: list[SlotCancellation] = []
    for c in diff.to_cancel:
        # STAFF cancel (no expected_user_id): cancels regardless of owner, frees
        # the slot and drops the reminder job; commits inside the service.
        booking = await cancel_booking(session, c.booking_id)
        if booking is None:
            # Booking row vanished between the diff and now — nothing to remove.
            continue
        # The slot is now free -> remove it. Race-safe: if it was re-booked in the
        # await window, delete_free_slot refuses (0 rows) and the slot stays.
        await delete_free_slot(session, booking.slot_id)
        cancelled.append(c)

    removed = 0
    for slot_id in diff.to_delete_free_slot_ids:
        if await delete_free_slot(session, slot_id):
            removed += 1

    return OverrideResult(added=added, removed=removed, cancelled=cancelled)


async def _send_to_all(bot: Bot, recipients: Iterable[int], text: str) -> None:
    """Send ``text`` to each recipient, best-effort.

    Each send is isolated: a recipient who blocked the bot (or any other send
    error) is logged at info and skipped, so one failure never stops the rest.
    Touches only ``bot`` — never a DB session — so it is safe to run detached.
    """
    for tg_id in recipients:
        try:
            await bot.send_message(tg_id, text)
        except Exception:  # noqa: BLE001 - a blocked user must not break the broadcast
            logger.info("Could not broadcast cancellation to user %s", tg_id)


async def broadcast_cancellation(
    bot: Bot,
    session: AsyncSession,
    *,
    actor_is_staff: bool,
    booked_name: str,
    slot_dt: datetime,
    exclude_tg_ids: Iterable[int],
) -> Optional[asyncio.Task]:
    """Notify every registered user that a cancellation freed up a slot.

    Best-effort and NON-BLOCKING. The recipient tg_ids and the final message
    text are resolved HERE, synchronously, while ``session`` is still valid; the
    per-user sends then run in a detached fire-and-forget task that only calls
    ``bot.send_message`` and NEVER touches ``session`` again — so it may safely
    outlive the handler and its request-scoped session.

    Call it AFTER the DB commit that freed the slot (and after the actor got
    their own confirmation), so the "слот снова свободен" claim is already true.

    ``exclude_tg_ids`` are dropped from the recipient list — always the actor who
    performed the cancellation (they already know), plus, on a staff force-free,
    the affected student (who gets a separate direct notice, so the third-person
    broadcast would be redundant). The name is HTML-escaped (the bot sends
    ``parse_mode=HTML``). ``slot_dt`` is the freed slot's naive-UTC start, rendered
    in school-TZ via :func:`format_dt`.

    Returns the spawned task (or ``None`` when there is no one to notify) so
    callers/tests can await completion; handlers ignore it (fire-and-forget).

    Scale note: sequential sends are fine at this scale (tens–low hundreds of
    users). At larger scale this should be throttled / queued to respect
    Telegram's ~30 msg/s limit — intentionally NOT throttled here.
    """
    when = format_dt(slot_dt)
    safe_name = escape(booked_name)
    if actor_is_staff:
        text = f"🔔 Запись {safe_name} на {when} отменена. Слот снова свободен."
    else:
        text = f"🔔 {safe_name} отменил(а) запись на {when}. Слот снова свободен."

    # Resolve recipients NOW, while the session is alive; drop the excluded ids.
    excluded = set(exclude_tg_ids)
    recipients = [
        u.tg_id for u in await get_all_users(session) if u.tg_id not in excluded
    ]
    if not recipients:
        return None

    # Fire-and-forget: the send loop touches no DB session, only the bot.
    task = asyncio.create_task(_send_to_all(bot, recipients, text))
    _broadcast_tasks.add(task)
    task.add_done_callback(_broadcast_tasks.discard)
    return task
