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

import httpx  # noqa: E402

from app.core.settings import get_settings  # noqa: E402 - after the environment is isolated
from app.notifications import telegram  # noqa: E402
from app.storage.db import get_engine  # noqa: E402

get_settings.cache_clear()
get_engine.cache_clear()


def _no_real_telegram() -> httpx.Client:
    """Second guard: even with a token, a test can never open a connection to Telegram (2026-11-17 a cycle test
    sent real "report ready" messages before the environment was isolated). Tests inject a MockTransport client."""
    raise RuntimeError("tests must not connect to Telegram: pass a client with a MockTransport")


telegram.ipv4_client = _no_real_telegram

