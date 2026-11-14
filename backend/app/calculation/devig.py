"""Bookmaker margin (overround) and margin removal. Pure.

FIFTEEN-MINUTE-ODDS-CALCULATION.md section 9:
- 9.1 all sides of one market are needed: one price alone cannot reveal the margin
- 9.2 overround m = sum(1 / o_i) - 1
- 9.3 multiplicative: p_i = (1/o_i) / sum(1/o_j)
      power (default): k such that sum((1/o_i)^k) = 1, p_i = (1/o_i)^k  -> more margin on the longshot
      Shin (optional): insider-trading model, z such that the Shin probabilities sum to 1
Odds are validated (section 12 risks): every price > 1, margin within [min, max] (default 0 .. 0.15,
params value.overround_max), else the market is rejected - entry errors must not reach a price.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.calculation.invariants import InvariantError, check_probability
from app.domain.enums import DevigMethod

DEFAULT_MIN_OVERROUND = 0.0
DEFAULT_MAX_OVERROUND = 0.15  # params_v1.4 value.overround_max
_TOL = 1e-13
_MAX_ITERATIONS = 300


class MarketError(ValueError):
    """The prices cannot be a valid, complete market (wrong odds, missing side, implausible margin)."""


@dataclass(frozen=True)
class Devigged:
    probabilities: tuple[float, ...]  # same order as the odds; sum = 1
    method: DevigMethod
    overround: float
    parameter: float | None  # power: exponent k; Shin: z; multiplicative: None


def implied_probabilities(odds: Sequence[float]) -> tuple[float, ...]:
    """1 / o_i for every side. At least two sides; every price finite and > 1."""
    if len(odds) < 2:
        raise MarketError("a market needs at least two sides to measure its margin")
    out = []
    for o in odds:
        if not isinstance(o, (int, float)) or isinstance(o, bool) or not math.isfinite(o) or o <= 1.0:
            raise MarketError(f"decimal odds must be finite and > 1, got {o!r}")
        out.append(1.0 / o)
    return tuple(out)


def overround(odds: Sequence[float]) -> float:
    """Bookmaker margin: sum(1 / o_i) - 1 (0.082 = 8.2%)."""
    return math.fsum(implied_probabilities(odds)) - 1.0


def check_overround(m: float, min_overround: float = DEFAULT_MIN_OVERROUND,
                    max_overround: float = DEFAULT_MAX_OVERROUND) -> float:
    if not min_overround <= m <= max_overround:
        raise MarketError(f"overround {m:.4f} outside [{min_overround}, {max_overround}]: "
                          "wrong prices, missing side or mixed bookmakers")
    return m


def _bisect_increasing(f: Callable[[float], float], lo: float, hi: float) -> float:
    """Root of f on [lo, hi] for f increasing with f(lo) <= 0 <= f(hi)."""
    if not f(lo) <= 0.0 <= f(hi):
        raise MarketError("no margin-removal solution in the search interval")
    for _ in range(_MAX_ITERATIONS):
        mid = (lo + hi) / 2.0
        if f(mid) < 0.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < _TOL:
            break
    return (lo + hi) / 2.0


def _multiplicative(pi: tuple[float, ...]) -> tuple[tuple[float, ...], None]:
    total = math.fsum(pi)
    return tuple(p / total for p in pi), None


def _power(pi: tuple[float, ...]) -> tuple[tuple[float, ...], float]:
    # g(k) = 1 - sum(pi^k) is increasing in k because every pi < 1.
    k = _bisect_increasing(lambda k: 1.0 - math.fsum(p ** k for p in pi), 1e-3, 100.0)
    probs = tuple(p ** k for p in pi)
    total = math.fsum(probs)  # = 1 within the solver tolerance; remove the last rounding
    return tuple(p / total for p in probs), k


def _shin_probabilities(pi: tuple[float, ...], z: float) -> tuple[float, ...]:
    total = math.fsum(pi)
    return tuple((math.sqrt(z * z + 4.0 * (1.0 - z) * p * p / total) - z) / (2.0 * (1.0 - z)) for p in pi)


def _shin(pi: tuple[float, ...]) -> tuple[tuple[float, ...], float]:
    if math.fsum(pi) <= 1.0:
        raise MarketError("Shin's model needs a positive margin")
    # sum of Shin probabilities falls from sum(pi)/sqrt(sum(pi)) >= 1 (z = 0) as z grows.
    z = _bisect_increasing(lambda z: 1.0 - math.fsum(_shin_probabilities(pi, z)), 0.0, 0.999)
    probs = _shin_probabilities(pi, z)
    total = math.fsum(probs)
    return tuple(p / total for p in probs), z


_METHODS = {DevigMethod.MULTIPLICATIVE: _multiplicative, DevigMethod.POWER: _power, DevigMethod.SHIN: _shin}


def devig(odds: Sequence[float], method: DevigMethod = DevigMethod.POWER,
          min_overround: float = DEFAULT_MIN_OVERROUND, max_overround: float = DEFAULT_MAX_OVERROUND) -> Devigged:
    """Margin-free probabilities for ALL sides of ONE bookmaker's market (e.g. 15' Under 0.5 / Over 0.5)."""
    pi = implied_probabilities(odds)
    m = check_overround(math.fsum(pi) - 1.0, min_overround, max_overround)
    probs, parameter = _METHODS[method](pi)
    for p in probs:
        check_probability(p, "de-vigged probability", strict=True)
    if abs(math.fsum(probs) - 1.0) > 1e-9:
        raise InvariantError("de-vigged probabilities do not sum to 1")
    return Devigged(probabilities=probs, method=method, overround=m, parameter=parameter)
