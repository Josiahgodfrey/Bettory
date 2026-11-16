"""Estimated-value scenario (DATA-SOURCES-AND-MARKET-MODEL.md 9, phase 2). Pure.

Historical bookmaker 15' odds are not available, so the bookmaker's 15' market is ASSUMED to be Model C's
fair price (from the main-market odds) with a margin taken off every side:

    offered odds = (1 - margin) / P_C(side)

The result is an estimate, never proof. Note: formula v1.4 only accepts a price at or above Model C's fair
odds (minimum odds = max((1 + EV) / P, 1 / P_C)), so this assumed price can never qualify; the scenario
measures exactly that: whether value can exist when a bookmaker prices 15' markets off its main market.
"""

from __future__ import annotations

from app.calculation.ev import Bet
from app.calculation.models.model_c import ModelCResult
from app.calculation.poisson import score_grid


def _market(probs: dict[str, float], margin: float) -> dict[str, float] | None:
    if any(not 0.0 < p < 1.0 for p in probs.values()):
        return None
    prices = {k: (1.0 - margin) / p for k, p in probs.items()}
    return prices if all(o > 1.0 for o in prices.values()) else None  # an impossible price is no quote


def assumed_quotes(c: ModelCResult, margin: float) -> dict[Bet, dict[str, float]]:
    if not 0.0 <= margin < 1.0:
        raise ValueError("margin must be in [0, 1)")
    if not c.available or c.p00 is None:
        return {}
    out: dict[Bet, dict[str, float]] = {}
    under = _market({"NO": c.p00, "YES": 1.0 - c.p00}, margin)
    if under is not None:
        out[Bet.UNDER05] = under
    if c.lambda15_home is not None and c.lambda15_away is not None:
        o = score_grid(c.lambda15_home, c.lambda15_away).outcomes()
        draw = _market({"DRAW": o.p_draw, "HOME": o.p_home_lead, "AWAY": o.p_away_lead}, margin)
        if draw is not None:
            out[Bet.DRAW] = draw
    return out
