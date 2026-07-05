"""Keyboard builders and menu label constants (Russian UI)."""

from __future__ import annotations

from datetime import date
from typing import Iterable, Optional

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from bot.callbacks import (
    BookingActionCB,
    DateCB,
    EditDayCB,
    ForceFreeCB,
    ManageBookCB,
    ManageDayCB,
    NoopCB,
    ProfileCB,
    ReminderCB,
    SettingsCB,
    SetWeekCB,
    SlotCB,
    SlotManageCB,
    SlotOverrideConfirmCB,
    TimeCtrlCB,
    TimeToggleCB,
    UserCardCB,
    UserDeleteCB,
    UserDeleteConfirmCB,
    UserRoleCB,
    UsersListCB,
    WeekNavCB,
)
from bot.db.models import Booking, Slot, SlotStatus, User, UserRole
from bot.utils import format_date, format_day_short, format_time

# --- Main-menu button labels (also used as text filters in handlers) ------
BTN_BOOK = "Записаться"
BTN_MY = "Мои записи"
BTN_HELP = "Помощь"
BTN_PROFILE = "Мой профиль"
BTN_SCHEDULE = "Расписание"
BTN_MANAGE_SCHEDULE = "Ведение расписания"
BTN_MANAGE_BOOKINGS = "Управление записями"
BTN_DEFAULT_TIMES = "Времена по умолчанию"
BTN_SETTINGS = "Настройки"
BTN_USERS = "Пользователи"
BTN_EDIT_NAME = "Изменить ФИО"

# Every reply-keyboard label that appears in ANY role's main menu (see
# ``main_menu`` / ``_staff_slot_rows``). SINGLE SOURCE OF TRUTH for the two-part
# menu-trap fix: menu-entry handlers match these in any state (StateFilter("*"))
# and state text-input handlers guard against them (~F.text.in_(MENU_TEXTS)) so a
# menu tap is never swallowed by an in-progress FSM step. Keep in sync with the
# buttons added in ``main_menu``. Excludes inline-only labels (BTN_EDIT_NAME,
# BTN_TIME_*) which are never sent as plain reply-keyboard text.
MENU_TEXTS: frozenset[str] = frozenset(
    {
        BTN_BOOK,
        BTN_MY,
        BTN_SCHEDULE,
        BTN_PROFILE,
        BTN_HELP,
        BTN_MANAGE_SCHEDULE,
        BTN_MANAGE_BOOKINGS,
        BTN_DEFAULT_TIMES,
        BTN_SETTINGS,
        BTN_USERS,
    }
)

# Time-picker control-button labels.
BTN_TIME_DONE = "Готово"
BTN_TIME_CLEAR = "Очистить"
BTN_TIME_CANCEL = "Отмена"

# Localized role labels — never show a raw enum value to the user.
ROLE_LABELS: dict[UserRole, str] = {
    UserRole.student: "Ученик",
    UserRole.teacher: "Преподаватель",
    UserRole.admin: "Администратор",
}

# Reminder options: (label, offset-in-minutes). ``None`` -> no reminder.
REMINDER_OPTIONS: tuple[tuple[str, int | None], ...] = (
    ("За 30 минут", 30),
    ("За 1 час", 60),
    ("За 2 часа", 120),
    ("За 4 часа", 240),
    ("За 8 часов", 480),
    ("За сутки", 1440),
    ("Не напоминать", None),
)

# Sentinel offset used on the wire for "no reminder" (CallbackData needs an int).
NO_REMINDER = -1


def _staff_slot_rows(builder: ReplyKeyboardBuilder) -> None:
    """Add the shared teacher/admin slot-management rows."""
    builder.row(
        KeyboardButton(text=BTN_SCHEDULE),
        KeyboardButton(text=BTN_MANAGE_SCHEDULE),
    )
    builder.row(KeyboardButton(text=BTN_MANAGE_BOOKINGS))
    builder.row(
        KeyboardButton(text=BTN_DEFAULT_TIMES),
        KeyboardButton(text=BTN_SETTINGS),
    )


