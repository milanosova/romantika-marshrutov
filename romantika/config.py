"""Application settings, read from the environment only (never from code)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _data_dir() -> Path:
    """Season and tzolkin content: `data/` of a checkout, `romantika/data/` of an installed wheel."""
    from_repository = PROJECT_ROOT / "data"
    if from_repository.is_dir():
        return from_repository
    return Path(__file__).resolve().parent / "data"


DATA_DIR = _data_dir()


class Settings(BaseSettings):
    """Runtime configuration. Secrets come from `.env` in deployment, never committed."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    bot_token: str = ""
    admin_ids: Annotated[tuple[int, ...], NoDecode] = ()
    database_url: str = "postgresql+asyncpg://romantika:romantika@127.0.0.1:5432/romantika"
    # Required on purpose: a default would silently point inside site-packages in a
    # non-editable install, and participant media would land there instead of on a volume.
    media_dir: Path = Field(description="MEDIA_DIR: directory holding participant media (required).")
    public_base_url: str = "http://127.0.0.1:8010"
    backups_dir: Path = Path("/backups")
    admin_chat_id: int | None = None
    bot_username: str = ""
    # aiohttp ignores HTTPS_PROXY; the bot/worker pass this explicitly (TELEGRAM_PROXY or HTTPS_PROXY).
    telegram_proxy: str = Field(default="", validation_alias=AliasChoices("TELEGRAM_PROXY", "HTTPS_PROXY"))
    #: Bot API base for the local stand (`python -m romantika.ops.fake_telegram`); empty = api.telegram.org.
    telegram_api_base: str = ""
    channel_url: str = ""
    log_level: str = "INFO"
    env: str = "dev"
    dev_auth_user_id: int | None = None

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: object) -> object:
        """`ADMIN_IDS` is a comma-separated list of Telegram ids."""
        if isinstance(value, str):
            return tuple(int(part) for part in value.replace(";", ",").split(",") if part.strip())
        return value

    @field_validator("media_dir", mode="before")
    @classmethod
    def _require_media_dir(cls, value: object) -> object:
        """An empty `MEDIA_DIR` is as wrong as a missing one, and fails just as loudly."""
        if isinstance(value, str) and not value.strip():
            raise ValueError("MEDIA_DIR must be set to the directory holding participant media")
        return value

    @field_validator("admin_chat_id", "dev_auth_user_id", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids

    @property
    def admin_chat(self) -> int | None:
        """Where participants' reports and notes are copied: ADMIN_CHAT_ID, else the first admin."""
        return self.admin_chat_id or (self.admin_ids[0] if self.admin_ids else None)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings instance for entrypoints (tests build `Settings()` directly)."""
    return Settings()
