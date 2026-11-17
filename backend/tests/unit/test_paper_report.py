"""Paper betting report on synthetic rows (no database): the profit verdict from the e-value and min_bets, the
market's de-vigged probability, the integrity criterion, and the outputs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.backtest.config import load_backtest_config
from app.backtest.paper_report import IN_PROGRESS, market_p_under05, paper_json, render_paper, summarise_paper
from app.backtest.report import FAIL, NOT_EVALUABLE, PASS
from app.storage.repositories.paper import ChainStatus, ReportRow
from tests.support import PARAMS

CFG = load_backtest_config()
INTACT = ChainStatus(0, None, None, ())
T0 = datetime(2026, 11, 21, 12, tzinfo=UTC)
ODDS = [{"bookmaker": "Good", "market": "FIRST15_GOAL", "line": None, "selection": "NO", "odds": 1.40,
         "captured_at": "x"},
        {"bookmaker": "Good", "market": "FIRST15_GOAL", "line": None, "selection": "YES", "odds": 3.30,
         "captured_at": "x"},
        {"bookmaker": "Half", "market": "FIRST15_GOAL", "line": None, "selection": "NO", "odds": 1.50,
         "captured_at": "x"}]  # incomplete market: ignored


def row(i: int, won: bool, bet: bool = True, **kw: Any) -> ReportRow:
    base: dict[str, Any] = dict(
        chain_pos=i + 1, session_id=f"2026-11-{21 + i // 10:02d}-NOON", competition_code="ENG1",
        kickoff_utc=T0 + timedelta(hours=i), params_version="1.5", decision="QUALIFIED", grade="A",
        p_under05=0.80, p_draw=0.82, p_league_00=0.74, p_league_draw=0.78, odds_seen=ODDS,
        bet_market="UNDER05" if bet else None, bet_odds=1.40 if bet else None, bet_book="Good" if bet else None,
        bet_ev=0.12 if bet else None, bet_stake=1.0 if bet else None, settled=True, under05=won, draw=won,
        bet_outcome=("WIN" if won else "LOSE") if bet else None, profit=(0.40 if won else -1.0) if bet else None,
        corrections=0)
    base.update(kw)
    return ReportRow(**base)


def profit(rows: list[ReportRow], chain: ChainStatus = INTACT) -> Any:
    s = summarise_paper(rows, chain, CFG, PARAMS)
    return s, next(c for c in s.gate if c.name == "Profit (paper betting)")


def test_market_probability_uses_complete_markets_only() -> None:
    p = market_p_under05(row(0, True), PARAMS)
    assert p is not None and 0.68 < p < 0.72                    # 1.40 / 3.30 de-vigged; "Half" ignored
    assert market_p_under05(row(0, True, odds_seen=[]), PARAMS) is None


def test_a_real_edge_reaches_the_bar() -> None:
    rows = [row(i, i % 20 < 17) for i in range(400)]            # 85 % at odds 1.40: ROI +19 %
    s, c = profit(rows)
    assert c.status == PASS and s.e_profit.reached(CFG.paper_betting.alpha)
    assert s.bets.bets == 400 and abs((s.bets.roi or 0) - 0.19) < 1e-9 and s.bets.roi_interval is not None
    assert s.e_forecast.n == 400 and s.market_gap_pts is not None and s.market_gap_pts > 5


def test_no_edge_stays_in_progress_and_few_bets_get_no_verdict() -> None:
    fair = [row(i, i % 7 < 5) for i in range(140)]              # 71.4 % = 1 / 1.40: no edge
    assert profit(fair)[1].status == IN_PROGRESS
    few = [row(i, True) for i in range(CFG.paper_betting.min_bets - 1)]
    assert profit(few)[1].status == NOT_EVALUABLE


def test_unsettled_and_no_bet_rows_and_integrity() -> None:
    rows = [row(0, True), row(1, False, bet=False), row(2, True, settled=False, under05=None, draw=None,
                                                         bet_outcome=None, profit=None)]
    s = summarise_paper(rows, INTACT, CFG, PARAMS)
    assert (s.frozen, s.settled, s.pending, s.bets.bets, s.bets.pending) == (3, 2, 1, 1, 1)
    broken = summarise_paper(rows, ChainStatus(3, 3, "h", ((2, "content changed after recording"),)), CFG, PARAMS)
    assert broken.gate[0].name == "Record integrity" and broken.gate[0].status == FAIL
    md = render_paper(s, CFG)
    assert "## Sequential evidence" in md and "## Paper bets" in md and "Gate B" in md
    js = paper_json(s)
    assert js["bets"]["settled"] == 1 and js["chain"]["intact"] and js["e_profit"]["n"] == 1


def test_closing_line_value_of_paper_bets() -> None:
    closing = [{"bookmaker": "Good", "market": "FIRST15_GOAL", "selection": "NO", "odds": 1.30, "captured_at": "x"},
               {"bookmaker": "Good", "market": "FIRST15_GOAL", "selection": "YES", "odds": 3.60, "captured_at": "x"}]
    rows = [row(0, True, closing=closing), row(1, False, closing=closing), row(2, True)]   # the last: no line
    s = summarise_paper(rows, INTACT, CFG, PARAMS)
    assert s.bets.clv_n == 2 and s.bets.clv_beat == 1.0                    # 1.40 taken vs ~1.36 fair closing
    assert s.bets.clv_mean is not None and 0 < s.bets.clv_mean < 0.06
    assert "Closing line value" in render_paper(s, CFG) and paper_json(s)["bets"]["clv_n"] == 2
