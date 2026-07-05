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


class SlotOverrideConfirmCB(CallbackData, prefix="ovcfm"):
    """Teacher confirms/aborts a slot-editor override that would cancel bookings.

    ``action`` is ``"confirm"`` / ``"cancel"``. Callback-driven only (no text
    input), so it needs no MENU_TEXTS guard; ``can_manage_slots`` is re-checked
    server-side on both actions. The pending selection + context ride in FSM data.
    """

    action: str


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


class UsersListCB(CallbackData, prefix="ulist"):
    """Admin «Пользователи»: re-render the full user list (no payload).

    Used by the «‹ Назад к списку» button. Admin-only + server-re-checked.
    """


class UserCardCB(CallbackData, prefix="ucard"):
    """Admin «Пользователи»: open one user's card.

    ``tg_id`` is server-re-checked on every use and is never shown to or typed
    by users — it only rides in admin-only callback data.
    """

    tg_id: int


class UserRoleCB(CallbackData, prefix="urole"):
    """Admin «Пользователи»: set a target user's role from their card.

    ``role`` is the target stored role (``teacher`` / ``student``); an env-admin
    is never a valid target (re-checked server-side). ``tg_id`` rides only in
    admin-only, server-re-checked callback data.
    """

    tg_id: int
    role: str


class UserDeleteCB(CallbackData, prefix="udel"):
    """Admin «Пользователи»: request deletion of a user (shows confirmation)."""

    tg_id: int


class UserDeleteConfirmCB(CallbackData, prefix="udelc"):
    """Admin «Пользователи»: confirm deletion of a user (irreversible)."""

    tg_id: int
