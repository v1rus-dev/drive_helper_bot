"""Auth / session middleware.

Injects a per-update :class:`AsyncSession`, the current :class:`User` (or
``None``) and the :class:`Settings` into handler data. Also enforces the
"start with /start" rule for unregistered users and keeps the admin role in
sync with the ``ADMIN_IDS`` environment variable.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.config import get_settings, is_admin
from bot.db.database import get_sessionmaker
from bot.db.models import UserRole
from bot.db.repositories import get_user, set_role


class AuthMiddleware(BaseMiddleware):
    """Load user + session for every message / callback and gate access."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        settings = get_settings()
        sessionmaker = get_sessionmaker()

        async with sessionmaker() as session:
            data["session"] = session
            data["settings"] = settings

            tg_id = event.from_user.id if event.from_user else None
            user = await get_user(session, tg_id) if tg_id is not None else None

            # Keep the DB role symmetrically aligned with env authority for
            # existing users: promote to admin when in ADMIN_IDS, and demote a
            # stale stored admin back to student when no longer in ADMIN_IDS
            # (so de-provisioning actually revokes moderator-level access).
            if user is not None:
                if is_admin(user.tg_id, settings) and user.role != UserRole.admin:
                    user = await set_role(session, user.tg_id, UserRole.admin)
                elif not is_admin(user.tg_id, settings) and user.role == UserRole.admin:
                    user = await set_role(session, user.tg_id, UserRole.student)

            data["user"] = user

            if user is None:
                blocked = await self._block_unregistered(event, data)
                if blocked:
                    return None

            return await handler(event, data)

    @classmethod
    async def _block_unregistered(
        cls, event: TelegramObject, data: dict[str, Any]
    ) -> bool:
        """Return ``True`` (and reply) when an unregistered user must be stopped.

        Allowed through: the ``/start`` command and any message that belongs to
        an active FSM flow (registration).
        """
        prompt = "Пожалуйста, начните с команды /start"

        if isinstance(event, Message):
            text = event.text or ""
            if text.startswith("/start"):
                return False
            if await cls._raw_state(data) is not None:
                # Inside the registration FSM — let the handler process it.
                return False
            await event.answer(prompt)
            return True

        if isinstance(event, CallbackQuery):
            await event.answer(prompt, show_alert=True)
            return True

        return False

    @staticmethod
    async def _raw_state(data: dict[str, Any]) -> Optional[str]:
        """Read the current FSM state from middleware data, robustly.

        ``raw_state`` is normally injected by aiogram's FSM middleware; fall
        back to querying the ``FSMContext`` directly if only that is present.
        """
        if "raw_state" in data:
            return data["raw_state"]
        state = data.get("state")
        if state is not None:
            return await state.get_state()
        return None
