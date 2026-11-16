"""Application settings, loaded from environment variables and .env files.

Load order (later overrides earlier): repository-root .env, then backend/.env, then real
environment variables. Secrets are never hard-coded and never committed. See .env.example.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    environment: Literal["local", "production"] = "local"

    # Supabase project
    supabase_project_ref: str = "vhdhqpaxandnuqykjweq"
    supabase_url: str = ""
    supabase_service_role_key: SecretStr = SecretStr("")

    # Database (Supabase connection pooler). Values from Supabase dashboard -> Connect.
    database_host: str = ""  # e.g. aws-0-<region>.pooler.supabase.com
    database_port: int = 5432  # 5432 = session mode (worker), 6543 = transaction mode (API)
    database_name: str = "postgres"
    database_user: str = ""  # empty -> "postgres.<project_ref>"
    database_password: SecretStr = SecretStr("")
    database_sslmode: Literal["require", "verify-full"] = "require"
    database_statement_timeout_ms: int = 30_000
    # Full URL override for LOCAL development databases only (e.g. the Docker test database).
    # Non-TLS connections are allowed only to localhost / 127.0.0.1.
    database_url: SecretStr = SecretStr("")

    # Raw payload files: "local" (backend/<raw_store_dir>) or "supabase" (private Storage bucket)
    raw_store: Literal["local", "supabase"] = "local"
    raw_store_dir: str = "data/raw"

    # Auth: the single owner (Architecture 7)
    owner_user_id: str = ""
    # Only for projects still on the legacy shared JWT secret (HS256). New Supabase projects sign
    # with asymmetric keys, verified against the project's public JWKS - no secret needed.
    supabase_jwt_secret: SecretStr = SecretStr("")
    # LOCAL DEVELOPMENT ONLY: accept requests without a token, from this machine only, so the
    # endpoints can be tried in /docs before the frontend login exists. Refused outside "local".
    auth_local_bypass: bool = False

    # CORS: only the frontend origin(s)
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # Data providers
    api_football_key: SecretStr = SecretStr("")
    # "free": goal minutes 1 match per request; "pro" (or higher): 20 matches per request with `ids`
    api_football_plan: Literal["free", "pro"] = "free"
    # Hard cap of API-Football requests per job run (free plan: 100 per DAY in total - keep runs small)
    api_football_max_requests_per_run: int = Field(default=90, ge=1, le=75_000)
    football_data_org_key: SecretStr = SecretStr("")

    # Telegram
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: str = ""

    # Calculation parameters (versioned file in config/)
    params_file: str = "config/params_v1.5.yaml"

    @model_validator(mode="after")
    def _bypass_only_local(self) -> "Settings":
        if self.auth_local_bypass and self.environment != "local":
            raise ValueError("AUTH_LOCAL_BYPASS is allowed only when ENVIRONMENT=local")
        return self

    @property
    def resolved_database_user(self) -> str:
        return self.database_user or f"postgres.{self.supabase_project_ref}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
