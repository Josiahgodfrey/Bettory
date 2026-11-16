"""The worker's scheduler (APScheduler, ARCHITECTURE.md 5 / DEVELOPMENT-PLAN 5.1).

`python -m app.cli worker` starts it in the foreground. Jobs so far:

    daily_backfill   config/backfill.yaml: schedule   API-Football goal-minute backfill (docs/DAILY-BACKFILL.md)

Every job is coalesced (a missed run is run once, not repeatedly), never overlaps itself, and logs its
outcome; the runs it starts are stored, cross-checked and audited like any other ingestion run.
Phase 5 adds the session planner, session runs, the lineup re-check and settlement here.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.base import BaseScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import Engine

from app.core.settings import Settings
from app.domain.registry import Registry
from app.jobs.backfill import BackfillConfig, run_daily_backfill
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


def build_scheduler(engine: Engine, store: Callable[[], RawStore], registry: Registry, settings: Settings,
                    backfill: BackfillConfig, scheduler: BaseScheduler | None = None) -> BaseScheduler:
    backfill.check_registry(registry)  # a broken config fails at start, not at 00:30
    tz = ZoneInfo(backfill.schedule.timezone)
    sched = scheduler or BlockingScheduler(timezone=tz)
    hour, minute = backfill.schedule.hour_minute
    sched.add_job(daily_backfill_job, CronTrigger(hour=hour, minute=minute, timezone=tz), id="daily_backfill",
                  name="API-Football daily backfill", kwargs={"engine": engine, "store": store,
                                                               "registry": registry, "settings": settings,
                                                               "cfg": backfill},
                  coalesce=True, max_instances=1, misfire_grace_time=MISFIRE_GRACE_S, replace_existing=True)
    return sched
