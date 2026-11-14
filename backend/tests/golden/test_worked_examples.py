"""Worked examples from the docs reproduce exactly (FIFTEEN-MINUTE-ODDS-CALCULATION.md section 10)."""

import math

import pytest

from app.calculation.devig import devig, overround
from app.calculation.poisson import score_grid, solve_lambda_from_under
from app.domain.enums import DevigMethod

# Section 10 inputs: mu 1.40 / 1.10, share s = 0.125, indexes 1.00
LAM_HOME = 0.125 * 1.40  # 0.175
LAM_AWAY = 0.125 * 1.10  # 0.1375


def test_section_10_worked_example() -> None:
    grid = score_grid(LAM_HOME, LAM_AWAY)
    out = grid.outcomes()
    assert round(LAM_HOME + LAM_AWAY, 10) == 0.3125
    assert out.p_00 == pytest.approx(math.exp(-0.3125), abs=1e-12)
    assert round(out.p_00, 4) == 0.7316                      # doc: 73.2%
    assert round(1 / out.p_00, 3) == 1.367                    # doc: fair odds 1.37 (dev plan: 1.367)
    assert round(grid.p(1, 1), 3) == 0.018                    # doc: P(1-1) 1.8%
    assert round(out.p_draw, 3) == 0.749                      # doc: 74.9% -> fair 1.33
    assert round(1 / out.p_draw, 2) == 1.33
    assert round(out.p_home_lead, 3) == 0.142                 # doc: 14.1% (14.156%)
    assert round(out.p_away_lead, 3) == 0.109                 # doc: 10.9%
    assert round(1.05 / out.p_00, 2) == 1.44                  # doc: minimum odds Under 0.5 at EV 5%


def test_under_05_closed_form_recovers_lambda() -> None:
    assert solve_lambda_from_under(0.5, math.exp(-0.3125)) == pytest.approx(0.3125, abs=1e-12)


def test_section_10_bookmaker_a_margin_and_devig() -> None:
    odds = (1.30, 3.20)                                        # 15' Under 0.5 / Over 0.5
    assert round(overround(odds), 3) == 0.082                  # doc: 8.2%
    mult = devig(odds, DevigMethod.MULTIPLICATIVE)
    power = devig(odds, DevigMethod.POWER)
    assert round(mult.probabilities[0], 3) == 0.711            # doc: 71.1%
    assert round(power.probabilities[0], 3) == 0.739           # doc: 73.9%
    assert power.parameter is not None and round(power.parameter, 3) == 1.154   # doc: k = 1.154
    p_model = math.exp(-0.3125)
    assert round(abs(p_model - power.probabilities[0]) * 100, 1) == 0.7         # doc: gap 0.7 pts
