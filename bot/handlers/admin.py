"""Admin handlers: assign / remove moderators."""

from __future__ import annotations

import logging
from typing import Optional

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings, is_admin
from bot.db.models import User, UserRole
from bot.db.repositories import get_user, set_role
from bot.keyboards import BTN_ASSIGN_MOD, BTN_REMOVE_MOD
from bot.states import AssignModerator, RemoveModerator

logger = logging.getLogger(__name__)

router = Router(name="admin")


def _authorized(user: Optional[User], settings: Settings) -> bool:
    return user is not None and is_admin(user.tg_id, settings)


def _parse_tg_id(text: Optional[str]) -> Optional[int]:
    """Parse an integer Telegram id, or ``None`` if malformed."""
    if not text:
        return None
    try:
        return int(text.strip())
    except ValueError:
        return None


# --- Assign moderator ----------------------------------------------------

@router.message(StateFilter(None), F.text == BTN_ASSIGN_MOD)
async def assign_start(
    message: Message, state: FSMContext, user: Optional[User], settings: Settings
) -> None:
    if not _authorized(user, settings):
        await message.answer("Недостаточно прав.")
        return
    await message.answer("Введите Telegram id пользователя, которого назначить модератором:")
    await state.set_state(AssignModerator.tg_id)


@router.message(AssignModerator.tg_id, F.text)
async def assign_apply(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    tg_id = _parse_tg_id(message.text)
    if tg_id is None:
        await message.answer("Нужно целое число (Telegram id). Попробуйте ещё раз:")
        return

    target = await get_user(session, tg_id)
    if target is None:
        await state.clear()
        await message.answer(
            "Пользователь не найден. Он должен сначала запустить бота (/start)."
        )
        return

    await set_role(session, tg_id, UserRole.moderator)
    await state.clear()
    await message.answer(f"Пользователь {tg_id} назначен модератором.")


# --- Remove moderator ----------------------------------------------------

@router.message(StateFilter(None), F.text == BTN_REMOVE_MOD)
async def remove_start(
    message: Message, state: FSMContext, user: Optional[User], settings: Settings
) -> None:
    if not _authorized(user, settings):
        await message.answer("Недостаточно прав.")
        return
    await message.answer("Введите Telegram id пользователя, которого снять с модераторов:")
    await state.set_state(RemoveModerator.tg_id)


@router.message(RemoveModerator.tg_id, F.text)
async def remove_apply(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    tg_id = _parse_tg_id(message.text)
    if tg_id is None:
        await message.answer("Нужно целое число (Telegram id). Попробуйте ещё раз:")
        return

    target = await get_user(session, tg_id)
    if target is None:
        await state.clear()
        await message.answer("Пользователь не найден.")
        return

    if target.role != UserRole.moderator:
        await state.clear()
        await message.answer("Этот пользователь не является модератором.")
        return

    await set_role(session, tg_id, UserRole.student)
    await state.clear()
    await message.answer(f"Пользователь {tg_id} снят с модераторов.")
