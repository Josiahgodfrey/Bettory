"""Model A: team strength model (SYSTEM-DOCUMENTATION.md 7 Layer 2; DATA-SOURCES-AND-MARKET-MODEL.md 15/17). Pure.

    A_FT: FT attack x defence ratings -> mu_FT per team -> lambda15 = s x mu_FT
    A_HT: Model L match level           -> lambda15 = r x lambda_HT
    Model A = 0.5 x A_FT + 0.5 x A_HT, combined per team at the lambda15 level, so that
    mu (FT-equivalent) = lambda15_A / s can be blended with the market's mu in the pricing engine
    (FIFTEEN-MINUTE-ODDS-CALCULATION.md 4) and P_A = e^-lambda15_A is used for the model-gap check.

The early-tendency index (Model B v2) is NOT applied here; the pricing engine applies it (pricing doc 6).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.calculation.baselines import CompetitionBaseline
from app.calculation.invariants import InvariantError, check_probability
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
from app.calculation.models.model_l import ModelLResult, model_l

DEFAULT_FT_WEIGHT = 0.5  # P_A = 0.5 x A_FT + 0.5 x A_HT


@dataclass(frozen=True)
class ModelAResult:
    strength_ft: Strength | None  # None when mu_FT was supplied directly
    mu_ft: Expectancy  # A_FT expected full-time goals
    s: float
    lambda15_ft: Expectancy  # s x mu_FT
    model_l: ModelLResult  # A_HT
    ft_weight: float
    lambda15: Expectancy  # combined A_FT / A_HT
    mu: Expectancy  # FT-equivalent of the combined lambda15 (lambda15 / s): input to the mu blend
    p00_ft: float
    p00: float  # P_A


def combine(mu_ft: Expectancy, s: float, l_result: ModelLResult, ft_weight: float = DEFAULT_FT_WEIGHT,
            strength_ft: Strength | None = None) -> ModelAResult:
    check_probability(s, "s", strict=True)
    if not 0.0 <= ft_weight <= 1.0:
        raise InvariantError("ft_weight must be within [0, 1]")
    l15_ft = Expectancy(home=s * mu_ft.home, away=s * mu_ft.away)
    hw = 1.0 - ft_weight
    l15 = Expectancy(home=ft_weight * l15_ft.home + hw * l_result.lambda15.home,
                     away=ft_weight * l15_ft.away + hw * l_result.lambda15.away)
    return ModelAResult(
        strength_ft=strength_ft, mu_ft=mu_ft, s=s, lambda15_ft=l15_ft,
        model_l=l_result, ft_weight=ft_weight, lambda15=l15, mu=Expectancy(home=l15.home / s, away=l15.away / s),
        p00_ft=check_probability(math.exp(-l15_ft.total), "P_A_FT"),
        p00=check_probability(math.exp(-l15.total), "P_A"),
    )


def model_a(windows: TeamWindows, baseline: CompetitionBaseline, ft_weight: float = DEFAULT_FT_WEIGHT,
            venue_weight: float = DEFAULT_VENUE_WEIGHT, general_weight: float = DEFAULT_GENERAL_WEIGHT,
            min_matches: int = DEFAULT_MIN_MATCHES) -> ModelAResult:
    if baseline.s is None:
        raise ModelUnavailableError(f"competition baseline has no early share s ({baseline.status})")
    st = strength(windows, Period.FT, baseline.avg_home_goals, baseline.avg_away_goals,
                  venue_weight, general_weight, min_matches)
    mu_ft = expectancy(st, baseline.avg_home_goals, baseline.avg_away_goals)
    l_result = model_l(windows, baseline, venue_weight, general_weight, min_matches)
    return combine(mu_ft, baseline.s, l_result, ft_weight, st)
