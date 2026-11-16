"""Settlement of 15' bets and performance metrics."""

import math

import pytest

from app.calculation.ev import Bet
from app.domain.enums import BetOutcome, DataStatus, FixtureStatus
from app.settlement.performance import calibration, roi, score
from app.settlement.settle import clv, result_15, settle

FIN, AB = FixtureStatus.FINISHED, FixtureStatus.ABANDONED


@pytest.mark.parametrize(("h15", "under", "draw"), [((0, 0), True, True), ((1, 1), False, True),
                                                     ((1, 0), False, False), ((0, 2), False, False)])
def test_exact_result(h15: tuple[int, int], under: bool, draw: bool) -> None:
    r = result_15(FIN, DataStatus.COMPLETE, h15, sum(h15) > 0)
    assert (r.under05, r.draw) == (under, draw)


def test_partial_and_incomplete_results() -> None:
    no_goal = result_15(FIN, DataStatus.PARTIAL_DRAW_UNKNOWN, None, False)
    assert (no_goal.under05, no_goal.draw) == (True, True)                     # no goal: 0-0, also a draw
    goal = result_15(FIN, DataStatus.PARTIAL_DRAW_UNKNOWN, None, True)
    assert (goal.under05, goal.draw) == (False, None)                          # 1-0 or 1-1: draw unknown
    unknown = result_15(FIN, DataStatus.INCOMPLETE, None, None)
    assert (unknown.under05, unknown.draw) == (None, None)                     # never assumed 0-0


def test_contradictory_stored_result_raises() -> None:
    with pytest.raises(ValueError, match="contradicts"):
        result_15(FIN, DataStatus.COMPLETE, (0, 0), True)
    with pytest.raises(ValueError, match="without a 15' score"):
        result_15(FIN, DataStatus.DERIVED, None, False)


def test_settle_win_lose_void_pending() -> None:
    zero = result_15(FIN, DataStatus.COMPLETE, (0, 0), False)
    goal = result_15(FIN, DataStatus.COMPLETE, (1, 0), True)
    assert settle(Bet.UNDER05, zero, 1.50).profit == pytest.approx(0.5)
    assert settle(Bet.DRAW, goal, 2.0, stake=2).outcome is BetOutcome.LOSE and settle(Bet.DRAW, goal, 2, 2).profit == -2
    abandoned = result_15(AB, DataStatus.INCOMPLETE, None, None)
    assert settle(Bet.UNDER05, abandoned, 1.5).outcome is BetOutcome.VOID
    assert settle(Bet.UNDER05, result_15(FIN, DataStatus.INCOMPLETE, None, None), 1.5).outcome is BetOutcome.PENDING
    with pytest.raises(ValueError):
        settle(Bet.UNDER05, zero, 1.0)
    assert clv(1.60, 1.50) == pytest.approx(1.6 / 1.5 - 1)


def test_score_and_calibration() -> None:
    pairs = [(0.7, True)] * 7 + [(0.7, False)] * 3 + [(0.75, True)] * 2
    s = score(pairs)
    assert s is not None and s.n == 12 and s.actual == pytest.approx(9 / 12)
    assert s.brier == pytest.approx((7 * 0.09 + 3 * 0.49 + 2 * 0.0625) / 12)
    assert s.log_loss == pytest.approx(-(7 * math.log(0.7) + 3 * math.log(0.3) + 2 * math.log(0.75)) / 12)
    b70, b74 = calibration(pairs, 2)
    assert (b70.lo_pts, b70.hi_pts, b70.n, b70.actual) == (70, 72, 10, 0.7) and b70.gap_pts == pytest.approx(0)
    assert (b74.lo_pts, b74.n) == (74, 2) and b74.se_pts == pytest.approx(math.sqrt(0.75 * 0.25 / 2) * 100)
    assert calibration([(1.0, True)], 2)[0].lo_pts == 98                         # p = 1 goes in the top bucket
    assert score([]) is None
    with pytest.raises(ValueError):
        score([(1.2, True)])


def test_roi() -> None:
    zero = result_15(FIN, DataStatus.COMPLETE, (0, 0), False)
    goal = result_15(FIN, DataStatus.COMPLETE, (0, 1), True)
    pending = result_15(FIN, DataStatus.INCOMPLETE, None, None)
    bets = [settle(Bet.UNDER05, zero, 1.5)] * 3 + [settle(Bet.UNDER05, goal, 1.5),
                                                   settle(Bet.UNDER05, pending, 1.5)]
    r = roi(bets)
    assert (r.bets, r.wins, r.pending, r.staked) == (4, 3, 1, 4.0)
    assert r.profit == pytest.approx(0.5) and r.roi == pytest.approx(0.125)
    assert r.break_even == pytest.approx(1 / 1.5) and r.hit_rate == 0.75
    assert roi([]).roi is None
