"""Model C: lines -> lambda15, 1X2 split, consistency check, priority, fail-closed inputs."""

import math

import pytest

from app.calculation.invariants import InvariantError
from app.calculation.models.model_c import MarketQuote, MarketStatus, ModelCResult, fit_mu
from app.calculation.models.model_c import model_c as _model_c
from app.calculation.poisson import asian_whole_under_share, cdf, score_grid
from app.domain.enums import DevigMethod, MarketCode
from tests.support import PARAMS, params


def model_c(quotes: list[MarketQuote], r: float | None, s: float | None,
            method: DevigMethod | None = None) -> ModelCResult:
    """Model C with the active params; `method` overrides model_c.devig_method."""
    p = PARAMS if method is None else params(model_c__devig_method=method)
    return _model_c(quotes, r, s, p)

R, S = 0.28, 0.12
M = 1.06  # proportional 6% margin used to build realistic prices


def ou(market: MarketCode, line: float, lam: float, margin: float = M) -> MarketQuote:
    """Prices of a totals line for a true total lambda (margin spread proportionally)."""
    whole = float(line).is_integer()
    p_under = asian_whole_under_share(int(line), lam) if whole else cdf(int(line - 0.5), lam)
    return MarketQuote(market, {"UNDER": 1 / (p_under * margin), "OVER": 1 / ((1 - p_under) * margin)}, line)


def x12(mu_h: float, mu_a: float, margin: float = M) -> MarketQuote:
    out = score_grid(mu_h, mu_a).outcomes()
    return MarketQuote(MarketCode.FT_1X2, {"HOME": 1 / (out.p_home_lead * margin),
                                           "DRAW": 1 / (out.p_draw * margin), "AWAY": 1 / (out.p_away_lead * margin)})


def test_data_doc_6_3_worked_example() -> None:
    """HT Over 0.5 @ 1.40, Under @ 2.80 -> 33.3% -> lambda_HT 1.10 -> lambda15 0.31 -> 73.5%, fair 1.36."""
    q = MarketQuote(MarketCode.HT_TOTAL, {"OVER": 1.40, "UNDER": 2.80}, 0.5)
    c = model_c([q], r=0.28, s=None, method=DevigMethod.MULTIPLICATIVE)     # the doc example de-vigs this way
    (line,) = c.lines
    assert round(line.p_under, 3) == 0.333 and round(line.lam_period, 2) == 1.10
    assert c.lambda15 is not None and round(c.lambda15, 2) == 0.31
    assert c.p00 is not None and round(c.p00, 3) == 0.735 and round(1 / c.p00, 2) == 1.36
    assert c.status is MarketStatus.OK and c.p_draw is None                  # no 1X2: Draw not priced
    power = model_c([q], r=0.28, s=None)                                     # system default: power
    assert power.p00 is not None and round(power.p00, 3) == 0.723


HT_LINES = [(MarketCode.HT_TOTAL, 0.5), (MarketCode.HT_TOTAL, 1.5), (MarketCode.HT_ASIAN_TOTAL, 1.0)]


def test_agreeing_ht_lines_are_averaged() -> None:
    quotes = [ou(m, line, 1.10) for m, line in HT_LINES]
    exact = model_c(quotes, R, S, method=DevigMethod.MULTIPLICATIVE)   # same margin model as the prices
    assert exact.status is MarketStatus.OK and len(exact.lines) == 3
    assert exact.source == "HT_TOTAL 0.5 + HT_TOTAL 1.5 + HT_ASIAN_TOTAL 1"
    for e in exact.lines:
        assert e.lam_period == pytest.approx(1.10, rel=1e-6)
    assert exact.lambda15 == pytest.approx(R * 1.10, rel=1e-6)
    power = model_c(quotes, R, S)                                       # default: power de-vig
    assert power.status is MarketStatus.OK and power.disagreement is not None and power.disagreement < 0.20


def test_inconsistent_lines_switch_the_model_off() -> None:
    c = model_c([ou(MarketCode.HT_TOTAL, 0.5, 1.0), ou(MarketCode.HT_TOTAL, 1.5, 1.4)], R, S)
    assert c.status is MarketStatus.MARKET_INCONSISTENT and not c.available and c.p00 is None
    assert "disagree" in c.reasons[-1]


