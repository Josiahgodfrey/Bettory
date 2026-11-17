"""Telegram: the client never exposes the bot token in an error; the message texts (summary and link only)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.notifications import messages
from app.notifications.telegram import Telegram

TOKEN = "123456:SECRET-token"
EAT = ZoneInfo("Africa/Nairobi")


def client(handler: httpx.MockTransport) -> Telegram:
    return Telegram(TOKEN, "42", httpx.Client(transport=handler))


def test_send_ok_and_errors_without_the_token() -> None:
    seen: list[httpx.Request] = []

    def ok(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    assert client(httpx.MockTransport(ok)).send("hello").ok
    body = json.loads(seen[0].content)
    assert body == {"chat_id": "42", "text": "hello", "disable_web_page_preview": True}
    assert seen[0].url.scheme == "https" and seen[0].url.host == "api.telegram.org"

    def unauthorized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "description": f"Unauthorized {TOKEN}"})

    r = client(httpx.MockTransport(unauthorized)).send("x")
    assert not r.ok and r.error is not None and "401" in r.error and TOKEN not in r.error

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    r = client(httpx.MockTransport(down)).send("x")
    assert not r.ok and r.error == "network error: ConnectError"
    with pytest.raises(ValueError):
        Telegram("", "42")


def test_message_texts() -> None:
    ko = datetime(2026, 11, 21, 12, 30, tzinfo=UTC)                         # 15:30 EAT
    text = messages.report_ready("2026-11-21-NOON", 3, ko, EAT, "https://app.example/")
    assert text == ("☀️ Noon session 21 Nov — 3 matches verified (first kick-off 15:30 EAT). "
                    "Open: https://app.example/bets?session=2026-11-21-NOON")
    none = messages.report_ready("2026-11-22-NIGHT", 0, ko, EAT, "x")
    assert none == "Night session 22 Nov — 0 matches passed verification."
    w = messages.withdrawn("Home", "Away", ko, ["CONTEXT_ODDS_MOVEMENT"], EAT)
    assert w == "⚠️ Withdrawn: Home vs Away (15:30 EAT) — reason: CONTEXT_ODDS_MOVEMENT. Do not bet it."
    assert "15:30 EAT" in messages.login(ko, EAT) and messages.failure("x" * 500).endswith("x" * 10)
