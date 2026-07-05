"""Data-access functions (SQLAlchemy 2.0 style).

All functions take an :class:`AsyncSession`. Write helpers commit their own
transaction so callers get a consistent, atomic unit of work.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional, Sequence

from sqlalchemy import and_, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import (
    AppSetting,
    Booking,
    BookingStatus,
    Slot,
    SlotStatus,
    User,
    UserRole,
)
from bot.utils import local_to_utc, utcnow


# --- Users ---------------------------------------------------------------

async def get_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    """Return the user by Telegram id, or ``None``."""
    return await session.get(User, tg_id)


async def create_user(
    session: AsyncSession,
    tg_id: int,
    full_name: str,
    role: UserRole = UserRole.student,
) -> User:
    """Insert a new user and return it."""
    user = User(tg_id=tg_id, full_name=full_name, role=role)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def get_users_by_role(
    session: AsyncSession, role: UserRole
) -> Sequence[User]:
    """Users with the given stored role, ordered by ФИО (case-insensitive)."""
    stmt = (
        select(User)
        .where(User.role == role)
        .order_by(User.full_name.collate("NOCASE"))
    )
    return (await session.execute(stmt)).scalars().all()


async def get_all_users(session: AsyncSession) -> Sequence[User]:
    """All registered users, ordered by ФИО (case-insensitive).

    Backs the staff «Управление записями» manual-booking user picker.
    """
    stmt = select(User).order_by(User.full_name.collate("NOCASE"))
    return (await session.execute(stmt)).scalars().all()


async def set_role(session: AsyncSession, tg_id: int, role: UserRole) -> Optional[User]:
    """Set a user's role. Returns the updated user, or ``None`` if not found."""
    user = await session.get(User, tg_id)
    if user is None:
        return None
    user.role = role
    await session.commit()
    await session.refresh(user)
    return user


async def update_user_name(
    session: AsyncSession, tg_id: int, full_name: str
) -> Optional[User]:
    """Update a user's full name. Returns the updated user, or ``None``."""
    user = await session.get(User, tg_id)
    if user is None:
        return None
    user.full_name = full_name
    await session.commit()
    await session.refresh(user)
    return user


async def delete_user(
    session: AsyncSession, tg_id: int, reassign_to: int
) -> list[int]:
    """Delete a user, cascading safely, in ONE transaction.

    ``reassign_to`` is the acting admin's tg_id: slots the deleted user created
    are reassigned to them instead of being NULL-ed. This keeps ``created_by``
    a valid non-null FK, so the live production DB (whose ``slots.created_by`` is
    ``NOT NULL``) needs no schema migration — ``create_all`` cannot ALTER it.

    Returns the ids of the user's ACTIVE bookings so the caller can drop their
    reminder jobs AFTER commit (the scheduler is never touched from here).

    Order is chosen to keep ``PRAGMA foreign_keys=ON`` satisfied and to leave no
    orphaned occupied slot behind:

    1. Free every slot the user holds via an ACTIVE booking (so it is bookable
       again) and collect those booking ids.
    2. Delete ALL of the user's booking rows (active + cancelled) — required
       before the user row can go, since ``bookings.user_id`` FKs ``users.tg_id``.
    3. Reassign ``slots.created_by`` to ``reassign_to`` for slots this user
       created (keep the slots; hand them to the acting admin — a valid non-null
       FK).
    4. Delete the user row.

    Steps 1–2 guarantee no slot stays ``booked`` with a deleted booker, and
    steps 2–3 clear both FKs into ``users`` before step 4, so the delete never
    violates a foreign key.
    """
    active = (
        await session.execute(
            select(Booking).where(
                Booking.user_id == tg_id,
                Booking.status == BookingStatus.active,
            )
        )
    ).scalars().all()
    active_booking_ids = [b.id for b in active]

    # 1. Free the slots held by the user's active bookings.
    for booking in active:
        await session.execute(
            update(Slot)
            .where(Slot.id == booking.slot_id)
            .values(status=SlotStatus.free)
        )

    # 2. Remove every booking row for this user (satisfies the FK below).
    await session.execute(delete(Booking).where(Booking.user_id == tg_id))

    # 3. Reassign slots this user created to the acting admin. Using a real
    # tg_id (not NULL) keeps the NOT NULL created_by FK valid on the live DB,
    # so no schema migration is needed.
    await session.execute(
        update(Slot).where(Slot.created_by == tg_id).values(created_by=reassign_to)
    )

    # 4. Delete the user.
    await session.execute(delete(User).where(User.tg_id == tg_id))

    await session.commit()
    return active_booking_ids


