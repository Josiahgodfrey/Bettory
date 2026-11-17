"""One recorder cycle: capture -> freeze -> results -> settle (docs/PAPER-BETTING.md 8, config/paper.yaml).

Run every few minutes by the worker (app/jobs/scheduler.py) or by Windows Task Scheduler (`paper-cycle`). Every
step decides from the database alone whether it is due, so a cycle can run at any time, as often as wanted, in
any process: a step that is not due does nothing and costs nothing.

    capture   a session whose as_of is less than capture.lead_minutes away and that has no successful capture
              run in that window yet (at most capture.attempts runs): app/paper/capture.py
    freeze    a session past its as_of with upcoming fixtures not frozen yet for the live params version:
              app/paper/freeze.py. A freeze without a capture seals no odds (no paper bet) - reported
    results   at most every results.every_hours: goal minutes of the frozen fixtures still without a 15' result
    settle    every cycle, from the stored results (no request): app/paper/settle.py

All recorder runs share one daily request bound (daily_max_requests, per quota day from 00:00 UTC) on top of
the quota guard. Two cycles never overlap (a database lock across processes).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine

from app.core.settings import Settings
from app.domain.enums import FixtureStatus, RunStatus
from app.domain.params import Params
from app.domain.registry import Registry
from app.domain.sessions import SessionCalendar, SessionId
from app.ingestion import pipeline
from app.paper.capture import RECORDER_JOB_PREFIX, capture_job, capture_session
from app.paper.config import PaperConfig
from app.paper.freeze import MIN_LEAD, Provenance, due_sessions, freeze_session
from app.paper.settle import SETTLE_AFTER, settle_due
from app.storage.raw_store import RawStore
from app.storage.repositories import ops as ops_repo
from app.storage.repositories import paper as paper_repo

log = logging.getLogger(__name__)
CYCLE_LOCK = "paper_cycle"
RESULTS_JOB = "paper_results:"


@dataclass
class CycleResult:
    lines: list[str] = field(default_factory=list)  # what was done (or, in a dry run, would be done)
    errors: int = 0
    busy: bool = False  # another cycle was running: nothing done

    def note(self, line: str) -> None:
        self.lines.append(line)
        log.info("paper cycle: %s", line)

    def fail(self, line: str) -> None:
        self.errors += 1
        self.lines.append("! " + line)
        log.error("paper cycle: %s", line)


@dataclass(frozen=True)
class CycleInputs:
    """What a cycle needs besides the clock; built fresh for every cycle by the caller."""

    engine: Engine
    store: Callable[[], RawStore]
    registry: Registry
    settings: Settings
    params: Params
    cfg: PaperConfig
    competitions: Sequence[str]
    provenance: Provenance
    stake: float


def _quota_day(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def run_cycle(x: CycleInputs, *, now: datetime, dry_run: bool = False) -> CycleResult:
    out = CycleResult()
    with ops_repo.exclusive(x.engine, CYCLE_LOCK) as got:
        if not got:
            out.busy = True
            out.note("another cycle is running: nothing done")
            return out
        _Cycle(x, now, dry_run, out).run()
    return out


class _Cycle:
    def __init__(self, x: CycleInputs, now: datetime, dry_run: bool, out: CycleResult) -> None:
        self.x, self.now, self.dry_run, self.out = x, now, dry_run, out
        s = x.settings
        self.plan, self.key, self.rpm = s.api_football_plan, s.api_football_key.get_secret_value(), \
            s.api_football_requests_per_minute
        self.codes = tuple(sorted(x.competitions))

    def left_today(self) -> int:
        spent = ops_repo.requests_since(self.x.engine, RECORDER_JOB_PREFIX, _quota_day(self.now))
        return max(0, self.x.cfg.daily_max_requests.for_plan(self.plan) - spent)

    def run(self) -> None:
        cal = SessionCalendar(self.x.params)
        for sid in due_sessions(cal, self.now):
            self.session(cal, sid)
        self.results()
        if not self.dry_run:
            self.settle()

    def session(self, cal: SessionCalendar, sid: SessionId) -> None:
        x, now, out = self.x, self.now, self.out
        times = cal.times(sid)
        with x.engine.connect() as conn:
            fixtures = paper_repo.session_fixtures(conn, times.start_utc, times.end_utc, self.codes)
            done = paper_repo.frozen_match_ids(conn, str(sid), x.params.params_version)
        if not fixtures:
            return
        as_of = cal.run_start(sid, fixtures[0].kickoff_utc)
        window = as_of - x.cfg.capture.lead
        runs = ops_repo.run_statuses_since(x.engine, capture_job(sid), window)
        captured = RunStatus.SUCCEEDED in runs

        if window <= now < as_of and not captured and len(runs) < x.cfg.capture.attempts:
            budget = min(x.cfg.capture.max_requests.for_plan(self.plan), self.left_today())
            if not self.key:
                out.fail(f"{sid} capture due but API_FOOTBALL_KEY is not configured")
            elif budget < 2:
                out.fail(f"{sid} capture due but the recorder's daily request bound is used up")
            elif self.dry_run:
                out.note(f"{sid} would capture now (as_of {as_of:%H:%M}Z, up to {budget} requests)")
            else:
                try:
                    c = capture_session(x.engine, x.store, x.registry, self.key, x.params, sid,
                                        self.codes, now=now, max_requests=budget, requests_per_minute=self.rpm)
                    captured = c.status == "CAPTURED"
                    out.note(f"{sid} capture {c.status}: {c.stats.get('odds_requests', 0)} odds requests for "
                             f"{c.stats.get('session_fixtures', 0)} fixtures")
                except Exception as exc:  # stored as a FAILED run; retried by the next cycle within `attempts`
                    out.fail(f"{sid} capture failed: {type(exc).__name__}: {exc}")

        todo = [f for f in fixtures if f.match_id not in done and f.status is FixtureStatus.SCHEDULED
                and f.kickoff_utc >= now + MIN_LEAD]
        if now < as_of or not todo:
            return
        warn = "" if captured else " - no capture before as_of: no odds sealed, no paper bets"
        if self.dry_run:
            out.note(f"{sid} would freeze {len(todo)} fixtures now (as_of {as_of:%H:%M}Z){warn}")
            return
        try:
            r = freeze_session(x.engine, x.params, x.registry, sid, now=now,
                               competitions=self.codes, provenance=x.provenance, stake=x.stake)
            out.note(f"{sid} freeze {r.status}: new {r.frozen}, listed {r.qualified}, paper bets {r.bets}"
                     + (f", chain {r.chain_from}..{r.chain_to}" if r.chain_from is not None else "") + warn)
        except Exception as exc:
            out.fail(f"{sid} freeze failed: {type(exc).__name__}: {exc}")

    def results(self) -> None:
        x, now, out, cfg = self.x, self.now, self.out, self.x.cfg.results
        if not self.key or ops_repo.run_statuses_since(x.engine, RESULTS_JOB, now - timedelta(hours=cfg.every_hours)):
            return
        with x.engine.connect() as conn:
            wanted = paper_repo.results_wanted(conn, now - timedelta(hours=cfg.lookback_hours), now - SETTLE_AFTER)
        if not wanted:
            return
        budget = min(cfg.max_requests.for_plan(self.plan), self.left_today())
        if budget < 1:
            out.fail(f"results of {len(wanted)} fixtures due but the recorder's daily request bound is used up")
        elif self.dry_run:
            out.note(f"would fetch the results of {len(wanted)} fixtures now (up to {budget} requests)")
        else:
            try:
                st = pipeline.api_football_results(x.engine, x.store(), x.registry, self.key, wanted,
                                                   self.plan == "pro", budget, requests_per_minute=self.rpm,
                                                   job_name=f"{RESULTS_JOB}{now.astimezone(UTC):%Y-%m-%dT%H:%M}")
                out.note(f"results: {st.get('fixtures_requested', 0)} of {len(wanted)} fixtures requested")
            except Exception as exc:
                out.fail(f"results failed: {type(exc).__name__}: {exc}")

    def settle(self) -> None:
        try:
            s = settle_due(self.x.engine, self.now)
        except Exception as exc:
            self.out.fail(f"settle failed: {type(exc).__name__}: {exc}")
            return
        if s.settled or s.corrected:
            self.out.note(f"settle: {s.settled} settled, {s.corrected} corrected, {s.pending} still pending")
        for line in s.inconsistent:
            self.out.fail(f"inconsistent stored result, not settled: {line}")
