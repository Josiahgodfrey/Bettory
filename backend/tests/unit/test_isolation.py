"""The test run cannot reach the real services configured in BETTORY/.env (tests/conftest.py)."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.core.settings import Settings, get_settings
from app.notifications import outbox
from app.notifications.telegram import Telegram
from app.storage.db import DatabaseConfigError, get_engine


def test_no_real_services_in_tests() -> None:
    s = get_settings()
    assert not s.telegram_bot_token.get_secret_value() and not s.telegram_chat_id
    assert outbox.sender(s) is None                                   # Telegram: nothing can be sent
    assert not s.database_host and not s.database_url.get_secret_value()
    assert not s.api_football_key.get_secret_value() and s.raw_store == "local"
    with pytest.raises(DatabaseConfigError):                          # no default database: fails closed
        get_engine()


def test_settings_built_in_a_test_do_not_read_the_real_telegram() -> None:
    """Tests build Settings(...) directly (they read .env); the blanked environment variables win."""
    s = Settings(api_football_key=SecretStr("key"))
    assert outbox.sender(s) is None


def test_no_connection_to_telegram_even_with_a_token() -> None:
    with pytest.raises(RuntimeError, match="must not connect to Telegram"):
        Telegram("token", "chat")