def main_menu(role: UserRole = UserRole.student) -> ReplyKeyboardMarkup:
    """Build the main reply keyboard tailored to the user's effective role.

    Student — book / my bookings / schedule; teacher — slot management + the
    weekly schedule; admin — the teacher menu plus the unified «Пользователи»
    management menu. «Расписание», «Мой профиль» и «Помощь» показываются всем.
    """
    builder = ReplyKeyboardBuilder()
    if role == UserRole.teacher:
        _staff_slot_rows(builder)
    elif role == UserRole.admin:
        _staff_slot_rows(builder)
        builder.row(KeyboardButton(text=BTN_USERS))
    else:  # student (and any unknown role) — booking-facing menu
        builder.row(KeyboardButton(text=BTN_BOOK))
        builder.row(KeyboardButton(text=BTN_MY), KeyboardButton(text=BTN_SCHEDULE))
    builder.row(KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_HELP))
    return builder.as_markup(resize_keyboard=True)


def profile_actions_inline() -> InlineKeyboardMarkup:
    """Inline actions for the profile view: edit name (phone was removed)."""
    builder = InlineKeyboardBuilder()
    builder.button(text=BTN_EDIT_NAME, callback_data=ProfileCB(action="name"))
    builder.adjust(1)
    return builder.as_markup()


def _week_nav_row(
    offset: int, mode: str, show_prev: bool, show_next: bool, label: str
) -> list[InlineKeyboardButton]:
    """Build the ``‹ Пред. | <label> | След. ›`` navigation row for a week view.

    Arrows are omitted (not merely disabled) when out of range: ``‹`` when at the
    current week (``offset == 0``) and ``›`` when nothing lies further ahead. The
    center label is a non-tappable :class:`NoopCB` button. ``mode`` (``schedule``
    /``book``) rides in the arrow callbacks so the two views never collide.
    """
    row: list[InlineKeyboardButton] = []
    if show_prev:
        row.append(
            InlineKeyboardButton(
                text="‹ Пред.",
                callback_data=WeekNavCB(offset=offset - 1, mode=mode).pack(),
            )
        )
    row.append(InlineKeyboardButton(text=label, callback_data=NoopCB().pack()))
    if show_next:
        row.append(
            InlineKeyboardButton(
                text="След. ›",
                callback_data=WeekNavCB(offset=offset + 1, mode=mode).pack(),
            )
        )
    return row


def week_schedule_markup(
    offset: int, show_prev: bool, show_next: bool, label: str
) -> InlineKeyboardMarkup:
    """Navigation-only keyboard for the weekly schedule (text carries the data)."""
    builder = InlineKeyboardBuilder()
    builder.row(*_week_nav_row(offset, "schedule", show_prev, show_next, label))
    return builder.as_markup()


def week_booking_markup(
    days: Iterable[date],
    offset: int,
    show_prev: bool,
    show_next: bool,
    label: str,
) -> InlineKeyboardMarkup:
    """Day buttons (localized «Пн 15.07») for a week's free days + week navigation.

    Each day button carries a :class:`DateCB` so the existing free-slot time list
    (``times_inline`` / :class:`SlotCB`) handles the actual pick unchanged.
    """
    builder = InlineKeyboardBuilder()
    for d in days:
        builder.button(text=format_day_short(d), callback_data=DateCB(value=d.isoformat()))
    builder.adjust(2)
    builder.row(*_week_nav_row(offset, "book", show_prev, show_next, label))
    return builder.as_markup()


