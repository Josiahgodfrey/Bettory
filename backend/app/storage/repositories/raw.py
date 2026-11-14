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
               items_stored: int, error: str | None = None) -> None:
    conn.execute(
        text("update raw.ingestion_runs set status = :st, finished_at = now(), requests_used = :r, "
             "items_stored = :i, error = :e where id = :id"),
        {"st": status.value, "r": requests_used, "i": items_stored, "e": error, "id": run_id},
    )


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
