"""Backtest runner (DEVELOPMENT-PLAN.md 4.1, ARCHITECTURE.md 8.5): replay sessions through the live code.

    for each session (NIGHT / NOON) with fixtures in the period, in time order:
        as_of = run_start of the session (min(deadline, first kick-off - 2h) - buffer), exactly as live
        AnalysisContext(as_of).evaluate(fixture)       point-in-time reads -> models -> price -> verdict
        rank_and_cap(session verdicts)                 the 50-match cap, as live
    then, separately: read the stored results and settle

Nothing here changes a verdict: the backtest only records what the live code would have said, adds the
competition-only baseline price for comparison, and (when main-market odds exist) the estimated-value
scenario of the data doc 9. It runs in a READ ONLY transaction.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime

from sqlalchemy import Engine, text

from app.analysis.session import evaluate_session, league_baseline
from app.backtest.config import BacktestConfig
from app.backtest.estimate import assumed_quotes
from app.calculation.ev import Bet
from app.decision.layers import FixtureAnalysis, Verdict, verify
from app.domain.enums import Decision, Grade, ReconciliationStatus
from app.domain.params import Params
from app.domain.registry import Registry
from app.domain.sessions import SessionCalendar, SessionId, SessionKind
from app.settlement.settle import Result15, result_15
from app.storage.point_in_time import ReadCache
from app.storage.results import FixtureResult, SeasonShare, fixtures_between, season_shares

log = logging.getLogger(__name__)
ASSUMED_BOOKMAKER = "ASSUMED (Model C fair x (1 - margin))"


@dataclass(frozen=True)
class EstimatedBet:
    bet: Bet
    odds: float
    ev: float


@dataclass(frozen=True)
class BacktestRow:
    fixture_id: str
    competition: str
    season: str
    session: SessionId
    as_of: datetime
    kickoff_utc: datetime
    decision: Decision  # after the session cap (QUALIFIED = listed in the report)
    grade: Grade
    failed: tuple[str, ...]  # codes of the blocking checks
    error: str | None
    # probabilities of the 0-15 period
    p_under05: float | None
    p_draw: float | None
    p_model_a: float | None
    p_model_b2: float | None
    p_market: float | None  # Model C (main-market odds), when usable
    p_market_draw: float | None
    p_league_00: float | None  # competition-only baseline
    p_league_draw: float | None
    min_odds_under05: float | None
    min_odds_draw: float | None
    estimated: EstimatedBet | None  # estimated-value scenario (data doc 9), listed fixtures with Model C only
    result: Result15 | None  # None: result not usable (reconciliation CONFLICT)


@dataclass(frozen=True)
class SessionRun:
    id: SessionId
    as_of: datetime
    fixtures: int
    qualified: int  # QUALIFIED before the cap
    listed: int


@dataclass(frozen=True)
class BacktestRun:
    params_version: str
    formula_version: str
    first: date
    last: date
    competitions: tuple[str, ...]
    sessions: tuple[SessionRun, ...]
    rows: tuple[BacktestRow, ...]
    season_shares: tuple[SeasonShare, ...] = ()  # observed 15' shares of the seasons in the run (descriptive)


def _estimate(a: FixtureAnalysis, params: Params, margin: float) -> EstimatedBet | None:
    """Quote mode against the assumed bookmaker price, with the same verify() as live."""
    if a.model_c is None or not a.model_c.available:
        return None
    quotes = assumed_quotes(a.model_c, margin)
    if not quotes:
        return None
    v = verify(replace(a, quotes=quotes, bookmaker=ASSUMED_BOOKMAKER), params)
    if v.decision is not Decision.QUALIFIED or v.selected is None or v.ev is None:
        return None
    side = "NO" if v.selected is Bet.UNDER05 else "DRAW"
    return EstimatedBet(v.selected, quotes[v.selected][side], v.ev)


def _result(f: FixtureResult) -> Result15 | None:
    if f.reconciliation_status is ReconciliationStatus.CONFLICT:
        return None
    return result_15(f.status, f.data_status, f.h15, f.goal_before_15)


def _row(f: FixtureResult, sid: SessionId, as_of: datetime, a: FixtureAnalysis | None, v: Verdict,
         decision: Decision, estimated: EstimatedBet | None, params: Params) -> BacktestRow:
    league_00, league_draw = league_baseline(a, params)
    c = a.model_c if a is not None and a.model_c is not None and a.model_c.available else None
    return BacktestRow(
        fixture_id=str(f.id), competition=f.competition_code, season=f.season_label, session=sid, as_of=as_of,
        kickoff_utc=f.kickoff_utc, decision=decision, grade=v.grade if decision is Decision.QUALIFIED else Grade.SKIP,
        failed=tuple(ch.code for ch in v.failed), error=v.error, p_under05=v.p_under05, p_draw=v.p_draw,
        p_model_a=a.model_a.p00 if a is not None and a.model_a is not None else None,
        p_model_b2=a.model_b2.p00 if a is not None and a.model_b2 is not None else None,
        p_market=c.p00 if c else None, p_market_draw=c.p_draw if c else None,
        p_league_00=league_00, p_league_draw=league_draw,
        min_odds_under05=v.min_odds_under05, min_odds_draw=v.min_odds_draw, estimated=estimated, result=_result(f),
    )


def run_backtest(engine: Engine, params: Params, registry: Registry, config: BacktestConfig, first: date,
                 last: date, competitions: Sequence[str] | None = None,
                 progress: Callable[[SessionRun], None] | None = None,
                 cache: ReadCache | None = None) -> BacktestRun:
    """Replay the sessions of the dates first..last (session dates, EAT).

    `cache`: one ReadCache shared by several runs over the same period (parameter variations) - they analyse the
    same sessions at the same as_of, so the database is read once. Results are identical with or without it."""
    known = {d.code for d in registry.competitions}
    codes = tuple(sorted(competitions)) if competitions else tuple(sorted(known))
    unknown = [c for c in codes if c not in known]
    if unknown:
        raise ValueError(f"unknown competitions: {unknown}")
    cal = SessionCalendar(params)
    start = cal.times(SessionId(first, SessionKind.NIGHT)).start_utc
    end = cal.times(SessionId(last, SessionKind.NOON)).end_utc
    margin = config.estimated_value.assumed_bookmaker_margin

    rows: list[BacktestRow] = []
    sessions: list[SessionRun] = []
    with engine.connect() as conn, conn.begin():
        conn.execute(text("set transaction read only"))
        fixtures = {str(f.id): f for f in fixtures_between(conn, start, end, codes)}
        for sid, items in cal.group((k, f.kickoff_utc) for k, f in fixtures.items()).items():
            as_of = cal.run_start(sid, items[0][1])
            evaluated = evaluate_session(conn, params, registry, as_of, [fixtures[key].id for key, _ in items],
                                         cache)
            for (key, _), sv in zip(items, evaluated, strict=True):
                a, v, decision = sv.analysis, sv.verdict, sv.decision
                est = _estimate(a, params, margin) if a is not None and decision is Decision.QUALIFIED else None
                rows.append(_row(fixtures[key], sid, as_of, a, v, decision, est, params))
            qualified = sum(sv.verdict.decision is Decision.QUALIFIED for sv in evaluated)
            listed = sum(sv.decision is Decision.QUALIFIED for sv in evaluated)
            run = SessionRun(sid, as_of, len(items), qualified, listed)
            sessions.append(run)
            if progress is not None:
                progress(run)
        shares = season_shares(conn, sorted({(f.competition_code, f.season_label) for f in fixtures.values()}))
    return BacktestRun(params.params_version, params.formula_version, first, last, codes, tuple(sessions),
                       tuple(rows), tuple(shares))
