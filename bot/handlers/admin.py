"""Admin handlers: assign / remove teachers via inline user selection.

No Telegram id is ever typed or shown to users — the admin picks a registered
user from an inline list rendered by ФИО. The target ``tg_id`` rides only in
admin-only callback data and every action is re-checked server-side.
"""

from __future__ import annotations

import logging
from html import escape
from typing import Optional

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.callbacks import UserSelectCB
from bot.config import Settings, is_admin
from bot.db.models import User, UserRole
from bot.db.repositories import get_user, get_users_by_role, set_role
from bot.keyboards import (
    BTN_ASSIGN_TEACHER,
    BTN_REMOVE_TEACHER,
    ROLE_LABELS,
    users_inline,
)

logger = logging.getLogger(__name__)

router = Router(name="admin")


def _authorized(user: Optional[User], settings: Settings) -> bool:
    return user is not None and is_admin(user.tg_id, settings)


def _role_label(role: UserRole) -> str:
    """Localized Russian label for a stored role (never show the raw enum)."""
    return ROLE_LABELS.get(role, role.value)


# --- Assign teacher ------------------------------------------------------

@router.message(StateFilter("*"), F.text == BTN_ASSIGN_TEACHER)
async def assign_teacher_start(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    # Cancel any in-progress flow; admin authority is still re-checked below.
    await state.clear()
    if not _authorized(user, settings):
        await message.answer("Недостаточно прав.")
        return
    # Candidates: registered students, minus any env-admin (defensive — env
    # admins keep a stored 'admin' role, but filter anyway).
    candidates = [
        u
        for u in await get_users_by_role(session, UserRole.student)
        if not is_admin(u.tg_id, settings)
    ]
    if not candidates:
        await message.answer("Нет подходящих пользователей.")
        return
    await message.answer(
        "Выберите пользователя, которого назначить преподавателем:",
        reply_markup=users_inline(candidates, action="assign"),
    )


@router.callback_query(UserSelectCB.filter(F.action == "assign"))
async def assign_teacher_apply(
    callback: CallbackQuery,
    callback_data: UserSelectCB,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    # Re-check server-side: callback data is client-supplied and the admin's
    # rights may have changed since the list was rendered.
    if not _authorized(user, settings):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    target = await get_user(session, callback_data.tg_id)
    if target is None:
        await callback.message.edit_text("Пользователь не найден.")
        await callback.answer()
        return
    # Never demote an env-admin through role handlers — admin is env-authoritative.
    if is_admin(target.tg_id, settings):
        await callback.message.edit_text(
            "Это администратор — роль управляется через ADMIN_IDS, изменить нельзя."
        )
        await callback.answer()
        return

    prev = _role_label(target.role)
    await set_role(session, target.tg_id, UserRole.teacher)
    await callback.message.edit_text(
        f"{escape(target.full_name)} назначен преподавателем (была роль: {prev})."
    )
    await callback.answer()


# --- Remove teacher ------------------------------------------------------

@router.message(StateFilter("*"), F.text == BTN_REMOVE_TEACHER)
async def remove_teacher_start(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    # Cancel any in-progress flow; admin authority is still re-checked below.
    await state.clear()
    if not _authorized(user, settings):
        await message.answer("Недостаточно прав.")
        return
    teachers = [
        u
        for u in await get_users_by_role(session, UserRole.teacher)
        if not is_admin(u.tg_id, settings)
    ]
    if not teachers:
        await message.answer("Нет преподавателей.")
        return
    await message.answer(
        "Выберите преподавателя, которого снять:",
        reply_markup=users_inline(teachers, action="remove"),
    )


@router.callback_query(UserSelectCB.filter(F.action == "remove"))
async def remove_teacher_apply(
    callback: CallbackQuery,
    callback_data: UserSelectCB,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    # Re-check server-side (see assign_teacher_apply).
    if not _authorized(user, settings):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    target = await get_user(session, callback_data.tg_id)
    if target is None:
        await callback.message.edit_text("Пользователь не найден.")
        await callback.answer()
        return
    if is_admin(target.tg_id, settings):
        await callback.message.edit_text(
            "Это администратор — роль управляется через ADMIN_IDS, изменить нельзя."
        )
        await callback.answer()
        return
    if target.role != UserRole.teacher:
        await callback.message.edit_text("Этот пользователь не является преподавателем.")
        await callback.answer()
        return

    await set_role(session, target.tg_id, UserRole.student)
    await callback.message.edit_text(
        f"{escape(target.full_name)} снят с преподавателей."
    )
    await callback.answer()
