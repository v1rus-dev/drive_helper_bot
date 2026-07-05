"""Dependency-free regression test for the ``delete_free_slot`` safety invariant.

The delete-free-slot guard is safety-critical: it must NEVER destroy a slot's
active booking (data loss / orphaned occupied slot), and must only remove a slot
that is genuinely free. This test pins that behaviour.

It uses only the stdlib (``asyncio``) plus the project's already-installed
SQLAlchemy / aiosqlite, against a throwaway temp-file SQLite DB with
``PRAGMA foreign_keys=ON`` (mirroring production). No pytest, no new deps.

Run from the project root:

    python -m tests.test_slot_deletion
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import timedelta

from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.db.models import (
    Base,
    Booking,
    BookingStatus,
    Slot,
    SlotStatus,
    User,
    UserRole,
)
from bot.db.repositories import delete_free_slot
from bot.utils import utcnow


def _make_engine(db_path: str):
    """Async engine on a temp SQLite file with foreign_keys enforcement on."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        # foreign_keys=ON so a slot delete requires its booking rows gone first
        # — exactly the constraint delete_free_slot has to satisfy.
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


async def _slot_count(session, slot_id: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(Slot).where(Slot.id == slot_id)
    )


async def _booking_count(session, slot_id: int, status: BookingStatus | None = None) -> int:
    stmt = select(func.count()).select_from(Booking).where(Booking.slot_id == slot_id)
    if status is not None:
        stmt = stmt.where(Booking.status == status)
    return await session.scalar(stmt)


async def _new_slot(session, status: SlotStatus) -> Slot:
    slot = Slot(
        starts_at=utcnow() + timedelta(days=1),
        duration_min=90,
        created_by=1,
        status=status,
    )
    session.add(slot)
    await session.commit()
    await session.refresh(slot)
    return slot


async def _new_booking(session, slot_id: int, status: BookingStatus) -> Booking:
    booking = Booking(slot_id=slot_id, user_id=1, status=status)
    session.add(booking)
    await session.commit()
    await session.refresh(booking)
    return booking


# --- Scenarios -----------------------------------------------------------

async def case_a_free_slot_no_booking(sessionmaker) -> None:
    """(a) FREE slot, no booking -> returns True and the slot is gone."""
    async with sessionmaker() as session:
        slot = await _new_slot(session, SlotStatus.free)
        result = await delete_free_slot(session, slot.id)
        assert result is True, f"expected True, got {result!r}"
        assert await _slot_count(session, slot.id) == 0, "slot should be deleted"


async def case_b_slot_with_active_booking(sessionmaker) -> None:
    """(b) slot with an ACTIVE booking -> returns False, slot AND booking survive.

    The slot is deliberately left with ``status='free'`` while carrying an active
    booking — the exact TOCTOU/orphan state the guard must defend against. This
    proves the NOT EXISTS clause (not merely the status check) blocks the delete,
    so a concurrently-inserted active booking can never be destroyed.
    """
    async with sessionmaker() as session:
        slot = await _new_slot(session, SlotStatus.free)
        booking = await _new_booking(session, slot.id, BookingStatus.active)
        # Capture ids as plain ints: delete_free_slot rolls back on the False
        # path, which expires the ORM instances (accessing slot.id/booking.id
        # afterwards would trigger a sync lazy-load -> MissingGreenlet).
        slot_id, booking_id = slot.id, booking.id
        result = await delete_free_slot(session, slot_id)
        assert result is False, f"expected False, got {result!r}"
        assert await _slot_count(session, slot_id) == 1, "slot must still exist"
        assert (
            await _booking_count(session, slot_id, BookingStatus.active) == 1
        ), "active booking must NOT be deleted"
        # And the specific booking row is intact.
        assert await session.get(Booking, booking_id) is not None


async def case_c_free_slot_only_cancelled_booking(sessionmaker) -> None:
    """(c) FREE slot with only a CANCELLED booking -> True, slot gone, row cleaned."""
    async with sessionmaker() as session:
        slot = await _new_slot(session, SlotStatus.free)
        await _new_booking(session, slot.id, BookingStatus.cancelled)
        result = await delete_free_slot(session, slot.id)
        assert result is True, f"expected True, got {result!r}"
        assert await _slot_count(session, slot.id) == 0, "slot should be deleted"
        assert (
            await _booking_count(session, slot.id) == 0
        ), "cancelled booking row should be cleaned up"


async def _run() -> int:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = _make_engine(db_path)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as session:
            session.add(
                User(tg_id=1, full_name="Тест", role=UserRole.student)
            )
            await session.commit()

        cases = [
            ("a: free slot, no booking -> deleted", case_a_free_slot_no_booking),
            ("b: active booking -> preserved", case_b_slot_with_active_booking),
            ("c: only cancelled booking -> deleted + cleaned", case_c_free_slot_only_cancelled_booking),
        ]
        failures = 0
        for name, coro in cases:
            try:
                await coro(sessionmaker)
                print(f"PASS  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL  {name}: {exc}")
            except Exception as exc:  # noqa: BLE001 — surface any error as a failure
                failures += 1
                print(f"ERROR {name}: {type(exc).__name__}: {exc}")
        return failures
    finally:
        await engine.dispose()
        os.unlink(db_path)


def main() -> None:
    failures = asyncio.run(_run())
    if failures:
        print(f"\n{failures} case(s) FAILED")
        sys.exit(1)
    print("\nALL PASS")


if __name__ == "__main__":
    main()
