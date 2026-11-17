"""Capture a session's odds before its data cut-off, so the freeze can seal them (docs/PAPER-BETTING.md §5).

    capture_session   fixtures of the dates the session covers + the odds of its upcoming fixtures in the paper
                      competitions, stored in core.odds_snapshots (append-only) by the ordinary ingestion run
    then              freeze_session at or after as_of seals every price captured before as_of with the prediction

Order matters: capture BEFORE as_of. Prices captured later are kept (core.odds_snapshots) but are never sealed with
that session's predictions - a later price is information the analysis did not have.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine

from app.domain.params import Params
from app.domain.registry import Registry
from app.domain.sessions import SessionCalendar, SessionId
from app.ingestion import pipeline
from app.storage.raw_store import RawStore
from app.storage.repositories import paper as paper_repo


@dataclass(frozen=True)
class CaptureResult:
    session: SessionId
    status: str  # CAPTURED / LATE (after as_of: not sealed for this session) / ENDED / STARTED
    as_of: datetime | None
    stats: dict[str, Any]


def capture_session(engine: Engine, store: Callable[[], RawStore], registry: Registry, api_key: str, params: Params,
                    sid: SessionId, competitions: Sequence[str], *, now: datetime, max_requests: int,
                    requests_per_minute: int) -> CaptureResult:
    cal = SessionCalendar(params)
    times = cal.times(sid)
    if times.end_utc <= now:
        return CaptureResult(sid, "ENDED", None, {})
    start = max(times.start_utc, now)
    stats = pipeline.api_football_session_odds(engine, store(), registry, api_key, start, times.end_utc,
                                               competitions, max_requests, requests_per_minute=requests_per_minute)
    with engine.connect() as conn:
        fixtures = paper_repo.session_fixtures(conn, times.start_utc, times.end_utc, competitions)
    as_of = cal.run_start(sid, fixtures[0].kickoff_utc) if fixtures else None
    status = "CAPTURED" if as_of is None or now < as_of else "LATE"
    return CaptureResult(sid, status, as_of, dict(stats))
