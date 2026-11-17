"""Telegram bot messages (ARCHITECTURE.md 12.8): one sendMessage call per message, TLS verified.

The bot token is part of the request URL, so it must never reach a log line or the database: every error is
reduced to the HTTP status and Telegram's own description (or the exception's class name).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

API = "https://api.telegram.org"
TIMEOUT_S = 10.0


@dataclass(frozen=True)
class SendResult:
    ok: bool
    error: str | None = None  # safe to store: never contains the token


class Telegram:
    def __init__(self, token: str, chat_id: str, client: httpx.Client | None = None) -> None:
        if not token or not chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
        self._token, self.chat_id = token, chat_id
        self._client = client or httpx.Client(timeout=TIMEOUT_S)

    def send(self, text: str) -> SendResult:
        try:
            r = self._client.post(f"{API}/bot{self._token}/sendMessage",
                                  json={"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True})
        except httpx.HTTPError as exc:
            return SendResult(False, f"network error: {type(exc).__name__}")
        if r.status_code == 200:
            return SendResult(True)
        try:
            description = str(r.json().get("description", ""))[:200]
        except ValueError:
            description = ""
        return SendResult(False, f"HTTP {r.status_code}: {description}".replace(self._token, "<token>"))
