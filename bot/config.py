"""Application configuration via pydantic-settings.

Settings are instantiated lazily through :func:`get_settings` so that merely
importing modules never triggers validation (e.g. a missing ``BOT_TOKEN`` must
not crash an import smoke test).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache

from pydantic import field_validator
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

    # Bounds and granularity of the candidate slot start-times offered in the
    # button-based time picker. Times are local (school-TZ) ``HH:MM`` strings.
    slot_time_start: str = "07:00"
    slot_time_end: str = "21:00"
    slot_time_step_min: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("admin_ids")
    @classmethod
    def _validate_admin_ids(cls, value: str) -> str:
        """Fail fast at startup if ADMIN_IDS holds a non-numeric token.

        Guarantees ``admin_id_list`` can never raise ``ValueError`` deep inside
        per-update request handling (e.g. someone puts @usernames in ADMIN_IDS).
        """
        for part in value.split(","):
            token = part.strip()
            if not token:
                continue
            try:
                int(token)
            except ValueError:
                raise ValueError(
                    f"ADMIN_IDS должен содержать числовые Telegram id через запятую, "
                    f"а не @username; некорректное значение: «{token}»"
                ) from None
        return value

    @field_validator("slot_time_start", "slot_time_end")
    @classmethod
    def _validate_slot_time(cls, value: str) -> str:
        """Fail fast at startup if a slot-time bound is not ``HH:MM``."""
        try:
            datetime.strptime(value.strip(), "%H:%M")
        except ValueError:
            raise ValueError(
                f"Время слота должно быть в формате ЧЧ:ММ, а не «{value}»"
            ) from None
        return value.strip()

    @field_validator("slot_time_step_min")
    @classmethod
    def _validate_slot_step(cls, value: int) -> int:
        """The picker step must be a positive number of minutes."""
        if value <= 0:
            raise ValueError("SLOT_TIME_STEP_MIN должен быть положительным числом")
        return value

    def candidate_slot_times(self) -> list[str]:
        """Local ``HH:MM`` start-times from ``[start, end]`` inclusive by step.

        Backs the button-based time picker. Validators guarantee the bounds
        parse and the step is positive, so this never raises at runtime.
        """
        start = datetime.strptime(self.slot_time_start, "%H:%M")
        end = datetime.strptime(self.slot_time_end, "%H:%M")
        step = timedelta(minutes=self.slot_time_step_min)
        times: list[str] = []
        current = start
        while current <= end:
            times.append(current.strftime("%H:%M"))
            current += step
        return times

    @property
    def admin_id_list(self) -> list[int]:
        """Parsed list of admin Telegram ids (empty string -> ``[]``).

        Input is guaranteed numeric by the ``admin_ids`` validator above.
        """
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