def test_ht_lines_have_priority_over_ft() -> None:
    c = model_c([ou(MarketCode.FT_TOTAL, 2.5, 2.6), ou(MarketCode.HT_TOTAL, 0.5, 2.6 * 0.44)], R, S * 1.0)
    assert c.status is MarketStatus.OK and c.source == "HT_TOTAL 0.5"
    ft_only = model_c([ou(MarketCode.FT_TOTAL, 2.5, 2.6)], R, S)
    assert ft_only.source == "FT_TOTAL 2.5" and ft_only.lambda15 == pytest.approx(S * 2.6, rel=0.03)


def test_1x2_fit_recovers_mu() -> None:
    q = x12(1.6, 1.0)
    p = tuple(1 / o / sum(1 / v for v in q.prices.values()) for o in (q.prices["HOME"], q.prices["DRAW"],
                                                                         q.prices["AWAY"]))
    mu_h, mu_a, rmse = fit_mu(p)  # type: ignore[arg-type]
    assert mu_h == pytest.approx(1.6, rel=1e-3) and mu_a == pytest.approx(1.0, rel=1e-3) and rmse < 1e-4
    with_total = fit_mu(p, [(2.5, cdf(2, 2.6))])  # type: ignore[arg-type]
    assert with_total[0] + with_total[1] == pytest.approx(2.6, rel=1e-3)


def test_split_from_1x2_and_total_from_ht() -> None:
    c = model_c([x12(1.6, 1.0), ou(MarketCode.HT_TOTAL, 0.5, 1.15)], R, S)
    assert c.status is MarketStatus.OK and c.home_share is not None and c.home_share > 0.55
    assert c.lambda15_home is not None and c.lambda15_away is not None and c.lambda15 is not None
    assert c.lambda15_home + c.lambda15_away == pytest.approx(c.lambda15)
    assert c.p_draw is not None and c.p00 is not None and c.p_draw > c.p00
    assert c.p00 == pytest.approx(math.exp(-c.lambda15))


def test_1x2_only() -> None:
    c = model_c([x12(1.4, 1.2)], R, S, method=DevigMethod.MULTIPLICATIVE)
    assert c.status is MarketStatus.OK and c.source == "FT_1X2"
    assert c.lambda15 == pytest.approx(S * 2.6, rel=1e-3) and c.p_draw is not None
    assert model_c([x12(1.4, 1.2)], R, None).status is MarketStatus.UNAVAILABLE


def test_unusable_quotes_are_reported_not_used() -> None:
    bad_margin = MarketQuote(MarketCode.HT_TOTAL, {"OVER": 1.10, "UNDER": 2.00}, 0.5)       # 41% margin
    c = model_c([bad_margin, ou(MarketCode.FT_TOTAL, 2.5, 2.6)], R, S)
    assert c.status is MarketStatus.OK and c.source == "FT_TOTAL 2.5"
    assert any("overround" in r for r in c.reasons)
    no_r = model_c([ou(MarketCode.HT_TOTAL, 0.5, 1.1)], None, S)
    assert no_r.status is MarketStatus.UNAVAILABLE and any("no r" in r for r in no_r.reasons)
    assert model_c([], R, S).status is MarketStatus.UNAVAILABLE


@pytest.mark.parametrize("quote", [
    MarketQuote(MarketCode.FIRST15_GOAL, {"OVER": 3.2, "UNDER": 1.3}, 0.5),     # the bet market itself
    MarketQuote(MarketCode.HT_TOTAL, {"OVER": 1.4}, 0.5),                       # one side
    MarketQuote(MarketCode.HT_TOTAL, {"OVER": 1.4, "UNDER": 2.8}),              # no line
    MarketQuote(MarketCode.HT_TOTAL, {"OVER": 1.4, "UNDER": 2.8}, 1.0),         # whole line on O/U market
    MarketQuote(MarketCode.HT_ASIAN_TOTAL, {"OVER": 1.9, "UNDER": 1.9}, 0.75),  # quarter line
    MarketQuote(MarketCode.FT_1X2, {"HOME": 2.0, "AWAY": 3.0}),                 # missing draw
])
def test_wrong_inputs_raise(quote: MarketQuote) -> None:
    with pytest.raises(InvariantError):
        model_c([quote], R, S)


def test_only_documented_lines_are_used() -> None:
    c = model_c([ou(MarketCode.HT_TOTAL, 0.5, 1.1), ou(MarketCode.HT_TOTAL, 2.5, 1.1),
                 ou(MarketCode.FT_TOTAL, 5.5, 2.6)], R, S)
    assert c.status is MarketStatus.OK and c.source == "HT_TOTAL 0.5" and len(c.lines) == 1
    assert sum("not a Model C input" in r for r in c.reasons) == 2
