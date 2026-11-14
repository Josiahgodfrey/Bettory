"""Team statistics per view (GENERAL / HOME / AWAY) and head-to-head statistics. Pure.

Docs:
- SYSTEM-DOCUMENTATION.md 4.1, 5.2, 6   last 10 completed matches before kick-off per view; counts are
                                        always kept next to rates (e.g. 7/10)
- DATA-SOURCES-AND-MARKET-MODEL.md 11   win/draw/loss, goals scored/conceded (FT, HT, 15'), H2H
- DATA-SOURCES-AND-MARKET-MODEL.md 13.4 0-0 rate uses COMPLETE + DERIVED + PARTIAL_DRAW_UNKNOWN; the draw
                                        rate excludes PARTIAL_DRAW_UNKNOWN from its denominator
- DATA-MODEL.md 7.1 selection-bias rule a window containing an INCOMPLETE match is INSUFFICIENT_DATA for
                                        every 15' value (never computed from the remaining matches)

The caller supplies matches already filtered and ordered (core.team_history / core.h2h: FINISHED,
not CONFLICT, not friendlies, before `as_of`, newest first). This module checks the order and the
point-in-time limit again and refuses inconsistent input.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from app.calculation.invariants import InvariantError
from app.domain.enums import DataStatus
from app.domain.models import Score

DEFAULT_WINDOW = 10  # params_v1.4 data_gate.history_matches
DEFAULT_RECENT = 5  # red flag: goal before 15' in 3+ of the last 5
DEFAULT_H2H_LIMIT = 10

_EXACT = {DataStatus.COMPLETE, DataStatus.DERIVED}
_ZERO_KNOWN = _EXACT | {DataStatus.PARTIAL_DRAW_UNKNOWN}


class View(StrEnum):
    GENERAL = "GENERAL"
    HOME = "HOME"
    AWAY = "AWAY"


class StatsStatus(StrEnum):
    OK = "OK"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class TeamMatch:
    """A past match seen from one team's side (`is_home` = that team played at home)."""

    kickoff_utc: datetime
    is_home: bool
    data_status: DataStatus
    ft: Score
    ht: Score | None
    h15: Score | None  # COMPLETE / DERIVED
    goal_before_15: bool | None  # COMPLETE / DERIVED / PARTIAL_DRAW_UNKNOWN

    def __post_init__(self) -> None:
        if self.kickoff_utc.tzinfo is None:
            raise InvariantError("kickoff_utc must be timezone-aware")
        if self.data_status is DataStatus.PENDING:
            raise InvariantError("team statistics take finished matches only")
        if (self.data_status in _EXACT) != (self.h15 is not None):
            raise InvariantError(f"{self.data_status} match has an inconsistent 15' score")
        if (self.data_status in _ZERO_KNOWN) != (self.goal_before_15 is not None):
            raise InvariantError(f"{self.data_status} match has inconsistent goal_before_15")
        if self.h15 is not None and self.goal_before_15 != (self.h15.total > 0):
            raise InvariantError("goal_before_15 disagrees with the 15' score")

    def _for(self, s: Score) -> int:
        return s.home if self.is_home else s.away

    def _against(self, s: Score) -> int:
        return s.away if self.is_home else s.home

    @property
    def scored(self) -> int:
        return self._for(self.ft)

    @property
    def conceded(self) -> int:
        return self._against(self.ft)


@dataclass(frozen=True)
class Rate:
    """A count with its denominator, so a percentage is never shown without its sample (7/10)."""

    count: int
    n: int

    @property
    def value(self) -> float | None:
        return self.count / self.n if self.n else None


@dataclass(frozen=True)
class WindowStats:
    view: View
    status: StatsStatus
    reasons: tuple[str, ...]
    sample_size: int
    exact_sample_size: int  # COMPLETE + DERIVED
    first_kickoff: datetime | None
    last_kickoff: datetime | None
    # full time / half time (never need goal minutes)
    wins: int
    draws: int
    losses: int
    goals_scored: int
    goals_conceded: int
    ht_goals_scored: int | None  # None if any match lacks an HT score
    ht_goals_conceded: int | None
    # 15' values - None when status is INSUFFICIENT_DATA
    zero_zero: Rate | None = None  # 0-0 at 15'
    level: Rate | None = None  # level at 15' (0-0, 1-1 ...); PARTIAL excluded from n
    early_goal: Rate | None = None  # at least one goal before 15'
    early_scored: int | None = None  # exact matches only; None if any PARTIAL (side unknown)
    early_conceded: int | None = None
    early_goals_total: int | None = None  # both teams, exact matches
    exact_ft_goals_total: int | None = None  # FT goals of the exact matches (team early share)
    recent_early_goal_matches: Rate | None = None  # in the newest `recent` matches

    @property
    def win_rate(self) -> float | None:
        return self.wins / self.sample_size if self.sample_size else None

    def avg(self, total: int | None) -> float | None:
        return total / self.sample_size if total is not None and self.sample_size else None

    @property
    def avg_scored(self) -> float | None:
        return self.avg(self.goals_scored)

    @property
    def avg_conceded(self) -> float | None:
        return self.avg(self.goals_conceded)


def check_history(matches: Sequence[TeamMatch], as_of: datetime) -> None:
    if as_of.tzinfo is None:
        raise InvariantError("as_of must be timezone-aware")
    for i, m in enumerate(matches):
        if m.kickoff_utc >= as_of:
            raise InvariantError(f"match at {m.kickoff_utc} is not before as_of {as_of} (point-in-time breach)")
        if i and m.kickoff_utc > matches[i - 1].kickoff_utc:
            raise InvariantError("matches must be ordered newest first")


