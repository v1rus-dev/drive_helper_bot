"""Common handlers: /start, registration FSM, main menu, help."""

from __future__ import annotations

import logging
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.callbacks import NoopCB
from bot.config import Settings, is_admin
from bot.db.models import (
    User,
    UserRole,
    can_manage_slots,
    effective_role,
)
from bot.db.repositories import create_user
from bot.keyboards import BTN_HELP, MENU_TEXTS, main_menu
from bot.states import Registration
from bot.utils import clean_full_name

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


@router.message(Command("cancel"))
async def cmd_cancel(
    message: Message,
    state: FSMContext,
    user: Optional[User],
    settings: Settings,
) -> None:
    """Global /cancel: clear any FSM state and return to the main menu.

    Registered first (common router included first, handler defined before the
    registration handlers) so it wins from any state.
    """
    await state.clear()
    if user is None:
        await message.answer("Отменено. Пожалуйста, начните с команды /start")
        return
    await message.answer("Отменено.")
    await show_main_menu(message, user, settings)


@router.message(Registration.full_name, F.text, ~F.text.in_(MENU_TEXTS))
async def reg_full_name(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    settings: Settings,
) -> None:
    """Save the full name, create the user (admin if in ADMIN_IDS) and show menu.

    Registration collects only ФИО now (phone was removed). ``~F.text.in_(MENU_TEXTS)``
    lets a main-menu button label fall through to its (StateFilter("*")) menu
    handler instead of being consumed as a name.
    """
    full_name = clean_full_name(message.text)
    if full_name is None:
        await message.answer(
            "Имя не может быть пустым и должно быть не длиннее 100 символов. "
            "Попробуйте ещё раз. Или /cancel для отмены."
        )
        return

    role = UserRole.admin if is_admin(message.from_user.id, settings) else UserRole.student
    user = await create_user(
        session,
        tg_id=message.from_user.id,
        full_name=full_name,
        role=role,
    )
    await state.clear()
    await message.answer("Регистрация завершена!")
    await show_main_menu(message, user, settings)


@router.message(Registration.full_name, ~F.text)
async def reg_full_name_invalid(message: Message) -> None:
    """Non-text input while entering the name — re-prompt (avoids a silent drop)."""
    await message.answer("Напишите имя и фамилию текстом. Или /cancel для отмены.")


@router.callback_query(NoopCB.filter())
async def ignore_noop(callback: CallbackQuery) -> None:
    """No-op tap on a non-tappable label button — just clear the spinner."""
    await callback.answer()


@router.message(StateFilter("*"), F.text == BTN_HELP)
async def cmd_help(
    message: Message, state: FSMContext, user: Optional[User], settings: Settings
) -> None:
    """Show role-aware help text (cancels any in-progress flow first)."""
    await state.clear()
    lines = ["<b>Помощь</b>", ""]
    role = effective_role(user, settings) or UserRole.student
    if role == UserRole.student:
        lines += [
            "• «Записаться» — выбрать день недели и свободное время, записаться.",
            "• «Мои записи» — посмотреть, отменить или перенести записи.",
        ]
    lines.append(
        "• «Расписание» — слоты на неделю по дням: свободные и занятые "
        "(с именем записавшегося). Листается по неделям."
    )
    if can_manage_slots(user, settings):
        lines += [
            "• «Ведение расписания» — недельный редактор слотов: кнопка на каждый "
            "день недели открывает сетку времён (тап переключает ✅/⬜), «Готово» "
            "делает времена дня ровно отмеченными. «Задать времена на всю неделю» "
            "применяет один набор ко всем дням. Занятые слоты сохраняются, "
            "прошедшие времена пропускаются. Неделя листается кнопками ‹ / ›.",
            "• «Управление записями» — недельный обзор слотов по дням: тап по дню "
            "открывает его слоты, тап по слоту позволяет освободить занятый "
            "(бронь отменяется, ученик получает уведомление) или записать "
            "выбранного пользователя на свободный. Показываются все дни со слотами, "
            "даже выходные.",
            "• «Времена по умолчанию» — сохранить набор времён-пресет, который "
            "подставляется в «Задать времена на всю неделю».",
            "• «Настройки» — показывать ли выходные (сб/вс) в расписании, записи "
            "и редакторе; по умолчанию выходные скрыты.",
        ]
    if is_admin(user.tg_id if user else 0, settings):
        lines.append(
            "• «Пользователи» — список всех зарегистрированных: открыть карточку, "
            "сменить роль (назначить/снять преподавателя) или удалить пользователя "
            "(его будущие записи освобождаются)."
        )
    lines += [
        "• «Мой профиль» — посмотреть роль и изменить ФИО.",
        "",
        "Команда /start открывает главное меню, /cancel отменяет текущее действие.",
    ]
    await message.answer("\n".join(lines))


# Lowest-priority catch-all: a registered user (unregistered ones are stopped by
# AuthMiddleware) who sends unrecognized text with NO active FSM state gets the
# menu back instead of silence. StateFilter(None) keeps it out of every in-flow
# text step, and living in its own router — included LAST in main.py, after all
# feature routers — means it can never pre-empt a menu button or a state handler.
fallback_router = Router(name="fallback")


@fallback_router.message(StateFilter(None), F.text)
async def fallback_menu(
    message: Message, user: Optional[User], settings: Settings
) -> None:
    """Re-show the main menu for stray text so nothing is silently dropped."""
    await message.answer("Не понял команду. Вот главное меню:")
    await show_main_menu(message, user, settings)
