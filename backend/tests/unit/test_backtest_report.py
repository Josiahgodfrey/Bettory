"""Backtest report and Gate B logic on hand-made rows; estimated-value quotes."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from app.backtest.config import load_backtest_config
from app.backtest.estimate import assumed_quotes
from app.backtest.report import FAIL, NOT_EVALUABLE, PASS, render_markdown, rows_csv, summarise, summary_json
from app.backtest.runner import BacktestRow, BacktestRun, EstimatedBet, SessionRun
from app.calculation.ev import Bet
from app.calculation.models.model_c import MarketStatus, ModelCResult
from app.domain.enums import DataStatus, Decision, FixtureStatus, Grade
from app.domain.sessions import SessionId, SessionKind
from app.settlement.settle import result_15

CFG = load_backtest_config()
D0 = date(2026, 1, 1)


def row(i: int, p: float, zero: bool, decision: Decision = Decision.QUALIFIED, league: float = 0.72,
        estimated: EstimatedBet | None = None) -> BacktestRow:
    day = D0 + timedelta(days=i // 2)
    sid = SessionId(day, SessionKind.NOON)
    ko = datetime(day.year, day.month, day.day, 15, tzinfo=UTC)
    h15 = (0, 0) if zero else (1, 0)
    return BacktestRow(
        fixture_id=f"f{i}", competition="ENG1" if i % 2 else "GER1", season="2025-26", session=sid,
        as_of=ko - timedelta(hours=3), kickoff_utc=ko, decision=decision,
        grade=Grade.A if decision is Decision.QUALIFIED else Grade.SKIP, failed=(), error=None,
        p_under05=p, p_draw=p + 0.02, p_model_a=p, p_model_b2=p, p_market=None, p_market_draw=None,
        p_league_00=league, p_league_draw=league + 0.02, min_odds_under05=1.08 / p, min_odds_draw=1.08 / (p + 0.02),
        estimated=estimated, result=result_15(FixtureStatus.FINISHED, DataStatus.COMPLETE, h15, not zero))


def run(rows: list[BacktestRow]) -> BacktestRun:
    sessions = tuple(SessionRun(r.session, r.as_of, 1, int(r.decision is Decision.QUALIFIED),
                                int(r.decision is Decision.QUALIFIED)) for r in rows)
    return BacktestRun("1.5", "v1.4", D0, D0 + timedelta(days=400), ("ENG1", "GER1"), sessions, tuple(rows))


def calibrated(n: int, p: float = 0.75, start: int = 0) -> list[BacktestRow]:
    """n rows predicting p whose outcomes happen exactly round(p * n) times."""
    k = round(p * n)
    return [row(start + i, p, i < k) for i in range(n)]


def gate(summary_rows: list[BacktestRow], split: date | None = None) -> dict[str, str]:
    return {g.name: g.status for g in summarise(run(summary_rows), CFG, split).gate}


def test_well_calibrated_model_beating_the_baseline() -> None:
    rows = calibrated(400)                                          # 300 / 400 = 75 %, league says 72 %
    s = summarise(run(rows), CFG)
    g = {c.name: c for c in s.gate}
    assert g["Qualified selections"].status == PASS and g["Calibration"].status == PASS
    assert g["Beats competition-only baseline"].status == PASS
    assert g["Estimated ROI on unseen period"].status == NOT_EVALUABLE and not s.gate_passed
    under = s.min_odds_roi["all"]["UNDER05"]                        # min odds 1.44 at a true 75 %: ROI +8 %
    assert under.bets == 400 and under.roi == pytest.approx(0.75 * 1.08 / 0.75 - 1)


def test_overconfident_model_fails_calibration_and_baseline() -> None:
    rows = [replace(r, p_under05=0.85) for r in calibrated(400)]   # says 85 %, actual 75 %
    g = gate(rows)
    assert g["Calibration"] == FAIL and g["Beats competition-only baseline"] == FAIL


def test_small_buckets_are_not_judged_and_few_selections_fail() -> None:
    rows = [*calibrated(40, 0.75), row(100, 0.95, False)]         # 40 < min_bucket_matches; one wild miss
    g = gate(rows)
    assert g["Calibration"] == NOT_EVALUABLE and g["Qualified selections"] == FAIL


def test_unlisted_fixtures_count_for_calibration_not_for_bets() -> None:
    listed, skipped = calibrated(100), [replace(r, decision=Decision.SKIP) for r in calibrated(100, start=100)]
    s = summarise(run(listed + skipped), CFG)
    priced, listed_score = s.calibration["UNDER05"]["priced"][0], s.calibration["UNDER05"]["listed"][0]
    assert priced is not None and listed_score is not None and (priced.n, listed_score.n) == (200, 100)
    assert s.min_odds_roi["all"]["UNDER05"].bets == 100


def test_estimated_value_on_the_validation_period() -> None:
    est = EstimatedBet(Bet.UNDER05, 1.40, 0.05)
    rows = [replace(r, estimated=est) for r in calibrated(400)]
    split = rows[200].session.day
    s = summarise(run(rows), CFG, split)
    assert set(s.estimated) == {"all", "tuning", "validation"}
    val = s.estimated["validation"]
    assert val.bets == sum(1 for r in rows if r.session.day >= split)
    assert {c.name: c.status for c in s.gate}["Estimated ROI on unseen period"] in (PASS, FAIL)


def test_outputs_render() -> None:
    s = summarise(run(calibrated(120)), CFG, D0 + timedelta(days=30))
    md = render_markdown(s, CFG)
    assert "## Gate B" in md and "| Final price |" in md and "Gate B: **NOT PASSED**" in md
    csv_text = rows_csv(s.run.rows)
    assert csv_text.splitlines()[0].startswith("fixture_id,") and len(csv_text.splitlines()) == 121
    js = summary_json(s)
    assert js["fixtures"] == 120 and js["gate_b_passed"] is False and js["split"] == "2026-01-31"


def test_assumed_quotes() -> None:
    c = ModelCResult(status=MarketStatus.OK, reasons=(), lambda15=0.32, lambda15_home=0.18, lambda15_away=0.14,
                     p00=0.726, p_draw=0.75)
    q = assumed_quotes(c, 0.07)
    assert q[Bet.UNDER05]["NO"] == pytest.approx(0.93 / 0.726) and q[Bet.UNDER05]["NO"] < 1 / 0.726
    assert set(q[Bet.DRAW]) == {"DRAW", "HOME", "AWAY"}
    assert assumed_quotes(replace(c, p00=0.95), 0.07).get(Bet.UNDER05) is None     # 0.93 / 0.95 < 1: no price
    assert assumed_quotes(replace(c, status=MarketStatus.MARKET_INCONSISTENT), 0.07) == {}
    with pytest.raises(ValueError):
        assumed_quotes(c, 1.0)
