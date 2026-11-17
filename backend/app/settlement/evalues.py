"""Sequential e-values: evidence you may look at every day and stop on, without invalidating it. Pure.
docs/RESEARCH-PHASE4-TUNING.md §3.6, docs/PAPER-BETTING.md §7.

An e-process here is the wealth of a gambler who, observation after observation, stakes a fraction lambda of the
current wealth on a ratio r_i whose expectation is at most 1 under the null hypothesis H0:

    W_n(lambda) = prod_i (1 + lambda (r_i - 1)),   0 <= lambda <= 1,  r_i >= 0

Under H0 every W(lambda) is a non-negative supermartingale starting at 1, and so is any average of them (the
mixture over a fixed grid of lambdas, which spares choosing lambda in advance). Ville's inequality:

    P_H0( W ever reaches 1 / alpha ) <= alpha

so the running maximum may be compared with 1/alpha at any time - daily checks and early stopping are allowed.

Two uses:
    profit     r_i = odds_i x won_i            H0: no edge, P(win) <= 1 / odds for every bet  => E[r] <= 1
    forecast   r_i = q_i(y_i) / m_i(y_i)       q = BETTORY's probability, m = the market's de-vigged probability
                                               of the outcome that happened; H0: the market is right => E[r] = 1
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

LAMBDAS = (0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5)


@dataclass(frozen=True)
class EProcess:
    n: int
    value: float  # mixture wealth now
    peak: float  # running maximum (what Ville's inequality bounds)
    log_path: tuple[float, ...]  # log mixture wealth after each observation

    def reached(self, alpha: float) -> bool:
        return self.peak >= 1.0 / alpha


def e_process(ratios: Sequence[float], lambdas: Sequence[float] = LAMBDAS) -> EProcess:
    if any(not lam_ <= 1.0 or lam_ < 0 for lam_ in lambdas) or not lambdas:
        raise ValueError("lambdas must lie in [0, 1]")
    r = np.asarray(ratios, dtype=float)
    if r.size and (not np.all(np.isfinite(r)) or r.min() < 0):
        raise ValueError("ratios must be finite and >= 0")
    if r.size == 0:
        return EProcess(0, 1.0, 1.0, ())
    lam = np.asarray(lambdas, dtype=float)[:, None]
    steps = 1.0 + lam * (r[None, :] - 1.0)  # >= 1 - lambda >= 0
    with np.errstate(divide="ignore"):
        logs = np.cumsum(np.log(steps), axis=1)  # per lambda, after each observation
    top = logs.max(axis=0)
    finite = np.isfinite(top)
    mix = np.full(r.size, -math.inf)
    mix[finite] = top[finite] + np.log(np.exp(logs[:, finite] - top[finite]).mean(axis=0))
    path = tuple(float(x) for x in mix)
    peak = max(1.0, float(np.exp(np.max(mix))))
    return EProcess(int(r.size), float(np.exp(mix[-1])), peak, path)


def profit_ratio(odds: float, won: bool) -> float:
    if not odds > 1.0:
        raise ValueError("odds must be > 1")
    return odds if won else 0.0


def forecast_ratio(p_ours: float, p_market: float, happened: bool) -> float:
    """q(y) / m(y) for a binary outcome; both probabilities of the event, in (0, 1)."""
    if not (0.0 < p_ours < 1.0 and 0.0 < p_market < 1.0):
        raise ValueError("probabilities must lie in (0, 1)")
    return (p_ours / p_market) if happened else ((1.0 - p_ours) / (1.0 - p_market))
