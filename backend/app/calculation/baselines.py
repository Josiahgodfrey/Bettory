"""Competition baselines: goal averages, the 0-15 shares s and r with shrinkage, competition lambda15. Pure.

Docs:
- FIFTEEN-MINUTE-ODDS-CALCULATION.md 5.2  s = goals with minute <= T / all goals, shrunk toward the
                                          global share: s = (goals_<=T + K s_global) / (all goals + K), K = 300
- FIFTEEN-MINUTE-ODDS-CALCULATION.md 5.3  r = 0-15 goals / first-half goals (same shrinkage)
- FIFTEEN-MINUTE-ODDS-CALCULATION.md 5.4  s outside [0.09, 0.16] -> flag for review
- DATA-SOURCES-AND-MARKET-MODEL.md 13.4   0-0 rate uses COMPLETE + DERIVED + PARTIAL_DRAW_UNKNOWN
- DATA-SOURCES-AND-MARKET-MODEL.md 14.2   Model L league level: lambda15_L = avg HT goals x r
- DATA-SOURCES-AND-MARKET-MODEL.md 16     competition lambda15 (= s x avg FT goals), >= 50 matches,
                                          up to 3 seasons pooled
- DATA-MODEL.md 7.1 selection-bias rule   matches without a usable 15' result are never dropped: the
                                          15'-based values become INSUFFICIENT_DATA instead

Inputs are already point-in-time (the caller reads matches before `as_of`). FT/HT averages never need
goal minutes, so they stay usable when the 15' values are INSUFFICIENT_DATA.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, fields
from enum import StrEnum
from typing import Any

from app.calculation.invariants import InvariantError, check_probability
from app.domain.enums import DataStatus
from app.domain.models import Score
from app.domain.params import Params
from app.domain.registry import season_start_year

_EXACT_EARLY = {DataStatus.COMPLETE, DataStatus.DERIVED}  # exact number of 0-15 goals known
_ZERO_KNOWN = _EXACT_EARLY | {DataStatus.PARTIAL_DRAW_UNKNOWN}  # "goal before 15'?" known


class BaselineStatus(StrEnum):
    OK = "OK"
    REVIEW = "REVIEW"  # computed, but s is outside the plausible range (data error or unusual league)
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"  # do not use the 15'-based values


@dataclass(frozen=True)
class BaselineMatch:
    """One FINISHED, non-CONFLICT, non-friendly match of the competition (see core.competition_matches)."""

    season_label: str
    data_status: DataStatus
    ft: Score
    ht: Score | None
    h15: Score | None  # set for COMPLETE and DERIVED
    goal_before_15: bool | None  # set for COMPLETE, DERIVED and PARTIAL_DRAW_UNKNOWN

    def __post_init__(self) -> None:
        if self.data_status is DataStatus.PENDING:
            raise InvariantError("baselines take finished matches only")
        if (self.data_status in _EXACT_EARLY) != (self.h15 is not None):
            raise InvariantError(f"{self.data_status} match must {'' if self.h15 is None else 'not '}have a 15' score")
        if (self.data_status in _ZERO_KNOWN) != (self.goal_before_15 is not None):
            raise InvariantError(f"{self.data_status} match has inconsistent goal_before_15")
        if self.h15 is not None and self.goal_before_15 != (self.h15.total > 0):
            raise InvariantError("goal_before_15 disagrees with the 15' score")


@dataclass(frozen=True)
class Tally:
    """Sums over a set of matches. Tallies of different seasons or competitions can be added."""

    matches: int = 0
    ft_home: int = 0
    ft_away: int = 0
    ht_matches: int = 0  # matches with an HT score
    ht_home: int = 0
    ht_away: int = 0
    exact_matches: int = 0  # COMPLETE + DERIVED: exact 0-15 goals known
    exact_goals: int = 0  # FT goals in those matches
    exact_ht_goals: int = 0  # first-half goals in those matches (with HT score)
    exact_with_ht: int = 0
    exact_with_ht_early: int = 0  # 0-15 goals in exact matches that have an HT score
    early_goals: int = 0  # 0-15 goals in exact matches
    zero_known: int = 0  # COMPLETE + DERIVED + PARTIAL
    zero_zero: int = 0  # 0-0 at 15'
    partial: int = 0
    incomplete: int = 0

    def __add__(self, other: Tally) -> Tally:
        return Tally(**{f.name: getattr(self, f.name) + getattr(other, f.name) for f in fields(self)})


def tally(matches: Iterable[BaselineMatch]) -> Tally:
    t: dict[str, int] = {f.name: 0 for f in fields(Tally)}
    for m in matches:
        t["matches"] += 1
        t["ft_home"] += m.ft.home
        t["ft_away"] += m.ft.away
        if m.ht is not None:
            t["ht_matches"] += 1
            t["ht_home"] += m.ht.home
            t["ht_away"] += m.ht.away
        if m.h15 is not None:
            t["exact_matches"] += 1
            t["exact_goals"] += m.ft.total
            t["early_goals"] += m.h15.total
            if m.ht is not None:
                t["exact_with_ht"] += 1
                t["exact_ht_goals"] += m.ht.total
                t["exact_with_ht_early"] += m.h15.total
        if m.goal_before_15 is not None:
            t["zero_known"] += 1
            t["zero_zero"] += 0 if m.goal_before_15 else 1
        if m.data_status is DataStatus.PARTIAL_DRAW_UNKNOWN:
            t["partial"] += 1
        elif m.data_status is DataStatus.INCOMPLETE:
            t["incomplete"] += 1
    return Tally(**t)


def pool_seasons(by_season: dict[str, Tally], max_seasons: int) -> tuple[Tally, tuple[str, ...]]:
    """The most recent `max_seasons` seasons pooled (labels sorted by start year, newest first);
    max_seasons = params competition_baselines.max_pooled_seasons."""
    if max_seasons < 1:
        raise InvariantError("max_seasons must be >= 1")
    labels = sorted(by_season, key=season_start_year, reverse=True)[:max_seasons]
    pooled = Tally()
    for label in labels:
        pooled = pooled + by_season[label]
    return pooled, tuple(labels)


# --- shares ---------------------------------------------------------------------------------------


def _shares_usable(t: Tally) -> bool:
    """s and r need the exact 0-15 count of EVERY match (selection-bias rule)."""
    return t.matches > 0 and t.exact_matches == t.matches


@dataclass(frozen=True)
class GlobalShares:
    s: float  # share of all goals in 0-15
    r: float  # share of first-half goals in 0-15
    goals: int
    competitions: int


def global_shares(tallies: Sequence[Tally]) -> GlobalShares:
    """Shrinkage targets: pooled over every competition whose tally is complete. Raises if none."""
    usable = [t for t in tallies if _shares_usable(t) and t.exact_with_ht == t.matches]
    total = sum(usable, Tally())
    if total.exact_goals == 0 or total.exact_ht_goals == 0:
        raise InvariantError("no competition with complete 15' data to compute global shares")
    return GlobalShares(s=total.early_goals / total.exact_goals,
                        r=total.exact_with_ht_early / total.exact_ht_goals,
                        goals=total.exact_goals, competitions=len(usable))


def shrink(count: float, total: float, prior: float, k: float) -> float:
    """(count + k x prior) / (total + k): pulls small samples toward the prior."""
    if total < 0 or count < 0 or count > total or k < 0:
        raise InvariantError(f"invalid shrinkage inputs: count={count}, total={total}, k={k}")
    if total + k == 0:
        raise InvariantError("shrinkage with no data and k = 0")
    return (count + k * prior) / (total + k)


# --- baseline -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CompetitionBaseline:
    status: BaselineStatus
    reasons: tuple[str, ...]
    matches: int
    seasons: tuple[str, ...]
    # FT / HT averages - usable whenever matches >= min_matches, even if 15' data is missing
    avg_home_goals: float
    avg_away_goals: float
    avg_ht_home_goals: float | None
    avg_ht_away_goals: float | None
    ht_share: float | None  # first-half goals / all goals
    # 15'-based values - None when status is INSUFFICIENT_DATA
    s_raw: float | None = None
    s: float | None = None
    r_raw: float | None = None
    r: float | None = None
    lambda15: float | None = None  # s x avg FT goals (FT-based, used for expected_i in Model B v2)
    lambda15_l: float | None = None  # r x avg HT goals (Model L league level)
    p00_l: float | None = None  # e^-lambda15_l (Model L league screen)
    zero_zero_rate: float | None = None  # observed share of 0-0 at 15'
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def avg_goals(self) -> float:
        return self.avg_home_goals + self.avg_away_goals

    @property
    def avg_ht_goals(self) -> float | None:
        if self.avg_ht_home_goals is None or self.avg_ht_away_goals is None:
            return None
        return self.avg_ht_home_goals + self.avg_ht_away_goals


def competition_baseline(t: Tally, shares: GlobalShares, params: Params,
                         seasons: tuple[str, ...] = ()) -> CompetitionBaseline:
    cb = params.competition_baselines
    k_goals, min_matches = params.shrinkage.early_share_K_goals, cb.min_matches
    share_review_low, share_review_high = cb.share_review_low, cb.share_review_high
    reasons: list[str] = []
    counts = {"matches": t.matches, "exact": t.exact_matches, "partial": t.partial, "incomplete": t.incomplete}
    if t.matches < min_matches:
        reasons.append(f"only {t.matches} matches (< {min_matches})")
    if t.matches == 0:
        return CompetitionBaseline(BaselineStatus.INSUFFICIENT_DATA, tuple(reasons), 0, seasons,
                                   0.0, 0.0, None, None, None, counts=counts)

    avg_home, avg_away = t.ft_home / t.matches, t.ft_away / t.matches
    all_ht = t.ht_matches == t.matches
    avg_ht_home = t.ht_home / t.matches if all_ht else None
    avg_ht_away = t.ht_away / t.matches if all_ht else None
    ft_goals = t.ft_home + t.ft_away
    ht_share = (t.ht_home + t.ht_away) / ft_goals if all_ht and ft_goals else None
    if not all_ht:
        reasons.append(f"{t.matches - t.ht_matches} matches without HT score")
    base: dict[str, Any] = {
        "matches": t.matches, "seasons": seasons, "avg_home_goals": avg_home, "avg_away_goals": avg_away,
        "avg_ht_home_goals": avg_ht_home, "avg_ht_away_goals": avg_ht_away, "ht_share": ht_share, "counts": counts,
    }

    if t.incomplete:
        reasons.append(f"{t.incomplete} INCOMPLETE matches (no goal minutes) - never dropped (DATA-MODEL 7.1)")
    if t.partial:
        reasons.append(f"{t.partial} PARTIAL_DRAW_UNKNOWN matches: exact 0-15 goal count unknown")
    if reasons:
        return CompetitionBaseline(BaselineStatus.INSUFFICIENT_DATA, tuple(reasons), **base)

    s_raw = t.early_goals / t.exact_goals if t.exact_goals else None
    s = shrink(t.early_goals, t.exact_goals, shares.s, k_goals)
    r_raw = t.exact_with_ht_early / t.exact_ht_goals if t.exact_ht_goals else None
    r = shrink(t.exact_with_ht_early, t.exact_ht_goals, shares.r, k_goals)
    lambda15 = s * (avg_home + avg_away)
    lambda15_l = r * (t.ht_home + t.ht_away) / t.matches
    p00_l = math.exp(-lambda15_l)
    zero_zero_rate = t.zero_zero / t.zero_known
    for name, value in (("s", s), ("r", r), ("p00_l", p00_l), ("zero_zero_rate", zero_zero_rate)):
        check_probability(value, name)

    status = BaselineStatus.OK
    if s_raw is not None and not share_review_low <= s_raw <= share_review_high:
        status = BaselineStatus.REVIEW
        reasons.append(f"early share s = {s_raw:.3f} outside [{share_review_low}, {share_review_high}]")
    return CompetitionBaseline(status, tuple(reasons), s_raw=s_raw, s=s, r_raw=r_raw, r=r, lambda15=lambda15,
                               lambda15_l=lambda15_l, p00_l=p00_l, zero_zero_rate=zero_zero_rate,
                               **base)
