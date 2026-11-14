"""Pricing engine (v1.4) and EV: the pricing doc's full worked example, blend rules, market fallbacks."""

import math

import pytest

from app.calculation.ev import Bet, evaluate_quote, expected_price, expected_value, fair_odds, min_odds
from app.calculation.invariants import InvariantError
from app.calculation.models import Expectancy, Strength
from app.calculation.models.model_a import ModelAResult, combine
from app.calculation.models.model_b2 import BaselineMethod, ModelB2Result
from app.calculation.models.model_c import MarketStatus, ModelCResult
from app.calculation.models.model_l import model_l_from_strength
from app.calculation.pricing import MuSource, Price
from app.calculation.pricing import price as _price
from app.domain.params import Params
from tests.support import PARAMS, params

S = 0.125
NEUTRAL = Strength(1.0, 1.0, 1.0, 1.0)


def team(mu_home: float, mu_away: float, s: float = S) -> ModelAResult:
    """Model A whose blended mu equals the given values (ft_weight 1 -> mu = mu_FT)."""
    return combine(Expectancy(home=mu_home, away=mu_away), s, model_l_from_strength(NEUTRAL, 0.6, 0.5, 0.28),
                   ft_weight=1.0)


def b2(f_home: float, f_away: float) -> ModelB2Result:
    return ModelB2Result(f_home ** 2, f_away ** 2, f_home, f_away, 0.32, BaselineMethod.OWN, 0.3, 0.74, ())


def c_result(l_home: float | None, l_away: float | None, total: float) -> ModelCResult:
    draw = None if l_home is None else 0.75
    return ModelCResult(MarketStatus.OK, (), lambda15=total, lambda15_home=l_home, lambda15_away=l_away,
                        p00=math.exp(-total), p_draw=draw)


def price(a: ModelAResult, s: float, p: Params = PARAMS, model_b2: ModelB2Result | None = None,
          model_c: ModelCResult | None = None) -> Price:
    return _price(a, s, p, model_b2, model_c)


MARKET_ONLY = params(blend__team_weight=0.0, blend__market_weight=1.0)


def test_pricing_doc_13_full_worked_example() -> None:
    p = price(team(1.40, 1.10), S)                       # no market odds entered: w_team = 1
    assert p.mu_source is MuSource.TEAM and p.w_team == 1.0
    assert (p.lambda15.home, p.lambda15.away) == (pytest.approx(0.175), pytest.approx(0.1375))
    o = p.outcomes
    assert round(o.p_00, 3) == 0.732 and round(p.fair.under05, 2) == 1.37               # doc: 73.2%, 1.37
    assert round(o.p_draw, 3) == 0.749 and round(p.fair.draw, 2) == 1.33                # doc: 74.9%, 1.33
    assert round(o.p_home_lead, 3) == 0.142 and round(o.p_away_lead, 3) == 0.109
    assert round(p.min_odds_under05, 2) == 1.44                                          # doc: 1.05 / 0.732

    a = evaluate_quote(Bet.UNDER05, {"NO": 1.30, "YES": 3.20}, o.p_00, PARAMS, "Bookmaker A")
    assert round(a.overround, 3) == 0.082 and round(a.p_bookmaker, 3) == 0.739          # doc: 8.2%, 73.9%
    assert round(a.expected_price, 2) == 1.26                                            # doc: 1 / (0.732 x 1.082)
    assert round(a.ev, 3) == -0.049                                                      # doc: -4.9% -> SKIP
    assert round(a.gap_pts, 1) == 0.7                                                    # doc: 0.7 pts
    assert round(expected_value(1.45, o.p_00), 3) == 0.061                               # doc: Bookmaker B +6.1%


def test_market_blend_at_mu_level() -> None:
    a = team(1.40, 1.10)
    mkt = c_result(0.20, 0.10, 0.30)                     # market mu = lambda15 / s = 1.6 / 0.8
    p = price(a, S, model_c=mkt)
    assert p.mu_source is MuSource.BLEND and p.w_mkt == 0.5
    assert p.mu.home == pytest.approx(0.5 * 1.40 + 0.5 * 1.6) and p.mu.away == pytest.approx(0.5 * 1.10 + 0.5 * 0.8)
    assert p.p_market_00 == pytest.approx(math.exp(-0.30)) and p.p_market_draw == 0.75
    assert p.p_team_00 == pytest.approx(math.exp(-0.3125))
    only_market = price(a, S, MARKET_ONLY, model_c=mkt)
    assert only_market.mu_source is MuSource.MARKET and only_market.lambda15.total == pytest.approx(0.30)


def test_market_without_split_uses_team_split() -> None:
    p = price(team(1.50, 1.00), S, MARKET_ONLY, model_c=c_result(None, None, 0.30))
    assert p.lambda15.total == pytest.approx(0.30)
    assert p.mu.home / p.mu.total == pytest.approx(0.6)
    assert any("split like the team model" in n for n in p.notes) and p.p_market_draw is None


def test_unusable_market_falls_back_to_team() -> None:
    inconsistent = ModelCResult(MarketStatus.MARKET_INCONSISTENT, ("lines disagree",))
    p = price(team(1.40, 1.10), S, model_c=inconsistent)
    assert p.w_team == 1.0 and p.mu_source is MuSource.TEAM and p.p_market_00 is None
    assert any("MARKET_INCONSISTENT" in n for n in p.notes)


def test_index_factors_scale_lambda15() -> None:
    p = price(team(1.40, 1.10), S, model_b2=b2(0.88, 1.10))
    assert p.lambda15.home == pytest.approx(0.175 * 0.88) and p.lambda15.away == pytest.approx(0.1375 * 1.10)
    assert p.index_home == 0.88
    assert price(team(1.40, 1.10), S).index_home == 1.0                        # B v2 unavailable -> 1.0


def test_delta_changes_draw_only() -> None:
    base, adj = price(team(1.40, 1.10), S), price(team(1.40, 1.10), S, params(state_dependence__delta=0.2))
    assert adj.outcomes.p_00 == base.outcomes.p_00 and adj.outcomes.p_draw > base.outcomes.p_draw


def test_configuration_errors() -> None:
    with pytest.raises(ValueError):
        params(blend__team_weight=0.6, blend__market_weight=0.6)
    with pytest.raises(InvariantError, match="different s"):
        price(team(1.4, 1.1, s=0.12), S)


def test_ev_helpers() -> None:
    assert fair_odds(0.5) == 2.0 and min_odds(0.5, 0.05) == pytest.approx(2.1)
    assert expected_price(0.5, 0.0) == 2.0
    with pytest.raises(InvariantError):
        fair_odds(1.0)
    with pytest.raises(InvariantError):
        expected_value(1.0, 0.5)
    with pytest.raises(InvariantError):
        min_odds(0.5, -0.1)


def test_draw_quote_and_wrong_sides() -> None:
    q = evaluate_quote(Bet.DRAW, {"HOME": 6.0, "DRAW": 1.36, "AWAY": 7.5}, 0.749, PARAMS)   # 3.5% margin
    assert q.offered_odds == 1.36 and q.ev == pytest.approx(1.36 * 0.749 - 1)
    with pytest.raises(InvariantError):
        evaluate_quote(Bet.UNDER05, {"NO": 1.3}, 0.73, PARAMS)                         # one side
    with pytest.raises(InvariantError):
        evaluate_quote(Bet.DRAW, {"NO": 1.3, "YES": 3.2}, 0.73, PARAMS)
