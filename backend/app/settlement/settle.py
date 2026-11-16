"""Settle bets from the actual 15' result: WIN / LOSE / VOID / PENDING, profit, CLV. Pure.

FIFTEEN-MINUTE-ODDS-CALCULATION.md 12 (settlement conventions), DATA-MODEL.md 7 (data status):

    COMPLETE / DERIVED        exact 15' score known          -> Under 0.5 and Draw both settle
    PARTIAL_DRAW_UNKNOWN      only "goal before 15'" known   -> Under 0.5 settles; Draw settles only when
                                                                there was no goal (0-0 is a draw)
    INCOMPLETE / PENDING      nothing known                  -> PENDING (never assumed 0-0)
    ABANDONED, POSTPONED, CANCELLED without a known 15' result -> VOID (stake returned)

The cutoff itself (00:00-14:59) is applied when goals are turned into the stored 15' score (recovery.py);
this module only reads that score.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.calculation.ev import Bet
from app.domain.enums import BetOutcome, DataStatus, FixtureStatus

_EXACT = (DataStatus.COMPLETE, DataStatus.DERIVED)
_NOT_PLAYED = (FixtureStatus.ABANDONED, FixtureStatus.POSTPONED, FixtureStatus.CANCELLED)


@dataclass(frozen=True)
class Result15:
    """What is known about one match's 0-15 period. None = unknown."""

    status: FixtureStatus
    data_status: DataStatus
    under05: bool | None  # True: no goal before the cutoff (15' score 0-0)
    draw: bool | None

    def outcome(self, bet: Bet) -> bool | None:
        return self.under05 if bet is Bet.UNDER05 else self.draw


def result_15(status: FixtureStatus, data_status: DataStatus, h15: tuple[int, int] | None,
              goal_before_15: bool | None) -> Result15:
    under = draw = None
    if data_status in _EXACT:
        if h15 is None:
            raise ValueError(f"{data_status} match without a 15' score")
        under, draw = h15 == (0, 0), h15[0] == h15[1]
        if goal_before_15 is not None and goal_before_15 == under:
            raise ValueError(f"15' score {h15} contradicts goal_before_15={goal_before_15}")
    elif data_status is DataStatus.PARTIAL_DRAW_UNKNOWN and goal_before_15 is not None:
        under = not goal_before_15
        draw = True if under else None
    return Result15(status, data_status, under, draw)


@dataclass(frozen=True)
class Settlement:
    bet: Bet
    odds: float
    stake: float
    outcome: BetOutcome
    profit: float  # 0 for VOID and PENDING


def settle(bet: Bet, result: Result15, odds: float, stake: float = 1.0) -> Settlement:
    if not odds > 1.0 or not stake > 0:
        raise ValueError(f"odds must be > 1 and stake > 0, got {odds}, {stake}")
    won = result.outcome(bet)
    if won is None:
        outcome = BetOutcome.VOID if result.status in _NOT_PLAYED else BetOutcome.PENDING
        return Settlement(bet, odds, stake, outcome, 0.0)
    if won:
        return Settlement(bet, odds, stake, BetOutcome.WIN, stake * (odds - 1.0))
    return Settlement(bet, odds, stake, BetOutcome.LOSE, -stake)


def clv(taken_odds: float, closing_odds: float) -> float:
    """Closing line value: taken / closing - 1 (positive = we beat the closing price)."""
    if not taken_odds > 1.0 or not closing_odds > 1.0:
        raise ValueError("odds must be > 1")
    return taken_odds / closing_odds - 1.0
