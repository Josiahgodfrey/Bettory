"""Recorder schedule and request budgets (config/paper.yaml, docs/PAPER-BETTING.md 8)."""

from __future__ import annotations

from datetime import timedelta
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


class Capture(_Strict):
    lead_minutes: int = Field(ge=1, le=180)
    attempts: int = Field(ge=1, le=10)
    max_requests: PerPlan

    @property
    def lead(self) -> timedelta:
        return timedelta(minutes=self.lead_minutes)


class Results(_Strict):
    every_hours: int = Field(ge=1, le=48)
    lookback_hours: int = Field(ge=3, le=24 * 14)
    max_requests: PerPlan


class PaperConfig(_Strict):
    schedule: Schedule
    daily_max_requests: PerPlan
    capture: Capture
    results: Results

    @model_validator(mode="after")
    def _window(self) -> Self:
        if self.capture.lead_minutes <= self.schedule.every_minutes:
            raise ValueError("capture.lead_minutes must be longer than schedule.every_minutes, or a cycle can "
                             "miss the capture window")
        return self


def load_paper_config(path: Path = PAPER_CONFIG) -> PaperConfig:
    return PaperConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
