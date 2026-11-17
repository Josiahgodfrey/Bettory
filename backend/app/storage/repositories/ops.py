"""Operations: cross-process locks and run history for scheduled jobs."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, text

from app.domain.enums import RunStatus


def _lock_key(name: str) -> int:
    """Stable signed 64-bit key for pg advisory locks (Python's hash() changes between processes)."""
    return int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)


@contextmanager
def exclusive(engine: Engine, name: str) -> Iterator[bool]:
    """Hold a Postgres session advisory lock named `name` while the block runs; yields False (immediately)
    if another process holds it. Works across the API process and the worker, unlike an in-process lock."""
    with engine.connect() as conn:
        got = bool(conn.execute(text("select pg_try_advisory_lock(:k)"), {"k": _lock_key(name)}).scalar_one())
        conn.commit()
        try:
            yield got
        finally:
            if got:
                conn.execute(text("select pg_advisory_unlock(:k)"), {"k": _lock_key(name)})
                conn.commit()


def latest_run(engine: Engine, job_name: str, status: RunStatus | None = None) -> dict[str, Any] | None:
    """The newest ingestion run with this job name (optionally only with this status)."""
    with engine.connect() as conn:
        row = conn.execute(text("""
            select id, status, started_at, finished_at, requests_used, stats from raw.ingestion_runs
            where job_name = :j and (cast(:st as text) is null or status = cast(:st as core.run_status))
            order by started_at desc, id limit 1"""),
            {"j": job_name, "st": status.value if status else None}).mappings().one_or_none()
    return dict(row) if row else None


def requests_since(engine: Engine, job_prefix: str, since: datetime) -> int:
    """API requests spent by runs whose job name starts with `job_prefix`, started at or after `since`."""
    with engine.connect() as conn:
        return int(conn.execute(text(
            "select coalesce(sum(requests_used), 0) from raw.ingestion_runs "
            "where starts_with(job_name, :p) and started_at >= :s"), {"p": job_prefix, "s": since}).scalar_one())


def run_statuses_since(engine: Engine, job_prefix: str, since: datetime) -> list[RunStatus]:
    """Statuses of the runs whose job name starts with `job_prefix`, started at or after `since`, oldest first."""
    with engine.connect() as conn:
        return [RunStatus(r[0]) for r in conn.execute(text(
            "select status::text from raw.ingestion_runs where starts_with(job_name, :p) and started_at >= :s "
            "order by started_at"), {"p": job_prefix, "s": since})]
