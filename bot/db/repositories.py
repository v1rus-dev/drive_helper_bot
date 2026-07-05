"""Data-access functions (SQLAlchemy 2.0 style).

All functions take an :class:`AsyncSession`. Write helpers commit their own
transaction so callers get a consistent, atomic unit of work.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional, Sequence

from sqlalchemy import and_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Booking, BookingStatus, Slot, SlotStatus, User, UserRole
from bot.utils import local_to_utc, to_local, utcnow


# --- Users ---------------------------------------------------------------

async def get_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    """Return the user by Telegram id, or ``None``."""
    return await session.get(User, tg_id)


async def create_user(
    session: AsyncSession,
    tg_id: int,
    full_name: str,
    phone: str,
    role: UserRole = UserRole.student,
) -> User:
    """Insert a new user and return it."""
    user = User(tg_id=tg_id, full_name=full_name, phone=phone, role=role)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


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


async def update_user_phone(
    session: AsyncSession, tg_id: int, phone: str
) -> Optional[User]:
    """Update a user's phone number. Returns the updated user, or ``None``."""
    user = await session.get(User, tg_id)
    if user is None:
        return None
    user.phone = phone
    await session.commit()
    await session.refresh(user)
    return user


# --- Slots ---------------------------------------------------------------

async def get_slot(session: AsyncSession, slot_id: int) -> Optional[Slot]:
    """Return a slot by id, or ``None``."""
    return await session.get(Slot, slot_id)


async def get_upcoming_free_slots(
    session: AsyncSession, horizon_days: int = 60
) -> Sequence[Slot]:
    """Free slots starting within the next ``horizon_days``, ordered by start.

    The horizon caps how many distinct dates the picker can offer, keeping the
    inline date keyboard well under Telegram's ~100-button limit.
    """
    # TODO: paginate if horizon still exceeds ~100 dates
    now = utcnow()
    horizon = now + timedelta(days=horizon_days)
    stmt = (
        select(Slot)
        .where(
            Slot.status == SlotStatus.free,
            Slot.starts_at > now,
            Slot.starts_at <= horizon,
        )
        .order_by(Slot.starts_at)
    )
    return (await session.execute(stmt)).scalars().all()


async def get_free_slot_dates(session: AsyncSession) -> set[date]:
    """Set of local (school-TZ) calendar dates that have at least one free slot.

    Reuses :func:`get_upcoming_free_slots` (already capped at the 60-day horizon)
    and groups by the slot's *school-TZ* date — a slot at 23:00 UTC belongs to the
    next day in Europe/Minsk, so grouping must not be done on the naive-UTC value.
    """
    slots = await get_upcoming_free_slots(session)
    return {to_local(slot.starts_at).date() for slot in slots}


async def get_upcoming_slots(
    session: AsyncSession, horizon_days: int = 60
) -> Sequence[Slot]:
    """All slots (free *and* booked) starting within the next ``horizon_days``.

    Unlike :func:`get_upcoming_free_slots` this is not filtered by status — it
    backs the read-only schedule / staff overview, which show occupancy too.
    """
    now = utcnow()
    horizon = now + timedelta(days=horizon_days)
    stmt = (
        select(Slot)
        .where(Slot.starts_at > now, Slot.starts_at <= horizon)
        .order_by(Slot.starts_at)
    )
    return (await session.execute(stmt)).scalars().all()


async def get_all_slot_dates(session: AsyncSession, horizon_days: int = 60) -> set[date]:
    """Local (school-TZ) dates that have at least one slot of *any* status.

    Mirrors :func:`get_free_slot_dates` (same to_local grouping) but over all
    slots, so the read-only schedule calendar highlights every day with a slot.
    """
    slots = await get_upcoming_slots(session, horizon_days)
    return {to_local(slot.starts_at).date() for slot in slots}


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


async def get_all_slots_on_date(
    session: AsyncSession, local_date: date
) -> Sequence[Slot]:
    """Future slots of *any* status on the given local day, ordered by start.

    Backs the student read-only schedule — it deliberately returns only the
    slots (no booking / user join) so the caller cannot leak who booked a slot.
    """
    start_utc, end_utc = _local_day_bounds_utc(local_date)
    stmt = (
        select(Slot)
        .where(
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
