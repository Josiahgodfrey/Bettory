"""Margin removal: every method, validation of prices and margins, three-way markets."""

import math

import pytest

from app.calculation.devig import MarketError, check_overround, devig, implied_probabilities, overround
from app.domain.enums import DevigMethod

ALL = list(DevigMethod)


@pytest.mark.parametrize("method", ALL)
@pytest.mark.parametrize("odds", [(1.30, 3.20), (1.36, 2.95), (2.10, 3.40, 3.60), (1.08, 7.5)])
def test_probabilities_sum_to_one_and_keep_order(method: DevigMethod, odds: tuple[float, ...]) -> None:
    d = devig(odds, method)
    assert math.fsum(d.probabilities) == pytest.approx(1.0, abs=1e-12)
    assert list(d.probabilities) == sorted(d.probabilities, reverse=True) or len(odds) == 3
    ranked = sorted(range(len(odds)), key=lambda i: odds[i])                    # shortest price = most likely
    assert ranked == sorted(range(len(odds)), key=lambda i: -d.probabilities[i])
    assert d.overround == pytest.approx(overround(odds))


def test_power_and_shin_load_margin_on_the_longshot() -> None:
    odds = (1.30, 3.20)
    mult, power, shin = (devig(odds, m).probabilities[0] for m in
                         (DevigMethod.MULTIPLICATIVE, DevigMethod.POWER, DevigMethod.SHIN))
    assert mult < shin and mult < power                         # favourite gets more than its even share
    assert abs(shin - power) < 0.015                            # doc: "close to power" - about 1 pt at 8% margin


def test_zero_margin_market_is_unchanged() -> None:
    for method in (DevigMethod.MULTIPLICATIVE, DevigMethod.POWER):
        d = devig((2.0, 2.0), method)
        assert d.probabilities == pytest.approx((0.5, 0.5)) and d.overround == 0.0
    assert devig((2.0, 2.0), DevigMethod.POWER).parameter == pytest.approx(1.0)


def test_shin_needs_a_margin() -> None:
    with pytest.raises(MarketError):
        devig((2.0, 2.0), DevigMethod.SHIN)


@pytest.mark.parametrize("odds", [
    (1.30,),                      # one side cannot reveal the margin
    (),
    (1.0, 3.0),                   # price of 1 is impossible
    (0.95, 3.0),
    (1.3, math.inf),
    (1.3, math.nan),
    (1.3, "3.2"),
    (1.3, True),
])
def test_invalid_prices_rejected(odds: tuple[object, ...]) -> None:
    with pytest.raises(MarketError):
        devig(odds)  # type: ignore[arg-type]


def test_implausible_margins_rejected() -> None:
    with pytest.raises(MarketError, match="overround"):
        devig((1.10, 2.50))                                     # 31% margin: wrong prices or mixed sides
    with pytest.raises(MarketError, match="overround"):
        devig((2.20, 2.20))                                     # negative margin: mixed bookmakers / arbitrage
    assert devig((2.20, 2.20), min_overround=-0.1).probabilities == pytest.approx((0.5, 0.5))
    assert check_overround(0.08) == 0.08


def test_implied_probabilities() -> None:
    assert implied_probabilities((2.0, 4.0)) == (0.5, 0.25)
