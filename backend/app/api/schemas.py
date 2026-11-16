"""API request/response models. Strict: unknown fields are rejected."""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import RunStatus, SourceCode

# API-Football free plan: 100 requests/day. One run may use 90 (ApiFootball default budget):
# 1 for the fixtures of the day + goal-minute requests + odds requests.
API_FOOTBALL_RUN_BUDGET = 90


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- requests --------------------------------------------------------------------------------


class SeasonIngest(Strict):
    competition: str = Field(pattern=r"^[A-Z]{3}\d$", examples=["ENG1"], description="Registry code")
    season: str = Field(pattern=r"^\d{4}(-\d{2})?$", examples=["2025-26"],
                        description='"2025-26" for August-May leagues, "2025" for calendar-year leagues')
    reparse: bool = Field(False, description="Parse again even if the same payload was already stored")


class OpenFootballIngest(SeasonIngest):
    full: bool = Field(True, description="Use the -full file, which has goal minutes (needed for 15')")


class ApiFootballDayIngest(Strict):
    date: dt.date = Field(examples=["2026-11-14"])
    max_event_requests: int = Field(20, ge=0, le=API_FOOTBALL_RUN_BUDGET - 1,
                                    description="Requests for goal minutes of finished matches")
    max_odds_requests: int = Field(20, ge=0, le=API_FOOTBALL_RUN_BUDGET - 1,
                                   description="Requests for odds (incl. 15' markets) of upcoming matches")
    reparse: bool = False

    @model_validator(mode="after")
    def _within_budget(self) -> ApiFootballDayIngest:
        if 1 + self.max_event_requests + self.max_odds_requests > API_FOOTBALL_RUN_BUDGET:
            raise ValueError(f"1 + max_event_requests + max_odds_requests must be <= {API_FOOTBALL_RUN_BUDGET}")
        return self


class ResolveReview(Strict):
    team_id: UUID = Field(description="The existing team this provider name refers to")


# --- responses -------------------------------------------------------------------------------


class JobAccepted(Strict):
    run_id: UUID
    source: SourceCode
    job_name: str
    status: RunStatus = RunStatus.RUNNING
    status_url: str


class RunOut(Strict):
    id: UUID
    source: SourceCode
    job_name: str
    status: RunStatus
    started_at: dt.datetime
    finished_at: dt.datetime | None
    requests_used: int
    items_stored: int
    error: str | None
    stats: dict[str, int]


class CompetitionOut(Strict):
    code: str
    name: str
    country: str
    season_format: str
    sources: list[SourceCode]
    example_season: str


class ReviewOut(Strict):
    id: UUID
    source: SourceCode
    entity_type: str
    provider_key: str
    provider_name: str
    context: dict[str, Any]
    created_at: dt.datetime


class TeamOut(Strict):
    id: UUID
    name: str
    country: str
