"""BETTORY standard format (DATA-MODEL.md D1).

Every source parser converts its own format into these models; nothing downstream ever sees a
provider's format. All models are immutable and validated on creation - invalid data raises.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import FixtureStatus, MarketCode, Side, SnapshotType, SourceCode

_MINUTE_LABEL = re.compile(r"^(\d{1,3})(?:\+(\d{1,2}))?$")


class Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class Score(Strict):
    home: int = Field(ge=0)
    away: int = Field(ge=0)

    @property
    def total(self) -> int:
        return self.home + self.away


class SourceGoal(Strict):
    """One goal as reported by a source. Own goals are credited to the benefiting side."""

    side: Side
    minute: int = Field(ge=1, le=130)
    extra_minute: int | None = Field(default=None, ge=1, le=30)
    own_goal: bool = False
    penalty: bool = False
    player: str | None = None

    @classmethod
    def from_label(cls, side: Side, label: str, **kwargs: object) -> SourceGoal:
        """Parse minute labels such as "37", "90+4", "45+2"."""
        match = _MINUTE_LABEL.match(label.strip())
        if not match:
            raise ValueError(f"invalid minute label: {label!r}")
        extra = int(match.group(2)) if match.group(2) else None
        return cls(side=side, minute=int(match.group(1)), extra_minute=extra, **kwargs)

    @property
    def sort_key(self) -> tuple[int, int]:
        return (self.minute, self.extra_minute or 0)

    @property
    def is_first_half(self) -> bool:
        """Minute <= 45 (including 45+x stoppage time)."""
        return self.minute <= 45


class SourceMatch(Strict):
    """One source's version of one match - the output of every parser."""

    source: SourceCode
    provider_match_key: str = Field(min_length=1)
    competition_code: str = Field(min_length=1)  # BETTORY registry code, e.g. "ENG1"
    season_label: str = Field(pattern=r"^\d{4}(-\d{2})?$")  # "2025-26" or "2025"
    kickoff_date: date  # UTC date when kickoff_utc is known, otherwise the source's local date
    kickoff_utc: datetime | None
    home_name: str = Field(min_length=1)
    away_name: str = Field(min_length=1)
    home_key: str | None = None  # the source's own team id, when it has one
    away_key: str | None = None
    status: FixtureStatus
    ft: Score | None = None
    ht: Score | None = None
    goals: tuple[SourceGoal, ...] = ()
    has_goal_minutes: bool
    round: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.kickoff_utc is not None:
            if self.kickoff_utc.utcoffset() is None:
                raise ValueError("kickoff_utc must be timezone-aware")
            if self.kickoff_utc.astimezone(UTC).date() != self.kickoff_date:
                raise ValueError("kickoff_date must equal the UTC date of kickoff_utc")
        if self.home_name == self.away_name:
            raise ValueError("home and away team names are identical")
        if self.status is FixtureStatus.FINISHED and self.ft is None:
            raise ValueError("FINISHED match without full-time score")
        unplayed = self.status in (FixtureStatus.SCHEDULED, FixtureStatus.POSTPONED, FixtureStatus.CANCELLED)
        if unplayed and (self.ft is not None or self.goals):
            raise ValueError(f"{self.status} match must not carry scores or goals")
        if self.ht and self.ft and (self.ht.home > self.ft.home or self.ht.away > self.ft.away):
            raise ValueError("half-time score exceeds full-time score")
        if self.goals and not self.has_goal_minutes:
            raise ValueError("goals present but has_goal_minutes is False")
        return self

    def goals_consistent(self) -> bool:
        """Goal list agrees with FT and HT scores (DATA-MODEL.md section 6)."""
        if not self.has_goal_minutes or self.ft is None:
            return False
        home = [g for g in self.goals if g.side is Side.HOME]
        away = [g for g in self.goals if g.side is Side.AWAY]
        if (len(home), len(away)) != (self.ft.home, self.ft.away):
            return False
        if self.ht is not None:
            first_half = (
                sum(g.is_first_half for g in home),
                sum(g.is_first_half for g in away),
            )
            if first_half != (self.ht.home, self.ht.away):
                return False
        return True


class SourceOdds(Strict):
    """One odds quote as reported by a source."""

    source: SourceCode
    provider_match_key: str = Field(min_length=1)
    competition_code: str | None = None  # set when the odds row is self-contained (CSV)
    bookmaker: str = Field(min_length=1)
    market: MarketCode
    provider_market_name: str = Field(min_length=1)
    selection: str = Field(pattern=r"^[A-Z0-9_.+-]+$")
    line: float | None = None
    odds: float = Field(gt=1.0, lt=1000)
    snapshot_type: SnapshotType
    captured_at: datetime

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.captured_at.utcoffset() is None:
            raise ValueError("captured_at must be timezone-aware")
        return self
