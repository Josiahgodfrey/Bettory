"""Sequential e-values: arithmetic, validity under the null (Ville: the 1/alpha bar is crossed at most alpha of the
time, however often one looks) and power when there is an edge."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.settlement.evalues import e_process, forecast_ratio, profit_ratio


def test_arithmetic() -> None:
    assert e_process([]).value == 1.0 and e_process([]).peak == 1.0
    e = e_process([2.0, 0.0], lambdas=(0.5,))
    assert math.isclose(e.value, 1.5 * 0.5) and math.isclose(e.peak, 1.5) and e.n == 2
    mix = e_process([2.0], lambdas=(0.5, 1.0))
    assert math.isclose(mix.value, (1.5 + 2.0) / 2)                     # the mixture is the average wealth
    assert e_process([0.0], lambdas=(1.0,)).value == 0.0                # all-in and lost
    with pytest.raises(ValueError):
        e_process([-1.0])
    with pytest.raises(ValueError):
        e_process([1.0], lambdas=(1.5,))


def test_ratios_have_expectation_one_under_the_null() -> None:
    p = 0.72
    assert math.isclose(p * forecast_ratio(0.8, p, True) + (1 - p) * forecast_ratio(0.8, p, False), 1.0)
    odds = 1.40                                                           # a fair bet: P(win) = 1 / odds
    assert math.isclose((1 / odds) * profit_ratio(odds, True) + (1 - 1 / odds) * profit_ratio(odds, False), 1.0)


def _peaks(rng: np.random.Generator, runs: int, bets: int, p_win: float, odds: float) -> np.ndarray:
    wins = rng.random((runs, bets)) < p_win
    return np.array([e_process([profit_ratio(odds, bool(w)) for w in row]).peak for row in wins])


def test_valid_under_no_edge_even_when_looking_after_every_bet() -> None:
    rng = np.random.default_rng(7)
    odds = 1.40
    peaks = _peaks(rng, 1000, 300, 1 / odds, odds)                       # exactly no edge
    assert (peaks >= 20).mean() <= 0.05


def test_power_with_a_real_edge() -> None:
    rng = np.random.default_rng(11)
    odds = 1.40
    peaks = _peaks(rng, 200, 600, 0.79, odds)                            # EV = 0.79 x 1.40 - 1 = +10.6 %
    assert (peaks >= 20).mean() >= 0.8
