"""Recorder schedule and request budgets (config/paper.yaml, docs/PAPER-BETTING.md 8)."""

from __future__ import annotations

from datetime import time, timedelta
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.params import CONFIG_DIR

PAPER_CONFIG = CONFIG_DIR / "paper.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PerPlan(_Strict):
    """A request bound by the plan in API_FOOTBALL_PLAN."""

    free: int = Field(ge=0)
    pro: int = Field(ge=0)

    def for_plan(self, plan: str) -> int:
        return self.pro if plan == "pro" else self.free


class Schedule(_Strict):
    every_minutes: int = Field(ge=1, le=60)


HHMM = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"


class Plan(_Strict):
    """Load each session's fixtures in advance (ARCHITECTURE.md 12.4): the Noon session on its own day, the Night
    session the evening before (it starts at 01:00 EAT)."""

    enabled: bool  # false only if the fixtures are loaded by another job (tests of the later steps use false)
    noon_at: str = Field(pattern=HHMM)  # EAT, the session's own day
    night_at: str = Field(pattern=HHMM)  # EAT, the day before the session
    attempts: int = Field(ge=1, le=10)
    max_requests: PerPlan  # 1 fixtures listing per UTC date the session covers (the Night session covers 2)
    notify_no_fixtures: bool  # Telegram "no fixtures" for a planned session without any (one message each)

    @staticmethod
    def _clock(text: str) -> time:
        h, m = text.split(":")
        return time(int(h), int(m))

    @property
    def noon_time(self) -> time:
        return self._clock(self.noon_at)

    @property
    def night_time(self) -> time:
        return self._clock(self.night_at)


class Capture(_Strict):
    enabled: bool = True  # false (owner decision 2026-11-23): no odds are captured - the owner's app has them
    lead_minutes: int = Field(ge=1, le=180)
    attempts: int = Field(ge=1, le=10)
    max_requests: PerPlan

    @property
    def lead(self) -> timedelta:
        return timedelta(minutes=self.lead_minutes)


class Recheck(_Strict):
    with_odds: bool = True  # false: lineups only (1 request per match instead of 2)
    from_minutes: int = Field(ge=5, le=240)
    to_minutes: int = Field(ge=10, le=360)
    retry_minutes: int = Field(ge=5, le=120)
    attempts: int = Field(ge=1, le=10)
    max_requests: PerPlan

    @model_validator(mode="after")
    def _window(self) -> Self:
        if self.from_minutes >= self.to_minutes:
            raise ValueError("recheck.from_minutes must be before to_minutes")
        return self


class Closing(_Strict):
    enabled: bool = True  # false: no closing odds (no CLV)
    minutes_before: int = Field(ge=2, le=60)
    max_requests: PerPlan


class Results(_Strict):
    every_hours: int = Field(ge=1, le=48)
    lookback_hours: int = Field(ge=3, le=24 * 14)
    max_requests: PerPlan


class History(_Strict):
    """Goal minutes of the session's teams' recent matches from ESPN (docs/ESPN.md), once per session after its plan
    and before its cut-off. ESPN has no key and no daily quota: these requests never touch the API-Football bound."""

    enabled: bool = False  # off unless the config file switches it on (tests of other steps make no ESPN request)
    max_requests: int = Field(default=1500, ge=10, le=10000)  # per run; about 1 per second
    attempts: int = Field(default=2, ge=1, le=10)  # runs per session, failed ones included
    skip_complete: bool = True  # no summary for a match whose 15' data is already complete from another source
    all_competitions: bool = True  # also the registered leagues outside the recorder's (data for later backtests)


class PaperConfig(_Strict):
    schedule: Schedule
    daily_max_requests: PerPlan
    plan: Plan
    capture: Capture
    recheck: Recheck
    closing: Closing
    results: Results
    history: History = History()

    @model_validator(mode="after")
    def _window(self) -> Self:
        if self.capture.lead_minutes <= self.schedule.every_minutes:
            raise ValueError("capture.lead_minutes must be longer than schedule.every_minutes, or a cycle can "
                             "miss the capture window")
        if self.closing.minutes_before <= self.schedule.every_minutes:
            raise ValueError("closing.minutes_before must be longer than schedule.every_minutes, or a cycle can "
                             "miss the closing capture")
        if self.plan.max_requests.free < 2 or self.plan.max_requests.pro < 2:
            raise ValueError("plan.max_requests must allow 2 requests: the Night session covers two UTC dates")
        return self


def load_paper_config(path: Path = PAPER_CONFIG) -> PaperConfig:
    return PaperConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
