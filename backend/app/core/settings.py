"""Application settings, loaded from environment variables (and a local .env file).

Secrets are never hard-coded and never committed. See .env.example for the variable names.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    environment: Literal["local", "production"] = "local"

    # Supabase
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    database_url: str = ""

    # Auth: the single owner (Architecture 7)
    owner_user_id: str = ""

    # CORS: only the frontend origin(s)
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # Data providers
    api_football_key: str = ""
    football_data_org_key: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Calculation parameters (versioned file in config/)
    params_file: str = "config/params_v1.4.yaml"


@lru_cache
def get_settings() -> Settings:
    return Settings()