def week_editor_markup(
    days: Iterable[date],
    offset: int,
    show_prev: bool,
    show_next: bool,
    label: str,
) -> InlineKeyboardMarkup:
    """Slot-editor keyboard: one button per VISIBLE day, set-week, week nav.

    Each day button carries an :class:`EditDayCB` (date + the editor's current
    offset, so the per-day flow can return to this week). «Задать времена на всю
    неделю» carries a :class:`SetWeekCB`. The nav row reuses ``WeekNavCB`` in the
    ``"edit"`` mode; ``show_next`` is passed by the caller up to a bounded future
    horizon so empty future weeks can still be populated.
    """
    builder = InlineKeyboardBuilder()
    for d in days:
        builder.button(
            text=format_day_short(d),
            callback_data=EditDayCB(date=d.isoformat(), offset=offset),
        )
    builder.adjust(2)
    builder.row(
        InlineKeyboardButton(
            text="Задать времена на всю неделю",
            callback_data=SetWeekCB(offset=offset).pack(),
        )
    )
    builder.row(*_week_nav_row(offset, "edit", show_prev, show_next, label))
    return builder.as_markup()


def _slot_manage_label(slot: Slot, booker: Optional[User]) -> str:
    """Button label for one slot in the manage day view, e.g. «10:30 · занято: Иванов».

    Button labels are NOT HTML-parsed by Telegram (like the user-list buttons),
    so no escaping is applied here; the booker's ФИО is escaped wherever it
    appears in a message. Long names are truncated so the button stays in-limit.
    """
    t = format_time(slot.starts_at)
    if slot.status == SlotStatus.booked and booker is not None:
        name = booker.full_name if len(booker.full_name) <= 40 else booker.full_name[:39] + "…"
        return f"{t} · занято: {name}"
    if slot.status == SlotStatus.booked:
        return f"{t} · занято"
    return f"{t} · свободно"


def manage_week_markup(
    days: Iterable[date],
    offset: int,
    show_prev: bool,
    show_next: bool,
    label: str,
) -> InlineKeyboardMarkup:
    """Staff «Управление записями» week view: one button per day that HAS slots.

    Each day button carries a :class:`ManageDayCB` (date + current offset). The
    nav row reuses ``WeekNavCB`` in the ``"manage"`` mode so its arrows never
    collide with the schedule / booking / editor views.
    """
    builder = InlineKeyboardBuilder()
    for d in days:
        builder.button(
            text=format_day_short(d),
            callback_data=ManageDayCB(date=d.isoformat(), offset=offset),
        )
    builder.adjust(2)
    builder.row(*_week_nav_row(offset, "manage", show_prev, show_next, label))
    return builder.as_markup()


def manage_day_markup(
    rows: Iterable[tuple[Slot, Optional[Booking], Optional[User]]],
    offset: int,
) -> InlineKeyboardMarkup:
    """Staff day view: one button per slot (time + status) + «‹ Назад к неделе».

    Each slot button carries a :class:`SlotManageCB` (slot id + offset). The back
    button reuses ``WeekNavCB`` in ``"manage"`` mode to re-render the week.
    """
    builder = InlineKeyboardBuilder()
    for slot, _booking, booker in rows:
        builder.button(
            text=_slot_manage_label(slot, booker),
            callback_data=SlotManageCB(slot_id=slot.id, offset=offset),
        )
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(
            text="‹ Назад к неделе",
            callback_data=WeekNavCB(offset=offset, mode="manage").pack(),
        )
    )
    return builder.as_markup()


def force_free_confirm_markup(
    booking_id: int, local_date: date, offset: int
) -> InlineKeyboardMarkup:
    """Confirmation keyboard for freeing a booked slot: «Освободить» / «Назад».

    «Освободить» carries a :class:`ForceFreeCB` (booking id + offset); «Назад»
    returns to the day view via :class:`ManageDayCB`.
    """
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Освободить",
        callback_data=ForceFreeCB(booking_id=booking_id, offset=offset),
    )
    builder.button(
        text="Назад",
        callback_data=ManageDayCB(date=local_date.isoformat(), offset=offset),
    )
    builder.adjust(1)
    return builder.as_markup()


