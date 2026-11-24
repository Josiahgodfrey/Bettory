"""API request/response models. Strict: unknown fields are rejected."""

from __future__ import annotations

import datetime as dt
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


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
