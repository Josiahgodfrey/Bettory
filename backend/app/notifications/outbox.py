"""Queue and deliver Telegram messages (ARCHITECTURE.md 12.8).

    notify     queue a message once under a stable event key (a repeated event is ignored)
    deliver    send what is queued: up to 3 attempts each; messages older than a day are expired, not sent late.
               Without TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID nothing is sent and the messages wait (a day).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Engine

from app.core.settings import Settings
from app.notifications.telegram import SendResult, Telegram
from app.storage.repositories import notifications as repo

MAX_ATTEMPTS = 3
EXPIRE_AFTER_HOURS = 24
BATCH = 20

Sender = Callable[[str], SendResult]


@dataclass(frozen=True)
class Delivery:
    configured: bool
    sent: int = 0
    failed: int = 0
    expired: int = 0
    waiting: int = 0


def configured(settings: Settings) -> bool:
    return bool(settings.telegram_bot_token.get_secret_value() and settings.telegram_chat_id)


def sender(settings: Settings) -> Sender | None:
    if not configured(settings):
        return None
    return Telegram(settings.telegram_bot_token.get_secret_value(), settings.telegram_chat_id).send


def notify(engine: Engine, event_key: str, kind: str, message: str) -> bool:
    with engine.begin() as conn:
        return repo.enqueue(conn, event_key, kind, message)


def notify_at_most_hourly(engine: Engine, event_prefix: str, kind: str, message: str, stamp: str) -> bool:
    """Queue unless the same event (same prefix) was queued in the last hour: a failure that repeats every cycle
    reaches the owner at most once an hour."""
    with engine.begin() as conn:
        if repo.queued_within(conn, event_prefix, 60):
            return False
        return repo.enqueue(conn, f"{event_prefix}{stamp}", kind, message)


def deliver(engine: Engine, send: Sender | None) -> Delivery:
    with engine.begin() as conn:
        expired = repo.expire_older_than(conn, EXPIRE_AFTER_HOURS)
        todo = repo.pending(conn, BATCH)
    if send is None:
        return Delivery(False, expired=expired, waiting=len(todo))
    sent = failed = 0
    for n in todo:
        r = send(n["message"])
        with engine.begin() as conn:
            repo.mark(conn, n["id"], sent=r.ok, error=r.error, max_attempts=MAX_ATTEMPTS)
        sent += r.ok
        failed += not r.ok
    return Delivery(True, sent, failed, expired)