def manage_users_markup(
    users: Iterable[User], slot_id: int, local_date: date, offset: int
) -> InlineKeyboardMarkup:
    """Inline user list (by ФИО) for manually booking ``slot_id`` + «Назад».

    Each user carries a :class:`ManageBookCB` (slot id + target tg id + offset);
    labels are plain text (Telegram does not HTML-parse them). «Назад» returns to
    the day view via :class:`ManageDayCB`.
    """
    builder = InlineKeyboardBuilder()
    for user in users:
        label = user.full_name if len(user.full_name) <= 60 else user.full_name[:57] + "…"
        builder.button(
            text=label,
            callback_data=ManageBookCB(slot_id=slot_id, tg_id=user.tg_id, offset=offset),
        )
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(
            text="‹ Назад",
            callback_data=ManageDayCB(date=local_date.isoformat(), offset=offset).pack(),
        )
    )
    return builder.as_markup()


def override_confirm_markup() -> InlineKeyboardMarkup:
    """Confirmation keyboard for an override that will cancel bookings.

    «Подтвердить отмену» applies the pending override (incl. cancellations);
    «Отмена» discards it and returns to the editor. Both carry a
    :class:`SlotOverrideConfirmCB`; the pending selection rides in FSM data.
    """
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Подтвердить отмену",
        callback_data=SlotOverrideConfirmCB(action="confirm"),
    )
    builder.button(
        text="Отмена",
        callback_data=SlotOverrideConfirmCB(action="cancel"),
    )
    builder.adjust(1)
    return builder.as_markup()


def settings_inline(show_weekends: bool) -> InlineKeyboardMarkup:
    """Inline toggle for the weekend-visibility setting.

    The button label names the action it performs (show / hide), so a tap always
    flips the current state; the surrounding message text reports the state.
    """
    builder = InlineKeyboardBuilder()
    label = "Скрыть выходные (сб/вс)" if show_weekends else "Показать выходные (сб/вс)"
    builder.button(text=label, callback_data=SettingsCB(action="toggle_weekends"))
    builder.adjust(1)
    return builder.as_markup()


def times_inline(slots: Iterable[Slot]) -> InlineKeyboardMarkup:
    """Inline keyboard of start times for the free slots of one date."""
    builder = InlineKeyboardBuilder()
    for slot in slots:
        builder.button(
            text=format_time(slot.starts_at),
            callback_data=SlotCB(slot_id=slot.id),
        )
    builder.adjust(3)
    return builder.as_markup()


def reminder_inline(booking_id: int) -> InlineKeyboardMarkup:
    """Inline keyboard for choosing a reminder offset for a booking."""
    builder = InlineKeyboardBuilder()
    for label, offset in REMINDER_OPTIONS:
        wire_offset = NO_REMINDER if offset is None else offset
        builder.button(
            text=label,
            callback_data=ReminderCB(booking_id=booking_id, offset=wire_offset),
        )
    builder.adjust(2)
    return builder.as_markup()


