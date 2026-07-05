"""Profile handlers: view own profile, edit ФИО (any registered user)."""

from __future__ import annotations

import logging
from html import escape
from typing import Optional

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.callbacks import ProfileCB
from bot.config import Settings
from bot.db.models import User, UserRole, effective_role
from bot.db.repositories import update_user_name
from bot.handlers.common import show_main_menu
from bot.keyboards import (
    BTN_PROFILE,
    MENU_TEXTS,
    ROLE_LABELS,
    profile_actions_inline,
)
from bot.states import EditProfile
from bot.utils import clean_full_name

logger = logging.getLogger(__name__)

router = Router(name="profile")

_UNREGISTERED = "Пожалуйста, начните с команды /start"


@router.message(StateFilter("*"), F.text == BTN_PROFILE)
async def show_profile(
    message: Message, state: FSMContext, user: Optional[User], settings: Settings
) -> None:
    """Show the user's own ФИО and role (cancels any in-progress flow)."""
    await state.clear()
    if user is None:
        await message.answer(_UNREGISTERED)
        return
    role = effective_role(user, settings) or UserRole.student
    label = ROLE_LABELS.get(role, ROLE_LABELS[UserRole.student])
    # Escape user-entered fields: the bot sends parse_mode=HTML bot-wide, and a
    # user could put HTML in their own name and break their own profile message.
    text = (
        "<b>Мой профиль</b>\n\n"
        f"ФИО: {escape(user.full_name)}\n"
        f"Роль: {label}"
    )
    await message.answer(text, reply_markup=profile_actions_inline())


@router.callback_query(ProfileCB.filter(F.action == "name"))
async def edit_name_start(
    callback: CallbackQuery, state: FSMContext, user: Optional[User]
) -> None:
    if user is None:
        await callback.answer(_UNREGISTERED, show_alert=True)
        return
    await state.set_state(EditProfile.full_name)
    await callback.message.answer("Введите новое ФИО:")
    await callback.answer()


@router.message(EditProfile.full_name, F.text, ~F.text.in_(MENU_TEXTS))
async def edit_name_apply(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: Optional[User],
    settings: Settings,
) -> None:
    if user is None:
        await state.clear()
        await message.answer(_UNREGISTERED)
        return
    full_name = clean_full_name(message.text)
    if full_name is None:
        await message.answer(
            "Имя не может быть пустым и должно быть не длиннее 100 символов. "
            "Попробуйте ещё раз. Или /cancel для отмены."
        )
        return
    await update_user_name(session, user.tg_id, full_name)
    await state.clear()
    await message.answer("ФИО обновлено.")
    await show_main_menu(message, user, settings)


@router.message(EditProfile.full_name, ~F.text)
async def edit_name_invalid(message: Message) -> None:
    """Non-text input while entering the name — re-prompt (avoids a silent drop)."""
    await message.answer("Введите ФИО текстом. Попробуйте ещё раз. Или /cancel для отмены.")