# --- Slots ---------------------------------------------------------------

async def get_slot(session: AsyncSession, slot_id: int) -> Optional[Slot]:
    """Return a slot by id, or ``None``."""
    return await session.get(Slot, slot_id)


async def get_slots_in_range(
    session: AsyncSession, start_utc: datetime, end_utc: datetime
) -> list[tuple[Slot, Optional[Booking], Optional[User]]]:
    """All slots (any status) in ``[start, end)`` with active booking + booker.

    Backs the weekly schedule view. The active booking and its user's ФИО are
    eagerly joined so the caller renders each row without a per-row query. The
    range is a naive-UTC half-open interval (week bounds are computed in the
    school TZ, then converted — see :func:`bot.utils.get_week_bounds`).
    """
    stmt = (
        select(Slot, Booking, User)
        .outerjoin(
            Booking,
            and_(
                Booking.slot_id == Slot.id,
                Booking.status == BookingStatus.active,
            ),
        )
        .outerjoin(User, User.tg_id == Booking.user_id)
        .where(Slot.starts_at >= start_utc, Slot.starts_at < end_utc)
        .order_by(Slot.starts_at)
    )
    rows = (await session.execute(stmt)).all()
    return [(row[0], row[1], row[2]) for row in rows]


async def get_free_slots_in_range(
    session: AsyncSession, start_utc: datetime, end_utc: datetime
) -> Sequence[Slot]:
    """Free, still-upcoming slots in ``[start, end)``, ordered by start.

    Backs the booking day picker: only free slots whose start is still in the
    future are offered.
    """
    stmt = (
        select(Slot)
        .where(
            Slot.status == SlotStatus.free,
            Slot.starts_at > utcnow(),
            Slot.starts_at >= start_utc,
            Slot.starts_at < end_utc,
        )
        .order_by(Slot.starts_at)
    )
    return (await session.execute(stmt)).scalars().all()


async def has_slots_after(session: AsyncSession, end_utc: datetime) -> bool:
    """Whether any slot (any status) starts at/after ``end_utc``.

    Decides whether the weekly schedule's next-week ``›`` button is shown.
    """
    slot_id = await session.scalar(
        select(Slot.id).where(Slot.starts_at >= end_utc).limit(1)
    )
    return slot_id is not None


async def has_free_slots_after(session: AsyncSession, end_utc: datetime) -> bool:
    """Whether any free, still-upcoming slot starts at/after ``end_utc``.

    Decides whether the booking view's next-week ``›`` button is shown.
    """
    slot_id = await session.scalar(
        select(Slot.id)
        .where(
            Slot.status == SlotStatus.free,
            Slot.starts_at > utcnow(),
            Slot.starts_at >= end_utc,
        )
        .limit(1)
    )
    return slot_id is not None


def _local_day_bounds_utc(local_date: date) -> tuple[datetime, datetime]:
    """Return the [start, end) naive-UTC bounds of a local calendar day."""
    day_start_local = datetime.combine(local_date, time.min)
    start_utc = local_to_utc(day_start_local)
    end_utc = local_to_utc(day_start_local + timedelta(days=1))
    return start_utc, end_utc


async def get_free_slots_on_date(
    session: AsyncSession, local_date: date
) -> Sequence[Slot]:
    """Future free slots that fall on the given local calendar day."""
    start_utc, end_utc = _local_day_bounds_utc(local_date)
    stmt = (
        select(Slot)
        .where(
            Slot.status == SlotStatus.free,
            Slot.starts_at > utcnow(),
            Slot.starts_at >= start_utc,
            Slot.starts_at < end_utc,
        )
        .order_by(Slot.starts_at)
    )
    return (await session.execute(stmt)).scalars().all()


async def get_slots_on_date(
    session: AsyncSession, local_date: date
) -> list[tuple[Slot, Optional[Booking], Optional[User]]]:
    """All slots on a local day with their active booking + student, if any."""
    start_utc, end_utc = _local_day_bounds_utc(local_date)
    stmt = (
        select(Slot, Booking, User)
        .outerjoin(
            Booking,
            and_(
                Booking.slot_id == Slot.id,
                Booking.status == BookingStatus.active,
            ),
        )
        .outerjoin(User, User.tg_id == Booking.user_id)
        .where(Slot.starts_at >= start_utc, Slot.starts_at < end_utc)
        .order_by(Slot.starts_at)
    )
    rows = (await session.execute(stmt)).all()
    return [(row[0], row[1], row[2]) for row in rows]


