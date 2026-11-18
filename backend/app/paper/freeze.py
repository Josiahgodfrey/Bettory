"""Freeze a session's predictions before kick-off, and check later that they replay identically.

    freeze_session   at or after the session's run_start (its as_of, exactly as in the backtest):
                     evaluate every fixture of the session with the shared session step (app/analysis/session.py)
                     and store one row per fixture that has not kicked off yet in paper.predictions
                     (append-only, hash-chained, stamped by the database clock)
    replay_session   later: evaluate the same fixtures again at the stored as_of with the stored params version
                     and compare. Identical = the record is reproducible from the data; a difference means data
                     known before as_of arrived or changed after the freeze (e.g. a late result) and is reported.
    due_sessions     the sessions a freeze run should look at now

Nothing here decides anything new: a frozen row is exactly what the live code said at as_of.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Engine, text

from app.analysis.session import SessionVerdict, evaluate_session, league_baseline
from app.decision.layers import Verdict
from app.domain.enums import Decision, FixtureStatus, Grade
from app.domain.params import Params
from app.domain.registry import Registry
from app.domain.sessions import SessionCalendar, SessionId
from app.paper.bet import PaperBet, paper_bet
from app.paper.odds import SEALED_MARKETS, SeenOdds, seen_odds
from app.storage.point_in_time import PointInTimeReader
from app.storage.repositories import paper as paper_repo

# a fixture is frozen only if it kicks off at least this long after the freeze (the database refuses <= now)
MIN_LEAD = timedelta(minutes=1)
REPLAY_TOLERANCE = 1e-9


@dataclass(frozen=True)
class Provenance:
    """What produced the prediction, stored with every row."""

    plan_file: str | None = None
    plan_sha256: str | None = None
    code_version: str | None = None


@dataclass(frozen=True)
class FreezeResult:
    session: SessionId
    status: str  # FROZEN / NO_FIXTURES / TOO_EARLY / ENDED
    as_of: datetime | None
    fixtures: int  # in the session (all evaluated)
    frozen: int  # new rows
    already: int  # frozen earlier (same fixture, session, params version)
    started: int  # kicked off before the freeze: evaluated for the cap, not frozen
    qualified: int  # listed after the cap, among the fixtures frozen now or earlier
    chain_from: int | None = None  # chain positions of the new rows
    chain_to: int | None = None
    bets: int = 0  # new rows with a paper bet


def checks_json(v: Verdict) -> tuple[dict[str, Any], ...]:
    """Every check of a verdict as stored with the prediction (the reasons shown to the owner)."""
    return tuple({"layer": int(c.layer), "layer_name": c.layer.name, "code": c.code, "passed": c.passed,
                  "value": c.value, "threshold": c.threshold, "detail": c.detail, "blocking": c.blocking}
                 for c in v.checks)


def due_sessions(cal: SessionCalendar, now: datetime) -> list[SessionId]:
    """Sessions that have not ended yet among yesterday .. tomorrow (EAT dates), in time order."""
    today = now.astimezone(cal.tz).date()
    return [sid for sid in cal.between(today - timedelta(days=1), today + timedelta(days=1))
            if cal.times(sid).end_utc > now]


def prediction_row(sv: SessionVerdict, f: paper_repo.SessionFixture, sid: SessionId, as_of: datetime,
                   params: Params, prov: Provenance, odds: SeenOdds, bet: PaperBet | None) -> paper_repo.NewPrediction:
    """The frozen row of one fixture (also the shape of a session preview, computed now and not stored)."""
    a, v = sv.analysis, sv.verdict
    c = a.model_c if a is not None and a.model_c is not None and a.model_c.available else None
    league_00, league_draw = league_baseline(a, params)
    return paper_repo.NewPrediction(
        match_id=f.match_id, competition_code=f.competition_code, kickoff_utc=f.kickoff_utc, session_id=str(sid),
        as_of=as_of, params_version=params.params_version, formula_version=params.formula_version,
        plan_file=prov.plan_file, plan_sha256=prov.plan_sha256, code_version=prov.code_version,
        decision=sv.decision.value,
        grade=(v.grade if sv.decision is Decision.QUALIFIED else Grade.SKIP).value,
        failed_checks=tuple(ch.code for ch in v.failed), error=v.error,
        p_under05=v.p_under05, p_draw=v.p_draw,
        p_model_a=a.model_a.p00 if a is not None and a.model_a is not None else None,
        p_model_b2=a.model_b2.p00 if a is not None and a.model_b2 is not None else None,
        p_market=c.p00 if c else None, p_market_draw=c.p_draw if c else None,
        p_league_00=league_00, p_league_draw=league_draw,
        min_odds_under05=v.min_odds_under05, min_odds_draw=v.min_odds_draw,
        fair_under05=v.fair_under05, fair_draw=v.fair_draw, model_spread_pts=v.model_spread_pts,
        complete_sample=v.complete_sample, checks=checks_json(v),
        odds_seen=tuple(odds.rows),
        best_under05_odds=odds.best_under05[0] if odds.best_under05 else None,
        best_under05_book=odds.best_under05[1] if odds.best_under05 else None,
        best_draw_odds=odds.best_draw[0] if odds.best_draw else None,
        best_draw_book=odds.best_draw[1] if odds.best_draw else None,
        bet_market=bet.market.value if bet else None, bet_odds=bet.odds if bet else None,
        bet_book=bet.bookmaker if bet else None, bet_ev=bet.ev if bet else None,
        bet_stake=bet.stake if bet else None)


def freeze_session(engine: Engine, params: Params, registry: Registry, sid: SessionId, *, now: datetime,
                   competitions: Sequence[str] | None = None, provenance: Provenance = Provenance(),
                   stake: float = 1.0) -> FreezeResult:
    cal = SessionCalendar(params)
    times = cal.times(sid)
    codes = tuple(sorted(competitions)) if competitions else tuple(sorted(d.code for d in registry.competitions))
    if times.end_utc <= now:
        return FreezeResult(sid, "ENDED", None, 0, 0, 0, 0, 0)
    with engine.begin() as conn:
        fixtures = paper_repo.session_fixtures(conn, times.start_utc, times.end_utc, codes)
        if not fixtures:
            return FreezeResult(sid, "NO_FIXTURES", None, 0, 0, 0, 0, 0)
        as_of = cal.run_start(sid, fixtures[0].kickoff_utc)
        if now < as_of:
            return FreezeResult(sid, "TOO_EARLY", as_of, len(fixtures), 0, 0, 0, 0)
        verdicts = evaluate_session(conn, params, registry, as_of, [f.match_id for f in fixtures])
        reader = PointInTimeReader(conn, as_of)  # the same odds read as Model C: captured strictly before as_of
        frozen = already = started = qualified = bets = 0
        positions: list[int] = []
        for f, sv in zip(fixtures, verdicts, strict=True):
            if f.status is not FixtureStatus.SCHEDULED or f.kickoff_utc < now + MIN_LEAD:
                started += 1
                continue
            rows = reader.odds(f.match_id, SEALED_MARKETS)
            bet = paper_bet(sv.analysis, sv.decision, rows, params, stake)
            row = prediction_row(sv, f, sid, as_of, params, provenance, seen_odds(rows), bet)
            pos = paper_repo.insert_prediction(conn, row)
            if pos is None:
                already += 1
            else:
                frozen += 1
                positions.append(pos)
                bets += bet is not None
            qualified += sv.decision is Decision.QUALIFIED
    return FreezeResult(sid, "FROZEN", as_of, len(fixtures), frozen, already, started, qualified,
                        min(positions, default=None), max(positions, default=None), bets)


@dataclass(frozen=True)
class ReplayDifference:
    match_id: str
    field: str
    frozen: object
    replayed: object


@dataclass(frozen=True)
class ReplayResult:
    session: str
    params_version: str
    compared: int
    differences: tuple[ReplayDifference, ...]

    @property
    def identical(self) -> bool:
        return not self.differences


def _same(a: object, b: object) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= REPLAY_TOLERANCE
    return a == b


def replay_session(engine: Engine, params: Params, registry: Registry, session_id: str) -> ReplayResult:
    """Recompute the frozen rows of one session (for `params.params_version`) and list every difference.
    The cap is not replayed (it depends on the whole session list): decisions are compared before the cap."""
    with engine.connect() as conn, conn.begin():
        conn.execute(text("set transaction read only"))
        rows = paper_repo.frozen(conn, session_id, params.params_version)
        if not rows:
            return ReplayResult(session_id, params.params_version, 0, ())
        by_as_of: dict[datetime, list[paper_repo.FrozenPrediction]] = {}
        for r in rows:
            by_as_of.setdefault(r.as_of, []).append(r)
        diffs: list[ReplayDifference] = []
        for as_of, group in by_as_of.items():
            again = evaluate_session(conn, params, registry, as_of, [r.match_id for r in group])
            reader = PointInTimeReader(conn, as_of)
            for r, sv in zip(group, again, strict=True):
                v = sv.verdict
                odds_rows = reader.odds(r.match_id, SEALED_MARKETS)
                odds = seen_odds(odds_rows)
                if odds.rows != r.odds_seen:
                    diffs.append(ReplayDifference(str(r.match_id), "odds_seen", len(r.odds_seen), len(odds.rows)))
                # the bet with the frozen (post-cap) decision: the cap is not replayed
                stake = r.bet_stake if r.bet_stake is not None else 1.0
                bet = paper_bet(sv.analysis, Decision(r.decision), odds_rows, params, stake)
                again_bet = (bet.market.value, bet.odds, bet.bookmaker) if bet else None
                if again_bet != ((r.bet_market, r.bet_odds, r.bet_book) if r.bet_market else None):
                    diffs.append(ReplayDifference(str(r.match_id), "bet", r.bet_market, again_bet))
                if list(checks_json(v)) != r.checks:
                    diffs.append(ReplayDifference(str(r.match_id), "checks", len(r.checks), len(v.checks)))
                if r.complete_sample != v.complete_sample:
                    diffs.append(ReplayDifference(str(r.match_id), "complete_sample", r.complete_sample,
                                                  v.complete_sample))
                pre_cap = r.decision if r.decision != Decision.QUALIFIED_OVER_CAP.value else Decision.QUALIFIED.value
                for name, old, new in (("p_under05", r.p_under05, v.p_under05), ("p_draw", r.p_draw, v.p_draw),
                                       ("min_odds_under05", r.min_odds_under05, v.min_odds_under05),
                                       ("min_odds_draw", r.min_odds_draw, v.min_odds_draw),
                                       ("model_spread_pts", r.model_spread_pts, v.model_spread_pts),
                                       ("decision", pre_cap, v.decision.value)):
                    if not _same(old, new):
                        diffs.append(ReplayDifference(str(r.match_id), name, old, new))
    return ReplayResult(session_id, params.params_version, len(rows), tuple(diffs))
