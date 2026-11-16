"""raw.ingestion_runs and raw.payloads."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from app.domain.enums import SOURCE_IDS, RunStatus, SourceCode


@dataclass(frozen=True)
class StoredPayload:
    id: UUID
    is_new: bool  # False when identical content was already stored (idempotency)


def start_run(conn: Connection, source: SourceCode, job_name: str) -> UUID:
    row_id: UUID = conn.execute(
        text("insert into raw.ingestion_runs (source_id, job_name) values (:s, :j) returning id"),
        {"s": SOURCE_IDS[source], "j": job_name},
    ).scalar_one()
    return row_id


def finish_run(conn: Connection, run_id: UUID, status: RunStatus, requests_used: int,
               items_stored: int, error: str | None = None, stats: dict[str, int] | None = None) -> None:
    conn.execute(
        text("update raw.ingestion_runs set status = :st, finished_at = now(), requests_used = :r, "
             "items_stored = :i, error = :e, stats = cast(:stats as jsonb) where id = :id"),
        {"st": status.value, "r": requests_used, "i": items_stored, "e": error,
         "stats": json.dumps(stats or {}, sort_keys=True), "id": run_id},
    )


def fail_if_running(conn: Connection, run_id: UUID, error: str) -> None:
    """Mark a run FAILED if it never reached finish_run (e.g. it crashed while setting up)."""
    conn.execute(
        text("update raw.ingestion_runs set status = 'FAILED', finished_at = now(), error = :e "
             "where id = :id and status = 'RUNNING'"),
        {"e": error, "id": run_id},
    )


_RUN_COLUMNS = ("select r.id, s.code as source, r.job_name, r.status, r.started_at, r.finished_at, "
                "r.requests_used, r.items_stored, r.error, r.stats "
                "from raw.ingestion_runs r join core.sources s on s.id = r.source_id")


def list_runs(conn: Connection, source: SourceCode | None = None, status: RunStatus | None = None,
              limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(f"{_RUN_COLUMNS} where (cast(:s as smallint) is null or r.source_id = :s) "
             "and (cast(:st as text) is null or r.status = cast(:st as core.run_status)) "
             "order by r.started_at desc, r.id limit :lim"),
        {"s": SOURCE_IDS[source] if source else None, "st": status.value if status else None, "lim": limit},
    )
    return [dict(r) for r in rows.mappings()]


def get_run(conn: Connection, run_id: UUID) -> dict[str, Any] | None:
    row = conn.execute(text(f"{_RUN_COLUMNS} where r.id = :id"), {"id": run_id}).mappings().one_or_none()
    return dict(row) if row else None


def insert_payload(conn: Connection, *, run_id: UUID, source: SourceCode, endpoint: str,
                   params: dict[str, Any], http_status: int, storage_path: str, digest: str,
                   byte_size: int, fetched_at: datetime) -> StoredPayload:
    new_id = conn.execute(
        text("insert into raw.payloads (run_id, source_id, endpoint, params, http_status, storage_path, "
             "sha256, byte_size, fetched_at) values (:run, :s, :e, cast(:p as jsonb), :h, :path, :sha, :b, :f) "
             "on conflict (source_id, sha256) do nothing returning id"),
        {"run": run_id, "s": SOURCE_IDS[source], "e": endpoint, "p": json.dumps(params), "h": http_status,
         "path": storage_path, "sha": digest, "b": byte_size, "f": fetched_at},
    ).scalar_one_or_none()
    if new_id is not None:
        return StoredPayload(new_id, True)
    existing = conn.execute(
        text("select id from raw.payloads where source_id = :s and sha256 = :sha"),
        {"s": SOURCE_IDS[source], "sha": digest},
    ).scalar_one()
    return StoredPayload(existing, False)