async def create_slots(
    session: AsyncSession,
    starts_at_list_utc: Sequence[datetime],
    created_by: int,
    duration_min: int,
) -> tuple[int, int]:
    """Create free slots, skipping start times that already exist.

    Returns ``(created_count, skipped_duplicate_count)``.
    """
    created = 0
    skipped = 0
    for starts_at in starts_at_list_utc:
        exists = await session.scalar(
            select(Slot.id).where(Slot.starts_at == starts_at)
        )
        if exists is not None:
            skipped += 1
            continue
        session.add(
            Slot(
                starts_at=starts_at,
                duration_min=duration_min,
                created_by=created_by,
                status=SlotStatus.free,
            )
        )
        created += 1
    await session.commit()
    return created, skipped


async def free_slot(session: AsyncSession, slot_id: int) -> None:
    """Mark a slot as free (used when a booking is cancelled)."""
    await session.execute(
        update(Slot).where(Slot.id == slot_id).values(status=SlotStatus.free)
    )
    await session.commit()


async def delete_free_slot(session: AsyncSession, slot_id: int) -> bool:
    """Delete a slot only if it is genuinely free with no active booking.

    Returns ``True`` if the slot was deleted, ``False`` otherwise.

    TOCTOU safety: an ACTIVE booking row is NEVER deleted, under any timing.
    Only *cancelled* booking rows are removed (they carry no user-facing state —
    only active bookings are ever shown or reminded — and exist solely to satisfy
    the FK once the now-free slot is deleted, since ``PRAGMA foreign_keys=ON``).
    The slot delete is a single guarded statement whose ``WHERE`` re-checks
    ``status='free'`` AND ``NOT EXISTS`` an active booking in the *same* SQL
    statement, so a booking inserted concurrently in the await window cannot be
    lost: the guarded delete simply affects 0 rows and the whole unit of work is
    rolled back (undoing the cancelled-booking cleanup as well).
    """
    # Remove only cancelled booking rows referencing this slot; active rows are
    # left untouched so a concurrent booking is never destroyed.
    await session.execute(
        delete(Booking).where(
            Booking.slot_id == slot_id,
            Booking.status == BookingStatus.cancelled,
        )
    )
    # Guarded, atomic slot delete. The correlated NOT EXISTS is evaluated in the
    # same statement as the status check, so an active booking (pre-existing or
    # concurrently inserted) blocks the delete instead of orphaning the slot.
    active_exists = (
        select(Booking.id)
        .where(
            Booking.slot_id == slot_id,
            Booking.status == BookingStatus.active,
        )
        .exists()
    )
    result = await session.execute(
        delete(Slot).where(
            Slot.id == slot_id,
            Slot.status == SlotStatus.free,
            ~active_exists,
        )
    )
    if result.rowcount > 0:
        await session.commit()
        return True
    # Slot was booked / taken / gone -> roll back, undoing the cancelled cleanup.
    await session.rollback()
    return False


# NOTE: the day/week slot override (create free slots, delete deselected free
# slots, and CANCEL bookings on deselected booked slots) lives in
# ``bot.services.booking_service`` — it orchestrates repo writes AND the staff
# cancel path (which drops reminder jobs), so it belongs in the service layer,
# not here.


# --- Bookings ------------------------------------------------------------

async def capture_slot_and_book(
    session: AsyncSession,
    slot_id: int,
    user_id: int,
    offset: Optional[int],
) -> Optional[Booking]:
    """Atomically capture a free slot and create an active booking.

    The capture is a single guarded UPDATE — never a read-then-write — so two
    concurrent bookings cannot both succeed. Returns the new booking, or
    ``None`` if the slot was already taken. The partial unique index is a
    second line of defense: an :class:`IntegrityError` is treated as "taken".
    """
    result = await session.execute(
        update(Slot)
        .where(Slot.id == slot_id, Slot.status == SlotStatus.free)
        .values(status=SlotStatus.booked)
    )
    if result.rowcount == 0:
        # Slot did not exist as free -> already taken.
        await session.rollback()
        return None

    booking = Booking(
        slot_id=slot_id,
        user_id=user_id,
        status=BookingStatus.active,
        reminder_offset_min=offset,
    )
    session.add(booking)
    try:
        await session.commit()
    except IntegrityError:
        # Partial unique index tripped -> another active booking exists.
        await session.rollback()
        return None
    await session.refresh(booking)
    return booking


