"""Test isolation from the real services configured in BETTORY/.env.

Environment variables take priority over the .env files, so blanking them here (before any test module builds the
settings) guarantees that no test reaches the production database (Supabase), Telegram, API-Football,
football-data.org or Supabase Storage. A test that needs a database uses TEST_DATABASE_URL (a local Docker
database, tests/integration/conftest.py); the default engine has no host and fails closed.
"""

from __future__ import annotations

import os

ISOLATED = {
    "DATABASE_URL": "",
    "DATABASE_HOST": "",
    "DATABASE_PASSWORD": "",
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_CHAT_ID": "",
    "API_FOOTBALL_KEY": "",
    "FOOTBALL_DATA_ORG_KEY": "",
    "SUPABASE_SERVICE_ROLE_KEY": "",
    "RAW_STORE": "local",
}
os.environ.update(ISOLATED)

from app.core.settings import get_settings  # noqa: E402 - after the environment is isolated
from app.storage.db import get_engine  # noqa: E402

get_settings.cache_clear()
get_engine.cache_clear()

