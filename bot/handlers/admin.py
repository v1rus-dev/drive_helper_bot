"""Admin handlers: the unified «Пользователи» management menu.

One inline menu covers viewing every registered user, changing a user's role
(assign / remove teacher) and deleting a user. No Telegram id is ever typed or
shown to users — the admin picks a registered user from an inline list rendered
by ФИО, and the target ``tg_id`` rides only in admin-only callback data.

Security model (every point re-checked SERVER-SIDE, never trusting hidden
buttons or stale callback data):

* ``is_admin`` (env-authoritative via ADMIN_IDS) is re-checked on the menu
  entry AND on EVERY callback below; a non-admin gets «Недостаточно прав.».
* An env-admin is never a valid target for a role change or a delete — both are
  re-checked with ``is_admin(target)`` and refused, so admin stays managed only
  through ADMIN_IDS.
* All ФИО are HTML-escaped in message text (button labels are plain — Telegram
  does not HTML-parse them).
* Deleting a user frees their future slots and removes the freed bookings'
  reminder jobs (via the ids returned by :func:`delete_user`, best-effort after
  commit), so no orphaned occupied slot or dangling reminder is left behind.

The flow is entirely button-driven (no text input), so there is no FSM
text-trap to guard beyond the menu-entry ``state.clear()``.
"""

from __future__ import annotations

import logging
from html import escape
from typing import Optional

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.callbacks import (
    UserCardCB,
    UserDeleteCB,
    UserDeleteConfirmCB,
    UserRoleCB,
    UsersListCB,
)
from bot.config import Settings, is_admin
from bot.db.models import User, UserRole, effective_role
from bot.db.repositories import delete_user, get_all_users, get_user, set_role
from bot.keyboards import (
    BTN_USERS,
    ROLE_LABELS,
    user_card_markup,
    user_delete_confirm_markup,
    users_list_markup,
)
from bot.services.reminders import remove_reminder

logger = logging.getLogger(__name__)

router = Router(name="admin")

# Cap on the user list so a huge roster never overflows the inline keyboard; a
# note is shown when more users exist.
_USER_LIST_CAP = 50


def _authorized(user: Optional[User], settings: Settings) -> bool:
    return user is not None and is_admin(user.tg_id, settings)


def _role_label(role: UserRole) -> str:
    """Localized Russian label for a role (never show the raw enum)."""
    return ROLE_LABELS.get(role, role.value)


async def _render_users_list(
    session: AsyncSession, settings: Settings
) -> tuple[str, InlineKeyboardMarkup]:
    """Build the ``(text, keyboard)`` for the full user list.

    Button labels are «ФИО — роль» (plain, not HTML-parsed) using the target's
    EFFECTIVE role, so an env-admin always reads as «Администратор». Capped at
    :data:`_USER_LIST_CAP` with a note when the roster is larger.
    """
    users = list(await get_all_users(session))
    if not users:
        return "Нет пользователей.", users_list_markup([])

    note = ""
    if len(users) > _USER_LIST_CAP:
        note = f"\n\nПоказаны первые {_USER_LIST_CAP} из {len(users)}."
        users = users[:_USER_LIST_CAP]

    entries: list[tuple[int, str]] = []
    for u in users:
        role = effective_role(u, settings) or UserRole.student
        name = u.full_name if len(u.full_name) <= 40 else u.full_name[:39] + "…"
        entries.append((u.tg_id, f"{name} — {_role_label(role)}"))

    text = "Пользователи. Выберите, чтобы открыть карточку:" + note
    return text, users_list_markup(entries)


async def _show_users_list(
    callback: CallbackQuery, session: AsyncSession, settings: Settings
) -> None:
    """Re-render the user list in place (edit the current message)."""
    text, markup = await _render_users_list(session, settings)
    await callback.message.edit_text(text, reply_markup=markup)


async def _show_card(
    callback: CallbackQuery,
    session: AsyncSession,
    tg_id: int,
    settings: Settings,
) -> None:
    """Render a user's card, or fall back to the list if the user is gone."""
    target = await get_user(session, tg_id)
    if target is None:
        await callback.answer("Пользователь не найден.", show_alert=True)
        await _show_users_list(callback, session, settings)
        return
    role = effective_role(target, settings) or UserRole.student
    env_admin = is_admin(target.tg_id, settings)
    lines = [escape(target.full_name), f"Роль: {_role_label(role)}"]
    if env_admin:
        lines.append("Роль управляется через ADMIN_IDS.")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=user_card_markup(target.tg_id, role, env_admin),
    )
    await callback.answer()


# --- Menu entry ----------------------------------------------------------