def window_stats(matches: Sequence[TeamMatch], view: View, as_of: datetime, window: int = DEFAULT_WINDOW,
                 recent: int = DEFAULT_RECENT) -> WindowStats:
    """Statistics of the newest `window` matches of one view (HOME: home matches only, AWAY: away only)."""
    check_history(matches, as_of)
    if view is View.HOME and not all(m.is_home for m in matches):
        raise InvariantError("HOME view received an away match")
    if view is View.AWAY and any(m.is_home for m in matches):
        raise InvariantError("AWAY view received a home match")
    ms = list(matches[:window])
    n = len(ms)

    reasons: list[str] = []
    if n < window:
        reasons.append(f"only {n} matches (< {window})")
    incomplete = sum(m.data_status is DataStatus.INCOMPLETE for m in ms)
    partial = sum(m.data_status is DataStatus.PARTIAL_DRAW_UNKNOWN for m in ms)
    if incomplete:
        reasons.append(f"{incomplete} INCOMPLETE matches (no goal minutes) - window not usable for 15' values")

    all_ht = all(m.ht is not None for m in ms)
    exact = [m for m in ms if m.h15 is not None]
    base: dict[str, Any] = {
        "view": view, "sample_size": n, "exact_sample_size": len(exact),
        "first_kickoff": ms[-1].kickoff_utc if ms else None, "last_kickoff": ms[0].kickoff_utc if ms else None,
        "wins": sum(m.scored > m.conceded for m in ms), "draws": sum(m.scored == m.conceded for m in ms),
        "losses": sum(m.scored < m.conceded for m in ms),
        "goals_scored": sum(m.scored for m in ms), "goals_conceded": sum(m.conceded for m in ms),
        "ht_goals_scored": sum(m._for(m.ht) for m in ms if m.ht) if all_ht else None,
        "ht_goals_conceded": sum(m._against(m.ht) for m in ms if m.ht) if all_ht else None,
    }
    if reasons:
        return WindowStats(status=StatsStatus.INSUFFICIENT_DATA, reasons=tuple(reasons), **base)

    known = [m for m in ms if m.goal_before_15 is not None]  # = all matches here (no INCOMPLETE)
    early_flags = [bool(m.goal_before_15) for m in known]
    newest = ms[:recent]
    return WindowStats(
        status=StatsStatus.OK, reasons=(), **base,
        zero_zero=Rate(sum(not f for f in early_flags), len(known)),
        level=Rate(sum(m.h15.home == m.h15.away for m in exact if m.h15), len(exact)),
        early_goal=Rate(sum(early_flags), len(known)),
        early_scored=None if partial else sum(m._for(m.h15) for m in exact if m.h15),
        early_conceded=None if partial else sum(m._against(m.h15) for m in exact if m.h15),
        early_goals_total=sum(m.h15.total for m in exact if m.h15),
        exact_ft_goals_total=sum(m.ft.total for m in exact),
        recent_early_goal_matches=Rate(sum(bool(m.goal_before_15) for m in newest), len(newest)),
    )


# --- head to head -----------------------------------------------------------------------------------


class H2HScope(StrEnum):
    ALL_VENUES = "ALL_VENUES"
    SAME_VENUE = "SAME_VENUE"  # the fixture's home team at home


@dataclass(frozen=True)
class H2HStats:
    scope: H2HScope
    status: StatsStatus
    reasons: tuple[str, ...]
    meetings: int
    home_wins: int  # wins of the fixture's HOME team, at any venue
    draws: int
    away_wins: int
    avg_ft_goals: float | None
    avg_ht_goals: float | None
    last_meeting: date | None
    zero_zero: Rate | None = None
    early_goal: Rate | None = None


def h2h_stats(meetings: Sequence[TeamMatch], scope: H2HScope, as_of: datetime,
              limit: int = DEFAULT_H2H_LIMIT) -> H2HStats:
    """`meetings` seen from the fixture's home team (is_home = it played at home), newest first.
    H2H is context only (no weight in the formula); its 15' values follow the same selection-bias rule."""
    check_history(meetings, as_of)
    ms = [m for m in meetings if scope is H2HScope.ALL_VENUES or m.is_home][:limit]
    n = len(ms)
    incomplete = sum(m.data_status is DataStatus.INCOMPLETE for m in ms)
    reasons = (f"{incomplete} INCOMPLETE meetings",) if incomplete else ()
    all_ht = all(m.ht is not None for m in ms)
    base: dict[str, Any] = {
        "scope": scope, "meetings": n,
        "home_wins": sum(m.scored > m.conceded for m in ms), "draws": sum(m.scored == m.conceded for m in ms),
        "away_wins": sum(m.scored < m.conceded for m in ms),
        "avg_ft_goals": sum(m.ft.total for m in ms) / n if n else None,
        "avg_ht_goals": sum(m.ht.total for m in ms if m.ht) / n if n and all_ht else None,
        "last_meeting": ms[0].kickoff_utc.date() if ms else None,
    }
    if incomplete:
        return H2HStats(status=StatsStatus.INSUFFICIENT_DATA, reasons=reasons, **base)
    early = sum(bool(m.goal_before_15) for m in ms)
    return H2HStats(status=StatsStatus.OK, reasons=(), zero_zero=Rate(n - early, n), early_goal=Rate(early, n),
                    **base)
