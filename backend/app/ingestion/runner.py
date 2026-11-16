"""Start ingestion jobs in the background: the API answers at once with the run id.

- At most one job per source at a time in this process (two jobs would share one request quota).
- The raw.ingestion_runs row is created before the job starts, so the run is visible immediately.
- If a job crashes before the pipeline could record the outcome, the run is marked FAILED here,
  so a run never stays RUNNING because of an error.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from uuid import UUID

from sqlalchemy import Engine

from app.domain.enums import SourceCode
from app.storage.repositories import raw as raw_repo

log = logging.getLogger(__name__)

Work = Callable[[UUID], object]
Schedule = Callable[..., None]  # e.g. fastapi.BackgroundTasks.add_task

_locks: dict[SourceCode, threading.Lock] = {s: threading.Lock() for s in SourceCode}


class SourceBusyError(RuntimeError):
    pass


def is_busy(source: SourceCode) -> bool:
    return _locks[source].locked()


def submit(engine: Engine, source: SourceCode, job_name: str, work: Work, schedule: Schedule) -> UUID:
    """Create the run row, then schedule `work(run_id)`. Raises SourceBusyError if a job is running."""
    lock = _locks[source]
    if not lock.acquire(blocking=False):
        raise SourceBusyError(f"a {source.value} job is already running")
    try:
        with engine.begin() as conn:
            run_id = raw_repo.start_run(conn, source, job_name)
        schedule(_execute, engine, lock, run_id, work)
    except BaseException:
        lock.release()
        raise
    return run_id


def _execute(engine: Engine, lock: threading.Lock, run_id: UUID, work: Work) -> None:
    try:
        work(run_id)
    except Exception as exc:  # the outcome is recorded in the run row; nothing to re-raise to
        log.exception("ingestion run %s failed", run_id)
        with engine.begin() as conn:
            raw_repo.fail_if_running(conn, run_id, repr(exc)[:2000])
    finally:
        lock.release()
