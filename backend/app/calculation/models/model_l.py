"""Model L: league-average timeline model, match level (DATA-SOURCES-AND-MARKET-MODEL.md 14.3). Pure.

    HTAttack / HTDefence = team HT goals scored / conceded / league HT averages (60% venue, 40% general)
    lambda_HT_home = league avg HT home goals x HTAttack_home x HTDefence_away
    lambda_HT_away = league avg HT away goals x HTAttack_away x HTDefence_home
    lambda15       = r x (lambda_HT_home + lambda_HT_away)
    P_L            = e^-lambda15

The league level (screening, 14.2) is CompetitionBaseline.p00_l in baselines.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.calculation.baselines import CompetitionBaseline
from app.calculation.invariants import check_probability
from app.calculation.models import (
    DEFAULT_GENERAL_WEIGHT,
    DEFAULT_MIN_MATCHES,
    DEFAULT_VENUE_WEIGHT,
    Expectancy,
    ModelUnavailableError,
    Period,
    Strength,
    TeamWindows,
    expectancy,
    strength,
)


@dataclass(frozen=True)
class ModelLResult:
    strength: Strength
    lambda_ht: Expectancy  # expected first-half goals
    r: float
    lambda15: Expectancy  # r x lambda_HT per team
    p00: float  # P_L(0-0 at 15')


def model_l_from_strength(st: Strength, avg_ht_home: float, avg_ht_away: float, r: float) -> ModelLResult:
    check_probability(r, "r", strict=True)
    ht = expectancy(st, avg_ht_home, avg_ht_away)
    l15 = Expectancy(home=r * ht.home, away=r * ht.away)
    return ModelLResult(st, ht, r, l15, check_probability(math.exp(-l15.total), "P_L"))


def model_l(windows: TeamWindows, baseline: CompetitionBaseline, venue_weight: float = DEFAULT_VENUE_WEIGHT,
            general_weight: float = DEFAULT_GENERAL_WEIGHT, min_matches: int = DEFAULT_MIN_MATCHES) -> ModelLResult:
    if baseline.r is None or baseline.avg_ht_home_goals is None or baseline.avg_ht_away_goals is None:
        raise ModelUnavailableError(f"competition baseline has no r / HT averages ({baseline.status})")
    st = strength(windows, Period.HT, baseline.avg_ht_home_goals, baseline.avg_ht_away_goals,
                  venue_weight, general_weight, min_matches)
    return model_l_from_strength(st, baseline.avg_ht_home_goals, baseline.avg_ht_away_goals, baseline.r)
