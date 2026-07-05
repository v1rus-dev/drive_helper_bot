"""Time-zone and datetime helpers.

Storage contract: all datetimes are stored as **naive UTC** in the DB. Display
and user input parsing happen in the school time zone (env ``TZ``). These
helpers are the single place that converts between the two so aware/naive
datetimes are never accidentally mixed.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from bot.config import get_settings


def _tz() -> ZoneInfo:
    """School (local) time zone from configuration."""
    return ZoneInfo(get_settings().tz)


def utcnow() -> datetime:
    """Current time as a naive UTC datetime (matches how values are stored)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_local(naive_utc_dt: datetime) -> datetime:
    """Convert a naive-UTC datetime to an aware local datetime for display."""
    return naive_utc_dt.replace(tzinfo=timezone.utc).astimezone(_tz())


def local_to_utc(naive_local_dt: datetime) -> datetime:
    """Convert a naive local datetime to a naive-UTC datetime for storage."""
    return (
        naive_local_dt.replace(tzinfo=_tz())
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )


def format_date(dt_utc: datetime) -> str:
    """Format a naive-UTC datetime as ``DD.MM.YYYY`` in local time."""
    return to_local(dt_utc).strftime("%d.%m.%Y")


def format_time(dt_utc: datetime) -> str:
    """Format a naive-UTC datetime as ``HH:MM`` in local time."""
    return to_local(dt_utc).strftime("%H:%M")


def format_dt(dt_utc: datetime) -> str:
    """Format a naive-UTC datetime as ``DD.MM.YYYY HH:MM`` in local time."""
    return to_local(dt_utc).strftime("%d.%m.%Y %H:%M")


def parse_local_date(value: str) -> date:
    """Parse a ``DD.MM.YYYY`` string into a :class:`datetime.date` (local).

    Raises :class:`ValueError` on malformed input.
    """
    return datetime.strptime(value.strip(), "%d.%m.%Y").date()


def parse_local_time(value: str) -> time:
    """Parse an ``HH:MM`` string into a :class:`datetime.time` (local).

    Raises :class:`ValueError` on malformed input.
    """
    return datetime.strptime(value.strip(), "%H:%M").time()


def combine_local_to_utc(local_date: date, local_time: time) -> datetime:
    """Combine a local date and time and convert to a naive-UTC datetime."""
    return local_to_utc(datetime.combine(local_date, local_time))


def humanize_offset(offset_min: int) -> str:
    """Human-readable Russian phrase for a reminder offset in minutes."""
    mapping = {
        30: "30 минут",
        60: "1 час",
        120: "2 часа",
        240: "4 часа",
        480: "8 часов",
        1440: "сутки",
    }
    return mapping.get(offset_min, f"{offset_min} минут")
