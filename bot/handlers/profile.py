"""Profile handlers: view own profile, edit ФИО / phone (any registered user)."""

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
from bot.db.repositories import update_user_name, update_user_phone
from bot.handlers.common import show_main_menu
from bot.keyboards import (
    BTN_PROFILE,
    ROLE_LABELS,
    phone_request,
    profile_actions_inline,
)
from bot.states import EditProfile
from bot.utils import clean_full_name, normalize_phone

logger = logging.getLogger(__name__)

router = Router(name="profile")

_UNREGISTERED = "Пожалуйста, начните с команды /start"


@router.message(StateFilter(None), F.text == BTN_PROFILE)
async def show_profile(
    message: Message, user: Optional[User], settings: Settings
) -> None:
    """Show the user's own ФИО, телефон and localized role."""
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
        f"Телефон: {escape(user.phone)}\n"
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


@router.message(EditProfile.full_name, F.text)
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
            "Попробуйте ещё раз:"
        )
        return
    await update_user_name(session, user.tg_id, full_name)
    await state.clear()
    await message.answer("ФИО обновлено.")
    await show_main_menu(message, user, settings)


@router.message(EditProfile.full_name, ~F.text)
async def edit_name_invalid(message: Message) -> None:
    """Non-text input while entering the name — re-prompt (avoids a silent drop)."""
    await message.answer("Введите ФИО текстом. Попробуйте ещё раз:")


@router.callback_query(ProfileCB.filter(F.action == "phone"))
async def edit_phone_start(
    callback: CallbackQuery, state: FSMContext, user: Optional[User]
) -> None:
    if user is None:
        await callback.answer(_UNREGISTERED, show_alert=True)
        return
    await state.set_state(EditProfile.phone)
    await callback.message.answer(
        "Отправьте новый номер телефона кнопкой ниже или введите его вручную.",
        reply_markup=phone_request(),
    )
    await callback.answer()


@router.message(EditProfile.phone)
async def edit_phone_apply(
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
    contact_phone = message.contact.phone_number if message.contact is not None else None
    phone = normalize_phone(contact_phone, message.text)
    if phone is None:
        await message.answer(
            "Не удалось получить номер. Отправьте его кнопкой или введите вручную."
        )
        return
    await update_user_phone(session, user.tg_id, phone)
    await state.clear()
    await message.answer("Телефон обновлён.")
    await show_main_menu(message, user, settings)