@router.message(StateFilter("*"), F.text == BTN_USERS)
async def users_start(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    """«Пользователи» — render the user list (cancels any in-progress flow)."""
    # Cancel any in-progress flow so a menu tap always (re)starts this one; the
    # permission re-check below still gates the action server-side.
    await state.clear()
    if not _authorized(user, settings):
        await message.answer("Недостаточно прав.")
        return
    text, markup = await _render_users_list(session, settings)
    await message.answer(text, reply_markup=markup)


@router.callback_query(UsersListCB.filter())
async def users_list_back(
    callback: CallbackQuery,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    """«‹ Назад к списку» — re-render the user list."""
    if not _authorized(user, settings):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    await _show_users_list(callback, session, settings)
    await callback.answer()


# --- User card -----------------------------------------------------------

@router.callback_query(UserCardCB.filter())
async def user_card(
    callback: CallbackQuery,
    callback_data: UserCardCB,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    """Open a user's card (also the «Отмена» target from the delete confirm)."""
    if not _authorized(user, settings):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    await _show_card(callback, session, callback_data.tg_id, settings)


# --- Role change ---------------------------------------------------------

@router.callback_query(UserRoleCB.filter())
async def user_role_change(
    callback: CallbackQuery,
    callback_data: UserRoleCB,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    """Assign / remove teacher, then re-render the card with the new role."""
    if not _authorized(user, settings):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    target = await get_user(session, callback_data.tg_id)
    if target is None:
        await callback.answer("Пользователь не найден.", show_alert=True)
        await _show_users_list(callback, session, settings)
        return
    # Never role-change an env-admin — admin is env-authoritative (ADMIN_IDS).
    if is_admin(target.tg_id, settings):
        await callback.answer("Роль администратора менять нельзя.", show_alert=True)
        return
    # Validate the requested role: only student <-> teacher are permitted here.
    try:
        new_role = UserRole(callback_data.role)
    except ValueError:
        await callback.answer("Некорректная роль.", show_alert=True)
        return
    if new_role not in (UserRole.student, UserRole.teacher):
        await callback.answer("Некорректная роль.", show_alert=True)
        return
    await set_role(session, target.tg_id, new_role)
    await _show_card(callback, session, target.tg_id, settings)


# --- Delete --------------------------------------------------------------

@router.callback_query(UserDeleteCB.filter())
async def user_delete_request(
    callback: CallbackQuery,
    callback_data: UserDeleteCB,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    """Show the delete confirmation for a user."""
    if not _authorized(user, settings):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    target = await get_user(session, callback_data.tg_id)
    if target is None:
        await callback.answer("Пользователь не найден.", show_alert=True)
        await _show_users_list(callback, session, settings)
        return
    if is_admin(target.tg_id, settings):
        await callback.answer("Администратора удалить нельзя.", show_alert=True)
        return
    await callback.message.edit_text(
        f"Удалить {escape(target.full_name)}? Его будущие записи будут "
        "освобождены. Действие необратимо.",
        reply_markup=user_delete_confirm_markup(target.tg_id),
    )
    await callback.answer()


@router.callback_query(UserDeleteConfirmCB.filter())
async def user_delete_confirm(
    callback: CallbackQuery,
    callback_data: UserDeleteConfirmCB,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    """Confirm deletion: delete the user, free future slots, drop reminders."""
    if not _authorized(user, settings):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    target = await get_user(session, callback_data.tg_id)
    if target is None:
        # Race / double-tap: the user is already gone.
        await callback.answer("Пользователь не найден.", show_alert=True)
        await _show_users_list(callback, session, settings)
        return
    if is_admin(target.tg_id, settings):
        await callback.answer("Администратора удалить нельзя.", show_alert=True)
        return

    # delete_user frees the target's active-booking slots, deletes their booking
    # rows, reassigns created_by on slots they made to the acting admin, and
    # deletes the user — all in one transaction — returning the freed
    # active-booking ids so we can drop their reminder jobs here, AFTER commit.
    # The acting admin (callback.from_user.id) is an env-admin (guaranteed by the
    # is_admin re-check via _authorized), so it is a valid non-null created_by FK.
    active_booking_ids = await delete_user(
        session, target.tg_id, reassign_to=callback.from_user.id
    )
    for booking_id in active_booking_ids:
        try:
            remove_reminder(booking_id)
        except Exception:  # noqa: BLE001 - reminder removal is best-effort
            logger.info(
                "Could not remove reminder for booking=%s on user delete", booking_id
            )
    logger.info(
        "Admin %s deleted user %s (%s reminder job(s) removed)",
        user.tg_id,
        target.tg_id,
        len(active_booking_ids),
    )

    await callback.answer("Пользователь удалён.")
    await _show_users_list(callback, session, settings)