def booking_actions_inline(booking: Booking) -> InlineKeyboardMarkup:
    """Inline actions (cancel / reschedule) for one active booking."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Отменить",
        callback_data=BookingActionCB(action="cancel", booking_id=booking.id),
    )
    builder.button(
        text="Перенести",
        callback_data=BookingActionCB(action="resched", booking_id=booking.id),
    )
    builder.adjust(2)
    return builder.as_markup()


def time_grid(candidate_times: list[str], selected: set[str]) -> InlineKeyboardMarkup:
    """Toggle grid of candidate start-times (✅ selected / ⬜ not), 4 per row.

    Each time button flips its membership via :class:`TimeToggleCB`; a trailing
    control row offers Готово / Очистить / Отмена via :class:`TimeCtrlCB`.
    """
    builder = InlineKeyboardBuilder()
    for t in candidate_times:
        mark = "✅" if t in selected else "⬜"
        builder.button(text=f"{mark} {t}", callback_data=TimeToggleCB(t=t))
    builder.adjust(4)
    builder.row(
        InlineKeyboardButton(
            text=BTN_TIME_DONE, callback_data=TimeCtrlCB(action="done").pack()
        ),
        InlineKeyboardButton(
            text=BTN_TIME_CLEAR, callback_data=TimeCtrlCB(action="clear").pack()
        ),
        InlineKeyboardButton(
            text=BTN_TIME_CANCEL, callback_data=TimeCtrlCB(action="cancel").pack()
        ),
    )
    return builder.as_markup()


def users_list_markup(entries: Iterable[tuple[int, str]]) -> InlineKeyboardMarkup:
    """Inline list of users for the admin «Пользователи» menu.

    ``entries`` are ``(tg_id, label)`` pairs; the caller builds each label as
    «ФИО — роль» (already truncated). Button text is PLAIN — Telegram does not
    HTML-parse button labels, so names are NOT escaped here (they are escaped
    where they appear in message text). Each button carries a :class:`UserCardCB`
    with the admin-only, server-re-checked ``tg_id``.
    """
    builder = InlineKeyboardBuilder()
    for tg_id, label in entries:
        builder.button(text=label, callback_data=UserCardCB(tg_id=tg_id))
    builder.adjust(1)
    return builder.as_markup()


def user_card_markup(
    tg_id: int, role: UserRole, is_env_admin: bool
) -> InlineKeyboardMarkup:
    """Action buttons for one user's card, tailored to their effective role.

    An env-admin (``is_env_admin``) gets NO role/delete buttons — only «Назад»
    (admin is env-authoritative; role is managed via ADMIN_IDS and the user is
    never deletable). A student can be promoted to teacher, a teacher demoted to
    student; both non-admin roles can be deleted.
    """
    builder = InlineKeyboardBuilder()
    if not is_env_admin:
        if role == UserRole.student:
            builder.button(
                text="Назначить преподавателем",
                callback_data=UserRoleCB(tg_id=tg_id, role=UserRole.teacher.value),
            )
        elif role == UserRole.teacher:
            builder.button(
                text="Снять преподавателя",
                callback_data=UserRoleCB(tg_id=tg_id, role=UserRole.student.value),
            )
        builder.button(
            text="🗑 Удалить пользователя",
            callback_data=UserDeleteCB(tg_id=tg_id),
        )
    builder.button(text="‹ Назад к списку", callback_data=UsersListCB())
    builder.adjust(1)
    return builder.as_markup()


def user_delete_confirm_markup(tg_id: int) -> InlineKeyboardMarkup:
    """Confirmation keyboard for deleting a user: «Удалить» / «Отмена» (to card)."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Удалить", callback_data=UserDeleteConfirmCB(tg_id=tg_id)
    )
    builder.button(text="Отмена", callback_data=UserCardCB(tg_id=tg_id))
    builder.adjust(1)
    return builder.as_markup()


__all__ = [
    "BTN_BOOK",
    "BTN_MY",
    "BTN_HELP",
    "BTN_PROFILE",
    "BTN_SCHEDULE",
    "BTN_MANAGE_SCHEDULE",
    "BTN_MANAGE_BOOKINGS",
    "BTN_DEFAULT_TIMES",
    "BTN_SETTINGS",
    "BTN_USERS",
    "BTN_EDIT_NAME",
    "MENU_TEXTS",
    "ROLE_LABELS",
    "NO_REMINDER",
    "REMINDER_OPTIONS",
    "main_menu",
    "profile_actions_inline",
    "week_schedule_markup",
    "week_booking_markup",
    "week_editor_markup",
    "manage_week_markup",
    "manage_day_markup",
    "force_free_confirm_markup",
    "manage_users_markup",
    "override_confirm_markup",
    "settings_inline",
    "times_inline",
    "reminder_inline",
    "booking_actions_inline",
    "time_grid",
    "users_list_markup",
    "user_card_markup",
    "user_delete_confirm_markup",
    "format_date",
]
