"""CallbackData factories (aiogram 3.x) with unique prefixes."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class DateCB(CallbackData, prefix="date"):
    """A chosen day in the booking flow, encoded as ``YYYY-MM-DD`` (local date)."""

    value: str


class WeekNavCB(CallbackData, prefix="wk_nav"):
    """Week navigation for the weekly schedule / booking views.

    ``offset`` counts weeks from the current one (clamped ``>= 0`` server-side).
    ``mode`` is ``"schedule"`` / ``"book"`` / ``"edit"`` so the three views'
    arrows never collide even though they share this factory.
    """

    offset: int
    mode: str


class ProfileCB(CallbackData, prefix="profile"):
    """Profile edit action. ``action`` is ``"name"`` (phone was removed)."""

    action: str


class NoopCB(CallbackData, prefix="noop"):
    """No-op tap (a non-tappable label button); the handler only clears the spinner."""


class SlotCB(CallbackData, prefix="slot"):
    """A specific free slot chosen by the student."""

    slot_id: int


class ReminderCB(CallbackData, prefix="rem"):
    """Reminder preference. ``offset == -1`` means "no reminder" (stored NULL)."""

    booking_id: int
    offset: int


class BookingActionCB(CallbackData, prefix="bk"):
    """Action on one of the student's active bookings.

    ``action`` is one of ``"cancel"`` / ``"resched"``.
    """

    action: str
    booking_id: int


# ``sep="@"`` because the payload value ``t`` is an ``HH:MM`` time containing a
# colon, which is the default CallbackData field separator — using "@" keeps the
# time intact on unpack.
class TimeToggleCB(CallbackData, prefix="tt", sep="@"):
    """Toggle a candidate start-time in the button-based time picker."""

    t: str


class TimeCtrlCB(CallbackData, prefix="tc"):
    """Time-picker control button. ``action`` is ``done`` / ``clear`` / ``cancel``."""

    action: str


class EditDayCB(CallbackData, prefix="eday"):
    """Teacher taps a day in the weekly slot editor to edit its times.

    ``date`` is the ISO ``YYYY-MM-DD`` local date (no colons, so the default
    separator is safe); ``offset`` is the editor's current week offset so the
    per-day flow can re-render the same week when it returns.
    """

    date: str
    offset: int


class SetWeekCB(CallbackData, prefix="setwk"):
    """Teacher taps «Задать времена на всю неделю» in the slot editor.

    ``offset`` is the editor's current week offset (the week to apply to).
    """

    offset: int


class SettingsCB(CallbackData, prefix="setg"):
    """Teacher/admin settings action. ``action`` is ``"toggle_weekends"``."""

    action: str


class ManageDayCB(CallbackData, prefix="mday"):
    """Staff «Управление записями»: open a day's slots from the week view.

    ``date`` is the ISO ``YYYY-MM-DD`` local date (no colons, so the default
    separator is safe); ``offset`` is the manage view's current week offset so
    the flow can re-render the same week / day when it returns. Doubles as the
    «Назад» target from the slot-action screens. Staff-only + re-checked.
    """

    date: str
    offset: int


class SlotManageCB(CallbackData, prefix="mslot"):
    """Staff «Управление записями»: tap one slot in a day to act on it.

    ``offset`` rides along so the day/week can be re-rendered afterwards. The
    ``slot_id`` is only ever in staff-only, server-re-checked callback data and
    is never shown to users.
    """

    slot_id: int
    offset: int


class ForceFreeCB(CallbackData, prefix="mfree"):
    """Staff confirms freeing a booked slot («Освободить»).

    ``booking_id`` rides only in staff-only, server-re-checked callback data
    (never shown to users). ``offset`` re-renders the day afterwards.
    """

    booking_id: int
    offset: int


class ManageBookCB(CallbackData, prefix="mbook"):
    """Staff picks a user to manually book onto a free slot.

    ``slot_id`` + target ``tg_id`` ride only in staff-only, server-re-checked
    callback data and are never shown to or typed by users. ``offset``
    re-renders the day afterwards.
    """

    slot_id: int
    tg_id: int
    offset: int


class UserSelectCB(CallbackData, prefix="usel"):
    """Admin picks a user from an inline list to change their role.

    ``action`` is ``assign`` / ``remove``. ``tg_id`` is server-re-checked and is
    never shown to or typed by users — it only rides in admin-only callback data.
    """

    action: str
    tg_id: int
