"""The Telegram outbox (ops.notifications): queued once per event key, delivered by the recorder cycle."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text


def enqueue(conn: Connection, event_key: str, kind: str, message: str) -> bool:
    """True if queued now, False if this event was queued before (each event is sent once)."""
    return conn.execute(text("""
        insert into ops.notifications (event_key, kind, message) values (:k, :t, :m)
        on conflict (event_key) do nothing returning id"""), {"k": event_key, "t": kind, "m": message}
    ).first() is not None


def expire_older_than(conn: Connection, hours: int) -> int:
    """Expire messages still pending after `hours` (database clock): better unsent than sent a day late."""
    return conn.execute(text("update ops.notifications set status = 'EXPIRED' where status = 'PENDING' "
                             "and created_at < clock_timestamp() - make_interval(hours => :h)"), {"h": hours}).rowcount


def pending(conn: Connection, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(text("select id, kind, message, attempts from ops.notifications where status = 'PENDING' "
                             "order by created_at limit :n"), {"n": limit}).mappings().all()
    return [dict(r) for r in rows]


def mark(conn: Connection, notification_id: UUID, *, sent: bool, error: str | None, max_attempts: int) -> None:
    conn.execute(text("""
        update ops.notifications set attempts = attempts + 1, last_error = :e,
               sent_at = case when :ok then clock_timestamp() else null end,
               status = case when :ok then 'SENT' when attempts + 1 >= :max then 'FAILED' else 'PENDING' end
        where id = :i"""), {"i": notification_id, "ok": sent, "e": error, "max": max_attempts})


def recent(conn: Connection, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(text("""
        select id, event_key, kind, message, created_at, status, attempts, sent_at, last_error
        from ops.notifications order by created_at desc limit :n"""), {"n": limit}).mappings().all()
    return [dict(r) for r in rows]


def counts(conn: Connection) -> dict[str, int]:
    return {r[0]: int(r[1]) for r in conn.execute(text(
        "select status, count(*) from ops.notifications group by status"))}


def queued_within(conn: Connection, key_prefix: str, minutes: int) -> bool:
    """An event whose key starts with `key_prefix` was queued in the last `minutes` (database clock)."""
    return conn.execute(text(
        "select 1 from ops.notifications where starts_with(event_key, :p) "
        "and created_at > clock_timestamp() - make_interval(mins => :m) limit 1"),
        {"p": key_prefix, "m": minutes}).first() is not None
