"""The test run cannot reach the real services configured in BETTORY/.env (tests/conftest.py)."""

from __future__ import annotations

import pytest

from app.core.settings import get_settings
from app.notifications import outbox
from app.storage.db import DatabaseConfigError, get_engine


def test_no_real_services_in_tests() -> None:
    s = get_settings()
    assert not s.telegram_bot_token.get_secret_value() and not s.telegram_chat_id
    assert outbox.sender(s) is None                                   # Telegram: nothing can be sent
    assert not s.database_host and not s.database_url.get_secret_value()
    assert not s.api_football_key.get_secret_value() and s.raw_store == "local"
    with pytest.raises(DatabaseConfigError):                          # no default database: fails closed
        get_engine()
