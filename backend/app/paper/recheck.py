"""The re-check of listed matches about an hour before kick-off (ARCHITECTURE.md 12.7, docs/PAPER-BETTING.md 10).

Owner decision 2026-11-17: fetch the announced lineups and the current odds, then run the FULL verification again
with everything known at that moment (as_of = now). A match that no longer qualifies is WITHDRAWN (one-way:
decision/ranking.py `recheck`); its paper bet is settled VOID. Lineups are stored and shown (CONFIRMED once both
are announced); there is no lineup-specific rule until one is defined and tested.

    due           listed (QUALIFIED) predictions kicking off recheck.from .. to minutes from now, no final re-check
                  (CONFIRMED / WITHDRAWN) yet, fewer than `attempts` re-checks, the last one >= retry_minutes ago
    fetch         lineups + odds of the due fixtures (2 requests each), one ingestion run
    verify        AnalysisContext(now).evaluate -> recheck(QUALIFIED, verdict) -> CONFIRMED / LINEUPS_PENDING /
                  WITHDRAWN, stored in paper.rechecks (append-only, tied to the sealed prediction)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine

from app.analysis.builder import AnalysisContext
from app.decision.ranking import recheck
from app.domain.enums import Decision
from app.domain.params import Params
from app.domain.registry import Registry
from app.ingestion import pipeline
from app.paper.config import Recheck
from app.paper.freeze import checks_json
from app.storage.raw_store import RawStore
from app.storage.repositories import paper as paper_repo

RECHECK_JOB = "paper_recheck:"


@dataclass(frozen=True)
class Rechecked:
    prediction_id: str
    match_id: str
    session_id: str
    kickoff_utc: datetime
    status: str  # CONFIRMED / LINEUPS_PENDING / WITHDRAWN
    failed_checks: tuple[str, ...]


@dataclass
class RecheckRun:
    due: int = 0
    fetched: int = 0
    fetch_error: str | None = None
    results: list[Rechecked] = field(default_factory=list)


def due(engine: Engine, params: Params, cfg: Recheck, now: datetime) -> list[paper_repo.RecheckCandidate]:
    with engine.connect() as conn:
        cands = paper_repo.recheck_candidates(conn, params.params_version, now + timedelta(minutes=cfg.from_minutes),
                                              now + timedelta(minutes=cfg.to_minutes))
    retry_s = cfg.retry_minutes * 60
    return [c for c in cands if c.attempts < cfg.attempts
            and (c.seconds_since_last is None or c.seconds_since_last >= retry_s)]


def run_rechecks(engine: Engine, store: Callable[[], RawStore], registry: Registry, params: Params, cfg: Recheck,
                 *, now: datetime, api_key: str, max_requests: int, requests_per_minute: int) -> RecheckRun:
    run = RecheckRun()
    todo = due(engine, params, cfg, now)
    run.due = len(todo)
    if not todo:
        return run
    keys = [c.provider_key for c in todo if c.provider_key]
    if api_key and keys and max_requests >= 2:
        try:
            st = pipeline.api_football_recheck(engine, store(), registry, api_key, keys, max_requests,
                                               requests_per_minute=requests_per_minute,
                                               job_name=f"{RECHECK_JOB}{now.astimezone(UTC):%Y-%m-%dT%H:%M}")
            run.fetched = int(st.get("fixtures_requested", 0))
        except Exception as exc:  # verify with what is known; the failed run is in the run history
            run.fetch_error = f"{type(exc).__name__}: {exc}"
    for c in todo:
        with engine.begin() as conn:
            _, v = AnalysisContext(conn, params, registry, now).evaluate(c.match_id)
            withdrawn = recheck(Decision.QUALIFIED, v) is Decision.WITHDRAWN
            home, away = paper_repo.lineups_stored(conn, c.match_id)
            status = "WITHDRAWN" if withdrawn else "CONFIRMED" if home and away else "LINEUPS_PENDING"
            failed = tuple(ch.code for ch in v.failed)
            paper_repo.insert_recheck(conn, paper_repo.NewRecheck(
                prediction_id=c.prediction_id, prediction_hash=c.prediction_hash, status=status, lineups_home=home,
                lineups_away=away, decision=v.decision.value, failed_checks=failed, checks=checks_json(v),
                p_under05=v.p_under05, p_draw=v.p_draw, min_odds_under05=v.min_odds_under05,
                min_odds_draw=v.min_odds_draw))
        run.results.append(Rechecked(str(c.prediction_id), str(c.match_id), c.session_id, c.kickoff_utc, status,
                                     failed))
    return run
