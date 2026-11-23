"""The worker's scheduler (APScheduler, ARCHITECTURE.md 5 / DEVELOPMENT-PLAN 5.1).

`python -m app.cli worker` starts it in the foreground. Jobs so far:

    daily_backfill   config/backfill.yaml: schedule   API-Football goal-minute backfill (docs/DAILY-BACKFILL.md)
    espn_daily       config/backfill.yaml: espn       ESPN forward fill of the last days, every ESPN league
                                                       (docs/ESPN.md); no key, no quota
    paper_cycle      config/paper.yaml: every N min   paper betting recorder (docs/PAPER-BETTING.md 8), passed in
                                                       by the caller as an IntervalJob (app.paper is a sibling layer)

Every job is coalesced (a missed run is run once, not repeatedly), never overlaps itself, and logs its
outcome; the runs it starts are stored, cross-checked and audited like any other ingestion run.
Phase 5 adds the session planner, session runs, the lineup re-check and settlement here.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.base import BaseScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import Engine

from app.core.settings import Settings
from app.domain.enums import SourceCode
from app.domain.registry import Registry
from app.ingestion import pipeline
from app.jobs.backfill import BackfillConfig, EspnDaily, run_daily_backfill
from app.storage.raw_store import RawStore

log = logging.getLogger(__name__)
MISFIRE_GRACE_S = 6 * 3600  # a worker that was down at the scheduled time still runs the job within 6 hours


def daily_backfill_job(engine: Engine, store: Callable[[], RawStore], registry: Registry, settings: Settings,
                       cfg: BackfillConfig) -> None:
    try:
        result = run_daily_backfill(engine, store, registry, settings, cfg)
        log.info("daily backfill finished: %d requests, %s", result.requests_used, result.stopped)
    except Exception:  # the scheduler must keep running; the failure is in the log and in the run rows
        log.exception("daily backfill failed")


def espn_daily_job(engine: Engine, store: Callable[[], RawStore], registry: Registry, cfg: EspnDaily) -> None:
    try:
        today = datetime.now(UTC).date()
        days = [today - timedelta(days=i) for i in range(cfg.days_back, 0, -1)]
        codes = [c.code for c in registry.competitions if SourceCode.ESPN in c.providers]
        stats = pipeline.espn_days(engine, store(), registry, codes, days, max_requests=cfg.max_requests,
                                   job_name=f"espn_days:daily:{today}")
        log.info("ESPN daily: %d scoreboards, %d summaries", stats.get("scoreboards", 0), stats.get("summaries", 0))
    except Exception:  # the scheduler must keep running; the failure is in the log and in the run row
        log.exception("ESPN daily failed")


@dataclass(frozen=True)
class IntervalJob:
    """A job run every `every_minutes`, e.g. the paper betting cycle (built by the caller: app.jobs must not
    import app.paper)."""

    id: str
    name: str
    func: Callable[[], object]
    every_minutes: int


def _guarded(job: IntervalJob) -> None:
    try:
        job.func()
    except Exception:  # the scheduler must keep running; the next interval tries again
        log.exception("%s failed", job.name)


def build_scheduler(engine: Engine, store: Callable[[], RawStore], registry: Registry, settings: Settings,
                    backfill: BackfillConfig, scheduler: BaseScheduler | None = None,
                    interval_jobs: Sequence[IntervalJob] = ()) -> BaseScheduler:
    backfill.check_registry(registry)  # a broken config fails at start, not at 00:30
    tz = ZoneInfo(backfill.schedule.timezone)
    sched = scheduler or BlockingScheduler(timezone=tz)
    hour, minute = backfill.schedule.hour_minute
    sched.add_job(daily_backfill_job, CronTrigger(hour=hour, minute=minute, timezone=tz), id="daily_backfill",
                  name="API-Football daily backfill", kwargs={"engine": engine, "store": store,
                                                               "registry": registry, "settings": settings,
                                                               "cfg": backfill},
                  coalesce=True, max_instances=1, misfire_grace_time=MISFIRE_GRACE_S, replace_existing=True)
    if backfill.espn.enabled:
        eh, em = backfill.espn.hour_minute
        sched.add_job(espn_daily_job, CronTrigger(hour=eh, minute=em, timezone=tz), id="espn_daily",
                      name="ESPN daily forward fill", kwargs={"engine": engine, "store": store, "registry": registry,
                                                              "cfg": backfill.espn},
                      coalesce=True, max_instances=1, misfire_grace_time=MISFIRE_GRACE_S, replace_existing=True)
    for job in interval_jobs:
        sched.add_job(_guarded, IntervalTrigger(minutes=job.every_minutes, timezone=tz), id=job.id, name=job.name,
                      args=[job], coalesce=True, max_instances=1, misfire_grace_time=job.every_minutes * 60,
                      replace_existing=True)
    return sched
