"""Time-zone and datetime helpers.

Storage contract: all datetimes are stored as **naive UTC** in the DB. Display
and user input parsing happen in the school time zone (env ``TZ``). These
helpers are the single place that converts between the two so aware/naive
datetimes are never accidentally mixed.
"""

from __future__ import annotations

import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from bot.config import get_settings

# Upper bound on a stored full name (registration + profile editing share it).
MAX_NAME_LEN = 100

# Russian weekday names indexed by ``date.weekday()`` (Monday == 0).
RU_WEEKDAYS_FULL: tuple[str, ...] = (
    "Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье",
)
RU_WEEKDAYS_SHORT: tuple[str, ...] = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


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


def get_week_bounds(offset: int) -> tuple[datetime, datetime]:
    """Return the ``[start, end)`` naive-UTC bounds of an ISO week.

    Weeks are Monday 00:00 .. next Monday 00:00 computed in the *school* time
    zone, then converted to UTC for querying naive-UTC slot ``starts_at`` values.
    ``offset`` counts weeks from the current one (0 = current week, 1 = next, …);
    a slot at 23:00 UTC Sunday may be Monday next week locally, so the boundary
    must be taken on the local Monday, never on the naive-UTC value.
    """
    today_local = to_local(utcnow()).date()
    # weekday(): Monday == 0, so subtracting it lands on this week's Monday.
    monday = today_local - timedelta(days=today_local.weekday()) + timedelta(weeks=offset)
    start_local = datetime.combine(monday, time.min)
    start_utc = local_to_utc(start_local)
    end_utc = local_to_utc(start_local + timedelta(days=7))
    return start_utc, end_utc


def format_week_label(start_utc: datetime, end_utc: datetime) -> str:
    """Human label for a week, e.g. ``15.07–21.07`` (Monday .. Sunday, local).

    ``end_utc`` is the exclusive next-Monday boundary, so the displayed last day
    is ``end_utc`` minus one day in local time.
    """
    start_local = to_local(start_utc).date()
    last_local = to_local(end_utc).date() - timedelta(days=1)
    return f"{start_local.strftime('%d.%m')}–{last_local.strftime('%d.%m')}"


def format_day_full(local_date: date) -> str:
    """Localized day header, e.g. ``Понедельник, 15.07``."""
    return f"{RU_WEEKDAYS_FULL[local_date.weekday()]}, {local_date.strftime('%d.%m')}"


def format_day_short(local_date: date) -> str:
    """Localized short day label for buttons, e.g. ``Пн 15.07``."""
    return f"{RU_WEEKDAYS_SHORT[local_date.weekday()]} {local_date.strftime('%d.%m')}"


def visible_weekdays(show_weekends: bool) -> list[int]:
    """Python ``date.weekday()`` ints that are visible under the weekend setting.

    ``[0..4]`` (Mon–Fri) when weekends are hidden, ``[0..6]`` (Mon–Sun) when
    shown. Used everywhere a week's days are enumerated (slot editor, booking,
    schedule) so a single toggle governs weekend visibility across the bot.
    """
    return list(range(7)) if show_weekends else list(range(5))


def clean_full_name(raw: Optional[str]) -> Optional[str]:
    """Sanitize a user-entered full name for storage and HTML display.

    Removes Unicode control characters (they can break the HTML-parse-mode
    messages the bot sends) and trims surrounding whitespace. Returns the
    cleaned name, or ``None`` when it is empty or longer than ``MAX_NAME_LEN``.
    """
    if raw is None:
        return None
    # Drop control chars (Unicode category "C*"); keep ordinary spaces.
    cleaned = "".join(ch for ch in raw if not unicodedata.category(ch).startswith("C"))
    cleaned = cleaned.strip()
    if not cleaned or len(cleaned) > MAX_NAME_LEN:
        return None
    return cleaned


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
