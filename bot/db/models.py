"""SQLAlchemy 2.0 ORM models and shared enums."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from bot.config import Settings, is_admin
from bot.utils import utcnow


class Base(DeclarativeBase):
    """Declarative base for all models."""


# NOTE: all enums are ``str`` enums so that ``member.name == member.value``.
# SQLAlchemy's native Enum type persists the enum *member name*; keeping
# name == value makes the stored string predictable ("active", "free", ...) and
# lets the partial unique index below match a literal reliably.
class UserRole(str, enum.Enum):
    student = "student"
    moderator = "moderator"
    admin = "admin"


class SlotStatus(str, enum.Enum):
    free = "free"
    booked = "booked"


class BookingStatus(str, enum.Enum):
    active = "active"
    cancelled = "cancelled"


class User(Base):
    __tablename__ = "users"

    # Telegram user id — supplied by Telegram, never autoincremented.
    tg_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    phone: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole), default=UserRole.student, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )


class Slot(Base):
    __tablename__ = "slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Naive UTC (see bot.utils storage contract).
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id"), nullable=False
    )
    status: Mapped[SlotStatus] = mapped_column(
        SAEnum(SlotStatus), default=SlotStatus.free, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    bookings: Mapped[list["Booking"]] = relationship(back_populates="slot")


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("slots.id"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.tg_id"), nullable=False
    )
    status: Mapped[BookingStatus] = mapped_column(
        SAEnum(BookingStatus), default=BookingStatus.active, nullable=False
    )
    # Minutes before start; NULL means the user chose not to be reminded.
    reminder_offset_min: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    slot: Mapped["Slot"] = relationship(back_populates="bookings")

    __table_args__ = (
        # At most one ACTIVE booking per slot. Partial unique index: the literal
        # 'active' matches the string SQLAlchemy stores for BookingStatus.active
        # (member name == value, see the enum note above).
        Index(
            "uq_active_booking_per_slot",
            "slot_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
        ),
    )


def effective_role(user: Optional[User], settings: Settings) -> Optional[UserRole]:
    """Effective role: env admins are admins regardless of the stored role."""
    if user is None:
        return None
    if is_admin(user.tg_id, settings):
        return UserRole.admin
    return user.role


def is_moderator_or_admin(user: Optional[User], settings: Settings) -> bool:
    """Whether the user may use moderator features (moderator or admin)."""
    return effective_role(user, settings) in (UserRole.moderator, UserRole.admin)
