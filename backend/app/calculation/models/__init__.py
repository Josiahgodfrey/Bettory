"""Probability models: A (strength), L (league timeline), B v2 (competition index), C (market-implied).

Shared pieces: the attack x defence strength calculation used by Model A (FT goals) and Model L (HT
goals), and ModelUnavailableError - raised when a model's inputs are missing, so the caller can
record WHY a model did not produce a number (never a silent default).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.calculation.invariants import InvariantError, check_lambda
from app.calculation.team_stats import View, WindowStats
from app.domain.params import Params


class ModelUnavailableError(ValueError):
    """The model cannot produce a probability for this fixture (reason in the message)."""


class Period(StrEnum):
    FT = "FT"  # full-time goals (Model A_FT)
    HT = "HT"  # first-half goals (Model L / A_HT)


@dataclass(frozen=True)
class TeamWindows:
    """The two views each team uses (SYSTEM-DOCUMENTATION 4.1): home team GENERAL + HOME, away team GENERAL + AWAY."""

    home_general: WindowStats
    home_home: WindowStats
    away_general: WindowStats
    away_away: WindowStats

    def __post_init__(self) -> None:
        expected = ((self.home_general, View.GENERAL), (self.home_home, View.HOME),
                    (self.away_general, View.GENERAL), (self.away_away, View.AWAY))
        for stats, view in expected:
            if stats.view is not view:
                raise InvariantError(f"expected a {view} window, got {stats.view}")


@dataclass(frozen=True)
class Strength:
    attack_home: float
    defence_home: float
    attack_away: float
    defence_away: float


@dataclass(frozen=True)
class Expectancy:
    """Expected goals of each team for one period (FT or HT)."""

    home: float
    away: float

    @property
    def total(self) -> float:
        return self.home + self.away


def _per_match(stats: WindowStats, period: Period, min_matches: int, prior_scored: float, prior_conceded: float,
               m: float) -> tuple[float, float]:
    """(scored, conceded) per match for one window, shrunk toward the league rates with `m` pseudo-matches:
    (goals + m x league rate) / (matches + m). m = 0 gives the raw rate (the 1.4 formula)."""
    if stats.sample_size < min_matches:
        raise ModelUnavailableError(f"{stats.view} window has {stats.sample_size} matches (< {min_matches})")
    if period is Period.FT:
        scored, conceded = stats.goals_scored, stats.goals_conceded
    else:
        if stats.ht_goals_scored is None or stats.ht_goals_conceded is None:
            raise ModelUnavailableError(f"{stats.view} window has matches without an HT score")
        scored, conceded = stats.ht_goals_scored, stats.ht_goals_conceded
    n = stats.sample_size
    return (scored + m * prior_scored) / (n + m), (conceded + m * prior_conceded) / (n + m)


def strength(windows: TeamWindows, period: Period, avg_home: float, avg_away: float, params: Params) -> Strength:
    """SYSTEM-DOCUMENTATION 7, Layer 2 (FT) and DATA-SOURCES 14.3 (HT): each rating is a venue / general
    blend (blend.venue_weight / general_weight) of the team's per-match rate divided by the league average
    for that venue (general view: league average goals per team). Rates are first shrunk toward the league
    average with model_a.rating_prior_matches pseudo-matches (params 1.5), so ratings from 10 matches are
    not taken at face value and a team without goals in its window keeps a positive rating."""
    avg_home = check_lambda(avg_home, "league avg home goals")
    avg_away = check_lambda(avg_away, "league avg away goals")
    if avg_home == 0 or avg_away == 0:
        raise ModelUnavailableError("league average goals is 0")
    venue_weight, general_weight = params.blend.venue_weight, params.blend.general_weight
    min_matches = params.data_gate.history_matches
    m = params.model_a.rating_prior_matches.FT if period is Period.FT else params.model_a.rating_prior_matches.HT
    per_team = (avg_home + avg_away) / 2.0

    h_gen_s, h_gen_c = _per_match(windows.home_general, period, min_matches, per_team, per_team, m)
    h_home_s, h_home_c = _per_match(windows.home_home, period, min_matches, avg_home, avg_away, m)
    a_gen_s, a_gen_c = _per_match(windows.away_general, period, min_matches, per_team, per_team, m)
    a_away_s, a_away_c = _per_match(windows.away_away, period, min_matches, avg_away, avg_home, m)

    def blend(venue: float, venue_avg: float, general: float) -> float:
        return venue_weight * venue / venue_avg + general_weight * general / per_team

    return Strength(
        attack_home=blend(h_home_s, avg_home, h_gen_s),
        defence_home=blend(h_home_c, avg_away, h_gen_c),   # home team concedes what away teams score
        attack_away=blend(a_away_s, avg_away, a_gen_s),
        defence_away=blend(a_away_c, avg_home, a_gen_c),   # away team concedes what home teams score
    )


def expectancy(st: Strength, avg_home: float, avg_away: float) -> Expectancy:
    """lambda_home = avg home goals x HomeAttack x AwayDefence; lambda_away = avg away x AwayAttack x HomeDefence."""
    return Expectancy(home=check_lambda(avg_home * st.attack_home * st.defence_away, "lambda_home"),
                      away=check_lambda(avg_away * st.attack_away * st.defence_home, "lambda_away"))
