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


class ApiFootballBackfill(SeasonIngest):
    max_requests: int | None = Field(None, ge=1, description="Request cap for this run; at most "
                                     "API_FOOTBALL_MAX_REQUESTS_PER_RUN (the default)")
    skip_known_15: bool = Field(False, description="Do not fetch fixtures whose 15' result is already known "
                                "(use when no second goal-minute source exists for the season)")


class EspnDaysIngest(Strict):
    """Whole match days of whole leagues from ESPN (docs/ESPN.md): fixtures, scores, goal minutes. Resumable."""

    competitions: list[str] = Field(default_factory=list, max_length=60,
                                    description="Registry codes; empty = every competition with an ESPN code")
    date_from: dt.date = Field(examples=["2026-07-20"])
    date_to: dt.date = Field(examples=["2026-11-22"])
    max_requests: int = Field(1500, ge=1, le=20000, description="About 1 request per second")
    include_complete: bool = Field(False, description="Fetch summaries also for matches already complete")

    @model_validator(mode="after")
    def _range(self) -> EspnDaysIngest:
        if self.date_to < self.date_from or (self.date_to - self.date_from).days > 400:
            raise ValueError("date_to must be on or after date_from, at most 400 days later")
        return self


class EspnHistoryIngest(Strict):
    """The recent matches (goal minutes) of the teams playing in a time window, from ESPN (docs/ESPN.md)."""

    start_utc: dt.datetime = Field(examples=["2026-11-24T10:00:00Z"])
    end_utc: dt.datetime = Field(examples=["2026-11-24T22:00:00Z"])
    competitions: list[str] = Field(default_factory=list, max_length=60,
                                    description="Registry codes; empty = every competition with an ESPN code")
    max_requests: int = Field(1500, ge=1, le=20000, description="About 1 request per second")
    include_complete: bool = Field(False, description="Fetch summaries also for matches already complete")

    @model_validator(mode="after")
    def _window(self) -> EspnHistoryIngest:
        if self.start_utc.tzinfo is None or self.end_utc.tzinfo is None:
            raise ValueError("start_utc and end_utc need a time zone (e.g. Z)")
        if self.end_utc <= self.start_utc or self.end_utc - self.start_utc > dt.timedelta(days=7):
            raise ValueError("end_utc must be after start_utc, at most 7 days later")
        return self


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


class ResolveReviewNewTeam(Strict):
    name: str | None = Field(default=None, min_length=2, max_length=80,
                             description="The new team's name (default: the provider's name)")
    allow_similar: bool = Field(default=False, description="Confirm it differs from the similar teams reported")


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


class CrossCheckRequest(Strict):
    competition: str = Field(min_length=1, description="competition code, e.g. ENG1")
    season: str = Field(pattern=r"^\d{4}(-\d{2})?$", description="season label, e.g. 2025-26")


class AcceptCrossCheck(Strict):
    note: str = Field(min_length=3, max_length=2000, description="what was reviewed and why the data is accepted")


# --- odds check and real bets (docs/API.md step 3) -----------------------------------------------------------

Odds = Field(gt=1.0, lt=1000.0)


class Under05Prices(Strict):
    """Both sides of "Goal in 1-15 minutes" (one side cannot reveal the bookmaker's margin)."""

    yes: float = Odds
    no: float = Odds


class DrawPrices(Strict):
    """All three outcomes of "1x2 - 15 minutes"."""

    home: float = Odds
    draw: float = Odds
    away: float = Odds


class QuoteIn(Strict):
    bookmaker: str = Field(min_length=1, max_length=60, examples=["My bookmaker"])
    under05: Under05Prices | None = None
    draw: DrawPrices | None = None
    store: bool = Field(True, description="Store the prices (append-only, source MANUAL)")

    @model_validator(mode="after")
    def _one_market(self) -> QuoteIn:
        if self.under05 is None and self.draw is None:
            raise ValueError("enter at least one complete 15' market (under05 or draw)")
        return self


class BetIn(Strict):
    match_id: UUID
    market: str = Field(pattern=r"^(UNDER05|DRAW)$")
    odds: float = Odds
    stake: float = Field(gt=0)
    bookmaker: str = Field(min_length=1, max_length=60)
    placed_at: dt.datetime | None = Field(None, description="When the bet was placed (default: now); "
                                          "timezone required")
    note: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def _aware(self) -> BetIn:
        if self.placed_at is not None and self.placed_at.utcoffset() is None:
            raise ValueError("placed_at needs a timezone, e.g. 2026-11-21T14:05:00+03:00")
        return self


class CancelBet(Strict):
    reason: str = Field(min_length=3, max_length=500)
