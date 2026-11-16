"""Automatic daily API-Football goal-minute backfill (docs/DAILY-BACKFILL.md).

Once a day (config/backfill.yaml: schedule) the worker works through the configured targets in order:

    for each target (competition, season, mode):
        finished?   its last SUCCEEDED run left no fixture without goal minutes  -> skip, costs nothing
        otherwise   pipeline.api_football_backfill with what is left of today's budget
                    (history mode: only fixtures whose 15' result is unknown)
        stop when   the daily budget or the quota is used up, or a run stopped at its budget

Every run is an ordinary ingestion run: cross-checked, audited and visible in /data-health like a manual one.
The quota guard (config/data_quality.yaml) and the cross-process lock still apply to each run.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import Engine

from app.core.settings import Settings
from app.domain.enums import RunStatus, SourceCode
from app.domain.params import CONFIG_DIR
from app.domain.registry import Registry
from app.ingestion import pipeline
from app.storage.raw_store import RawStore
from app.storage.repositories import ops as ops_repo

log = logging.getLogger(__name__)


class Mode(StrEnum):
    HISTORY = "history"  # only fixtures whose 15' result is unknown
    CROSSCHECK = "crosscheck"  # every finished fixture: a second goal-minute source for the cross-check
    CURRENT = "current"  # the season being played: never finished, new results every day; paid plan only
                         # (the free plan cannot list the current season)


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Target(_Strict):
    competition: str = Field(pattern=r"^[A-Z]{3}\d$")
    season: str = Field(pattern=r"^\d{4}(-\d{2})?$")
    mode: Mode

    @property
    def job_name(self) -> str:  # the pipeline's run name: manual runs of the same target count too
        return f"api_football_backfill:{self.competition}:{self.season}"


class Schedule(_Strict):
    timezone: str
    time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    @field_validator("timezone")
    @classmethod
    def _zone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {v!r}") from exc
        return v

    @property
    def hour_minute(self) -> tuple[int, int]:
        h, m = (int(x) for x in self.time.split(":"))
        return h, m


class DailyMax(_Strict):
    """Upper bound of requests per day, by the plan in API_FOOTBALL_PLAN (at least listing + one fixture)."""

    free: int = Field(ge=2)
    pro: int = Field(ge=2)

    def for_plan(self, plan: str) -> int:
        return self.pro if plan == "pro" else self.free


class BackfillConfig(_Strict):
    schedule: Schedule
    daily_max_requests: DailyMax
    targets: list[Target] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        keys = [(t.competition, t.season) for t in self.targets]
        if len(keys) != len(set(keys)):
            raise ValueError("a (competition, season) target appears twice")
        return self

    def check_registry(self, registry: Registry) -> None:
        known = {c.code: c for c in registry.competitions}
        for t in self.targets:
            comp = known.get(t.competition)
            if comp is None:
                raise ValueError(f"backfill target {t.competition} is not in config/competitions.yaml")
            if SourceCode.API_FOOTBALL not in comp.providers:
                raise ValueError(f"{t.competition} has no API-Football league id in config/competitions.yaml")


def load_backfill_config(path: Path | None = None) -> BackfillConfig:
    with (path or CONFIG_DIR / "backfill.yaml").open(encoding="utf-8") as f:
        return BackfillConfig.model_validate(yaml.safe_load(f))


# --- progress ----------------------------------------------------------------------------------------------


class TargetState(StrEnum):
    FINISHED = "FINISHED"
    IN_PROGRESS = "IN_PROGRESS"
    NOT_STARTED = "NOT_STARTED"


@dataclass(frozen=True)
class TargetStatus:
    target: Target
    state: TargetState
    still_without_minutes: int | None  # after the last successful run
    last_run_at: str | None


def target_status(engine: Engine, target: Target) -> TargetStatus:
    run = ops_repo.latest_run(engine, target.job_name, RunStatus.SUCCEEDED)
    if run is None:
        return TargetStatus(target, TargetState.NOT_STARTED, None, None)
    stats: dict[str, Any] = run["stats"] or {}
    left = int(stats.get("fixtures_still_without_minutes", 0))
    if target.mode is Mode.CURRENT:  # matches keep finishing: never done
        return TargetStatus(target, TargetState.IN_PROGRESS, left, run["started_at"].isoformat())
    # a history-mode run skipped the known fixtures, so it does not finish a cross-check target
    covered = target.mode is Mode.HISTORY or not stats.get("skipped_known_15")
    done = left == 0 and not stats.get("stopped_at_request_budget") and covered
    return TargetStatus(target, TargetState.FINISHED if done else TargetState.IN_PROGRESS, left,
                        run["started_at"].isoformat())


# --- one day ----------------------------------------------------------------------------------------------


@dataclass
class DayResult:
    runs: list[dict[str, Any]] = field(default_factory=list)  # per target: requests, outcome, key statistics
    requests_used: int = 0
    stopped: str = ""  # why the day ended

    def as_dict(self) -> dict[str, Any]:
        return {"runs": self.runs, "requests_used": self.requests_used, "stopped": self.stopped}


def run_daily_backfill(engine: Engine, store: Callable[[], RawStore], registry: Registry, settings: Settings,
                       cfg: BackfillConfig) -> DayResult:
    cfg.check_registry(registry)
    key = settings.api_football_key.get_secret_value()
    if not key:
        raise ValueError("API_FOOTBALL_KEY is not configured")
    day = DayResult()
    plan_name = settings.api_football_plan
    daily_max = cfg.daily_max_requests.for_plan(plan_name)
    for target in cfg.targets:
        status = target_status(engine, target)
        if status.state is TargetState.FINISHED:
            continue
        entry: dict[str, Any] = {"competition": target.competition, "season": target.season,
                                 "mode": target.mode.value}
        if target.mode is Mode.CURRENT and plan_name != "pro":
            entry.update(outcome="skipped: the current season needs a paid plan (API_FOOTBALL_PLAN=pro)",
                         requests=0)
            day.runs.append(entry)
            continue
        budget = min(daily_max - day.requests_used, settings.api_football_max_requests_per_run)
        if budget < 2:
            day.stopped = "daily budget used"
            break
        try:
            stats = pipeline.api_football_backfill(
                engine, store(), registry, key, target.competition, target.season,
                settings.api_football_plan == "pro", budget,
                requests_per_minute=settings.api_football_requests_per_minute,
                skip_known_15=target.mode in (Mode.HISTORY, Mode.CURRENT))
        except pipeline.PlanError as exc:
            entry["outcome"] = f"plan: {exc}"
            day.runs.append(entry)
            day.stopped = "plan mismatch"
            break
        except pipeline.QuotaError as exc:
            entry["outcome"] = f"quota: {exc}"
            day.runs.append(entry)
            day.stopped = "quota used"
            break
        except pipeline.ConcurrentRunError as exc:
            entry["outcome"] = str(exc)
            day.runs.append(entry)
            day.stopped = "another API-Football job is running"
            break
        except Exception as exc:  # recorded in the run row and its audit; the next target may still work
            log.exception("daily backfill of %s %s failed", target.competition, target.season)
            entry["outcome"] = f"failed: {type(exc).__name__}: {exc}"[:300]
        else:
            entry["outcome"] = "ok"
            entry.update({k: stats.get(k, 0) for k in (
                "fixtures_requested_for_minutes", "records_with_goal_minutes", "fixtures_still_without_minutes",
                "skipped_known_15")})
        run = ops_repo.latest_run(engine, target.job_name)
        used = int(run["requests_used"]) if run else 0
        entry["requests"] = used
        day.requests_used += used
        day.runs.append(entry)
        if entry["outcome"] == "ok" and stats.get("stopped_at_request_budget"):
            day.stopped = "request budget reached"
            break
    else:
        day.stopped = day.stopped or "all targets finished or attempted"
    log.info("daily backfill: %s", day.as_dict())
    return day


def plan(engine: Engine, cfg: BackfillConfig) -> list[TargetStatus]:
    """Where every target stands (no request is made)."""
    return [target_status(engine, t) for t in cfg.targets]


