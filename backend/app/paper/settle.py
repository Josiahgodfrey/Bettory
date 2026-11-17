"""Settle frozen predictions from the stored 15' result (docs/PAPER-BETTING.md §6).

    every frozen prediction whose match kicked off more than SETTLE_AFTER ago:
        result unchanged since its latest settlement   -> nothing
        15' result not known yet                       -> PENDING (waits for goal minutes; never assumed 0-0)
        otherwise                                      -> a new paper.settlements row: the 15' outcomes (also
                                                          without a bet: calibration needs them) and the bet's
                                                          WIN / LOSE / VOID with its profit (app/settlement/settle.py)
    a result that changes after a settlement (a corrected score) adds a row that supersedes the previous one:
    nothing is overwritten, and the correction is visible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Engine

from app.calculation.ev import Bet
from app.domain.enums import BetOutcome, DataStatus, FixtureStatus
from app.settlement.settle import Result15, result_15, settle
from app.storage.repositories import paper as paper_repo

SETTLE_AFTER = timedelta(hours=2)  # a match is long over (and its result usually loaded) by then
_NOT_PLAYED = (FixtureStatus.ABANDONED, FixtureStatus.POSTPONED, FixtureStatus.CANCELLED)


@dataclass(frozen=True)
class SettleResult:
    settled: int  # first settlement of a prediction
    corrected: int  # a new settlement after the stored result changed
    pending: int  # kicked off, 15' result not known yet
    unchanged: int  # already settled on the current result
    inconsistent: tuple[str, ...]  # stored results that contradict themselves (data issue, not settled)


def _result(s: paper_repo.MatchResultState) -> Result15:
    h15 = (s.h15_home, s.h15_away) if s.h15_home is not None and s.h15_away is not None else None
    return result_15(FixtureStatus(s.match_status), DataStatus(s.data_status), h15, s.goal_before_15)


def settle_due(engine: Engine, now: datetime) -> SettleResult:
    settled = corrected = pending = unchanged = 0
    inconsistent: list[str] = []
    with engine.begin() as conn:
        for c in paper_repo.settlement_candidates(conn, now - SETTLE_AFTER):
            if c.last is not None and c.last == c.now:
                unchanged += 1
                continue
            try:
                res = _result(c.now)
            except ValueError as exc:
                inconsistent.append(f"{c.prediction_id}: {exc}")
                continue
            not_played = res.status in _NOT_PLAYED
            if res.under05 is None and not not_played:
                pending += 1
                continue
            outcome = profit = None
            if c.bet_market is not None and c.withdrawn:  # withdrawn before kick-off: not bet (owner decision)
                outcome, profit = BetOutcome.VOID.value, 0.0
            elif c.bet_market is not None:
                assert c.bet_odds is not None and c.bet_stake is not None
                st = settle(Bet(c.bet_market), res, c.bet_odds, c.bet_stake)
                if st.outcome is BetOutcome.PENDING:  # e.g. a draw bet with only "goal before 15'" known
                    pending += 1
                    continue
                outcome, profit = st.outcome.value, st.profit
            paper_repo.insert_settlement(conn, paper_repo.NewSettlement(
                c.prediction_id, c.prediction_hash, c.last_id, c.now.match_status, c.now.data_status,
                c.now.h15_home, c.now.h15_away, c.now.goal_before_15, res.under05, res.draw, outcome, profit))
            if c.last_id is None:
                settled += 1
            else:
                corrected += 1
    return SettleResult(settled, corrected, pending, unchanged, tuple(inconsistent))
