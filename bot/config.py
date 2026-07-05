"""Application configuration via pydantic-settings.

Settings are instantiated lazily through :func:`get_settings` so that merely
importing modules never triggers validation (e.g. a missing ``BOT_TOKEN`` must
not crash an import smoke test).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment / ``.env``."""

    # Required Telegram bot token from @BotFather.
    bot_token: str

    # Raw comma-separated admin ids as they arrive from the environment.
    # pydantic-settings would try to JSON-decode a ``list[int]`` field, which
    # breaks on the "111,222" form — so we keep the raw string and expose a
    # parsed property instead.
    admin_ids: str = ""

    tz: str = "Europe/Minsk"
    default_slot_duration_min: int = 90
    db_path: str = "/data/drivehelper.db"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def admin_id_list(self) -> list[int]:
        """Parsed list of admin Telegram ids (empty string -> ``[]``)."""
        return [int(part.strip()) for part in self.admin_ids.split(",") if part.strip()]

    @property
    def database_url(self) -> str:
        """Async SQLAlchemy URL for the SQLite database."""
        return f"sqlite+aiosqlite:///{self.db_path}"


@lru_cache
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance (lazy instantiation)."""
    return Settings()


def is_admin(tg_id: int, settings: Settings) -> bool:
    """Whether ``tg_id`` is an admin according to the environment (authoritative)."""
    return tg_id in settings.admin_id_list
