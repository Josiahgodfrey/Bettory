"""Telegram bot messages (ARCHITECTURE.md 12.8, Bot API https://core.telegram.org/bots/api): sendMessage, TLS verified.

    request    POST https://api.telegram.org/bot<token>/sendMessage  {chat_id, text, link_preview_options}
               (`link_preview_options` replaced `disable_web_page_preview` in Bot API 7.0); text 1 .. 4096 chars
    response   {"ok": true, "result": Message}  or  {"ok": false, "error_code", "description",
               "parameters": {"retry_after": seconds}}  (429 flood control: wait, the outbox retries next cycle)

Connections go over IPv4: on hosts without a working IPv6 route the default dual-stack attempt times out.
The bot token is part of the request URL, so it must never reach a log line or the database: every error is
reduced to the HTTP status and Telegram's own description (or the exception's class name).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

API = "https://api.telegram.org"
TIMEOUT_S = 20.0
MAX_TEXT = 4096


@dataclass(frozen=True)
class SendResult:
    ok: bool
    error: str | None = None  # safe to store: never contains the token
    retry_after: int | None = None  # 429 flood control: seconds Telegram asks to wait


def ipv4_client() -> httpx.Client:
    return httpx.Client(transport=httpx.HTTPTransport(local_address="0.0.0.0", retries=2), timeout=TIMEOUT_S)


class Telegram:
    def __init__(self, token: str, chat_id: str, client: httpx.Client | None = None) -> None:
        if not token or not chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
        self._token, self.chat_id = token, chat_id
        self._client = client or ipv4_client()

    def _url(self, method: str) -> str:
        return f"{API}/bot{self._token}/{method}"

    def _error(self, r: httpx.Response) -> SendResult:
        try:
            body = r.json()
        except ValueError:
            body = {}
        description = str(body.get("description", ""))[:200].replace(self._token, "<token>")
        retry = (body.get("parameters") or {}).get("retry_after")
        return SendResult(False, f"HTTP {r.status_code}: {description}",
                          int(retry) if isinstance(retry, int) else None)

    def send(self, text: str) -> SendResult:
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT - 1] + "…"
        try:
            r = self._client.post(self._url("sendMessage"),
                                  json={"chat_id": self.chat_id, "text": text,
                                        "link_preview_options": {"is_disabled": True}})
        except httpx.HTTPError as exc:
            return SendResult(False, f"network error: {type(exc).__name__}")
        return SendResult(True) if r.status_code == 200 else self._error(r)

    def me(self) -> dict[str, object] | None:
        """getMe: the bot's identity (None if the token is refused or Telegram is unreachable)."""
        try:
            r = self._client.get(self._url("getMe"))
        except httpx.HTTPError:
            return None
        result = r.json().get("result") if r.status_code == 200 else None
        return result if isinstance(result, dict) else None