async def get_booking(session: AsyncSession, booking_id: int) -> Optional[Booking]:
    """Return a booking by id, or ``None``."""
    return await session.get(Booking, booking_id)


async def get_active_booking_for_slot(
    session: AsyncSession, slot_id: int
) -> Optional[tuple[Booking, Optional[User]]]:
    """Active booking (+ its booker) for a slot, or ``None`` if the slot is free.

    Backs the staff «Управление записями» slot-action screen: it needs the
    booking id (to force-free) and the booker's ФИО (to show) in one query.
    """
    stmt = (
        select(Booking, User)
        .outerjoin(User, User.tg_id == Booking.user_id)
        .where(Booking.slot_id == slot_id, Booking.status == BookingStatus.active)
    )
    row = (await session.execute(stmt)).first()
    return (row[0], row[1]) if row is not None else None


async def get_active_bookings_for_user(
    session: AsyncSession, tg_id: int
) -> list[tuple[Booking, Slot]]:
    """Active bookings for a user joined with their slots, ordered by start."""
    stmt = (
        select(Booking, Slot)
        .join(Slot, Slot.id == Booking.slot_id)
        .where(Booking.user_id == tg_id, Booking.status == BookingStatus.active)
        .order_by(Slot.starts_at)
    )
    rows = (await session.execute(stmt)).all()
    return [(row[0], row[1]) for row in rows]


async def cancel_booking(
    session: AsyncSession,
    booking_id: int,
    expected_user_id: Optional[int] = None,
) -> Optional[Booking]:
    """Cancel a booking and free its slot. Returns the booking, or ``None``.

    Idempotent: an already-cancelled booking is returned unchanged. When
    ``expected_user_id`` is given, the booking is only touched if it belongs to
    that user (ownership guard / defense in depth against IDOR); a mismatch
    returns ``None`` and mutates nothing.
    """
    booking = await session.get(Booking, booking_id)
    if booking is None:
        return None
    if expected_user_id is not None and booking.user_id != expected_user_id:
        return None
    if booking.status == BookingStatus.active:
        booking.status = BookingStatus.cancelled
        booking.cancelled_at = utcnow()
        await session.execute(
            update(Slot)
            .where(Slot.id == booking.slot_id)
            .values(status=SlotStatus.free)
        )
        await session.commit()
        await session.refresh(booking)
    return booking


async def set_booking_reminder(
    session: AsyncSession, booking_id: int, offset: Optional[int]
) -> Optional[Booking]:
    """Set a booking's reminder offset (``None`` disables it)."""
    booking = await session.get(Booking, booking_id)
    if booking is None:
        return None
    booking.reminder_offset_min = offset
    await session.commit()
    await session.refresh(booking)
    return booking


async def get_active_bookings_with_reminders_future(
    session: AsyncSession,
) -> list[tuple[Booking, Slot]]:
    """Active bookings whose reminder time is still in the future (for restore)."""
    stmt = (
        select(Booking, Slot)
        .join(Slot, Slot.id == Booking.slot_id)
        .where(
            Booking.status == BookingStatus.active,
            Booking.reminder_offset_min.is_not(None),
        )
        .order_by(Slot.starts_at)
    )
    rows = (await session.execute(stmt)).all()
    now = utcnow()
    result: list[tuple[Booking, Slot]] = []
    for booking, slot in rows:
        run_at = slot.starts_at - timedelta(minutes=booking.reminder_offset_min or 0)
        if run_at > now:
            result.append((booking, slot))
    return result


# --- App settings (key-value) --------------------------------------------

async def get_setting(session: AsyncSession, key: str) -> Optional[str]:
    """Return the stored value for ``key``, or ``None`` if unset."""
    setting = await session.get(AppSetting, key)
    return setting.value if setting is not None else None


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    """Upsert a single app setting value."""
    setting = await session.get(AppSetting, key)
    if setting is None:
        session.add(AppSetting(key=key, value=value))
    else:
        setting.value = value
    await session.commit()


# Key under which the global weekend-visibility flag is stored ("1"/"0").
_SHOW_WEEKENDS_KEY = "show_weekends"


async def get_show_weekends(session: AsyncSession) -> bool:
    """Whether Sat/Sun are shown across the bot. Defaults to ``False`` (hidden)."""
    return await get_setting(session, _SHOW_WEEKENDS_KEY) == "1"


async def set_show_weekends(session: AsyncSession, value: bool) -> None:
    """Persist the global weekend-visibility flag as ``"1"`` / ``"0"``."""
    await set_setting(session, _SHOW_WEEKENDS_KEY, "1" if value else "0")
