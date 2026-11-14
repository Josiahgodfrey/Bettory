"""Expected value, minimum odds, expected bookmaker price and evaluation of a bookmaker's 15' quote. Pure.

FIFTEEN-MINUTE-ODDS-CALCULATION.md:
- 8    fair odds = 1 / P; minimum acceptable odds = (1 + EV_min) / P, EV_min = 0.05
- 9.1  both sides of the bookmaker's 15' market are needed (one side cannot reveal the margin)
- 9.4  expected price = 1 / (P x (1 + m))
- 10   EV = offered odds x P - 1
The two markets: "15' Under 0.5" (= "Goal in 1-15 minutes: NO") and "15' Draw" (1x2 - 15 minutes: DRAW).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.calculation.devig import devig
from app.calculation.invariants import InvariantError, check_fair_odds, check_probability
from app.domain.enums import DevigMethod

DEFAULT_EV_MIN = 0.05  # params_v1.4 value.ev_min


class Bet(StrEnum):
    UNDER05 = "UNDER05"  # Total 0-15 min UNDER 0.5
    DRAW = "DRAW"  # Result 0-15 min DRAW


def _check_odds(odds: float) -> float:
    if not isinstance(odds, (int, float)) or isinstance(odds, bool) or not odds > 1.0 or odds == float("inf"):
        raise InvariantError(f"decimal odds must be finite and > 1, got {odds!r}")
    return float(odds)


def fair_odds(p: float) -> float:
    return check_fair_odds(1.0 / check_probability(p, "P", strict=True))


def min_odds(p: float, ev_min: float = DEFAULT_EV_MIN) -> float:
    """Lowest offered price with EV >= ev_min."""
    if ev_min < 0:
        raise InvariantError("ev_min must be >= 0")
    return (1.0 + ev_min) / check_probability(p, "P", strict=True)


def expected_value(odds: float, p: float) -> float:
    return _check_odds(odds) * check_probability(p, "P") - 1.0


def expected_price(p: float, overround: float) -> float:
    """What a bookmaker with margin `overround` would offer for probability p (multiplicative approximation)."""
    return 1.0 / (check_probability(p, "P", strict=True) * (1.0 + overround))


@dataclass(frozen=True)
class QuoteEvaluation:
    """One bookmaker's complete 15' market, evaluated against BETTORY's probability for one bet."""

    bet: Bet
    bookmaker: str
    offered_odds: float
    overround: float
    devig_method: DevigMethod
    p_bookmaker: float  # bookmaker's de-vigged probability for this outcome
    p: float  # BETTORY's probability
    ev: float
    min_odds: float
    expected_price: float
    gap_pts: float  # |P - P_bookmaker| in percentage points


def evaluate_quote(bet: Bet, prices: Mapping[str, float], p: float, bookmaker: str = "",
                   ev_min: float = DEFAULT_EV_MIN, method: DevigMethod = DevigMethod.POWER,
                   max_overround: float = 0.15) -> QuoteEvaluation:
    """UNDER05: prices {YES, NO} of "Goal in 1-15 minutes" (NO = Under 0.5).
    DRAW: prices {HOME, DRAW, AWAY} of "1x2 - 15 minutes"."""
    sides = ("NO", "YES") if bet is Bet.UNDER05 else ("DRAW", "HOME", "AWAY")
    if set(prices) != set(sides):
        raise InvariantError(f"{bet} quote needs exactly {sorted(sides)}, got {sorted(prices)}")
    d = devig(tuple(_check_odds(prices[s]) for s in sides), method, max_overround=max_overround)
    offered = prices[sides[0]]
    p = check_probability(p, "P", strict=True)
    return QuoteEvaluation(bet, bookmaker, offered, d.overround, method, d.probabilities[0], p,
                           expected_value(offered, p), min_odds(p, ev_min), expected_price(p, d.overround),
                           abs(p - d.probabilities[0]) * 100.0)
