"""Common handlers: /start, registration FSM, main menu, help."""

from __future__ import annotations

import logging
from typing import Optional

from aiogram import F, Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings, is_admin
from bot.db.models import (
    User,
    UserRole,
    can_view_staff_schedule,
    effective_role,
    is_moderator_or_admin,
    is_teacher,
)
from bot.db.repositories import create_user
from bot.keyboards import BTN_HELP, main_menu, phone_request
from bot.states import Registration
from bot.utils import clean_full_name, normalize_phone

logger = logging.getLogger(__name__)

router = Router(name="common")


def _menu_for(user: Optional[User], settings: Settings) -> "object":
    """Reply keyboard for the given user's effective role."""
    role = effective_role(user, settings) or UserRole.student
    return main_menu(role)


async def show_main_menu(message: Message, user: Optional[User], settings: Settings) -> None:
    """Send the main menu tailored to the user's role."""
    await message.answer("Главное меню:", reply_markup=_menu_for(user, settings))


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    user: Optional[User],
    settings: Settings,
) -> None:
    """Entry point: register a new user or show the menu for an existing one."""
    await state.clear()
    if user is not None:
        await message.answer("С возвращением!")
        await show_main_menu(message, user, settings)
        return

    await message.answer(
        "Добро пожаловать в бот записи на занятия!\n\nКак вас зовут? "
        "Напишите имя и фамилию."
    )
    await state.set_state(Registration.full_name)


@router.message(Registration.full_name, F.text)
async def reg_full_name(message: Message, state: FSMContext) -> None:
    """Save the full name and ask for a phone number."""
    full_name = clean_full_name(message.text)
    if full_name is None:
        await message.answer(
            "Имя не может быть пустым и должно быть не длиннее 100 символов. "
            "Попробуйте ещё раз."
        )
        return
    await state.update_data(full_name=full_name)
    await message.answer(
        "Отправьте номер телефона кнопкой ниже или введите его вручную.",
        reply_markup=phone_request(),
    )
    await state.set_state(Registration.phone)


@router.message(Registration.full_name, ~F.text)
async def reg_full_name_invalid(message: Message) -> None:
    """Non-text input while entering the name — re-prompt (avoids a silent drop)."""
    await message.answer("Напишите имя и фамилию текстом.")


@router.message(Registration.phone)
async def reg_phone(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    """Save the phone, create the user (admin if in ADMIN_IDS) and show menu."""
    contact_phone = message.contact.phone_number if message.contact is not None else None
    phone = normalize_phone(contact_phone, message.text)

    if phone is None:
        await message.answer(
            "Не удалось получить номер. Отправьте его кнопкой или введите вручную."
        )
        return

    data = await state.get_data()
    full_name = data.get("full_name", "").strip() or "Без имени"

    role = UserRole.admin if is_admin(message.from_user.id, settings) else UserRole.student
    user = await create_user(
        session,
        tg_id=message.from_user.id,
        full_name=full_name,
        phone=phone,
        role=role,
    )
    await state.clear()
    await message.answer("Регистрация завершена!")
    await show_main_menu(message, user, settings)


@router.message(StateFilter(None), F.text == BTN_HELP)
async def cmd_help(message: Message, user: Optional[User], settings: Settings) -> None:
    """Show role-aware help text."""
    lines = ["<b>Помощь</b>", ""]
    role = effective_role(user, settings) or UserRole.student
    if role == UserRole.student:
        lines += [
            "• «Записаться» — выбрать свободное время и записаться на занятие.",
            "• «Мои записи» — посмотреть, отменить или перенести записи.",
            "• «Расписание» — посмотреть свободные и занятые слоты по дням.",
        ]
    if can_view_staff_schedule(user, settings):
        lines.append(
            "• «Расписание на день» — занятость на день с ФИО и телефоном учеников."
        )
    if is_teacher(user, settings):
        lines.append("• «Все слоты» — ближайшие слоты со статусом занятости.")
    if is_moderator_or_admin(user, settings):
        lines.append("• «Добавить слоты» — создать свободные слоты на дату.")
    if is_admin(user.tg_id if user else 0, settings):
        lines += [
            "• «Все слоты» — ближайшие слоты со статусом занятости.",
            "• «Назначить/Снять модератора» — управление модераторами.",
            "• «Назначить/Снять преподавателя» — управление преподавателями.",
        ]
    lines += [
        "• «Мой профиль» — посмотреть и изменить ФИО и телефон.",
        "",
        "Команда /start открывает главное меню.",
    ]
    await message.answer("\n".join(lines))
