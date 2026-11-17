"""Real bets and the odds check (docs/API.md step 3).

    quote_card    the owner enters one bookmaker's 15' prices -> the live quote check at the session's cut-off:
                  which market to bet (never both), EV, pass / fail with reasons; the prices are stored
    record_bet    a placed bet; rule breaches are recorded with it, never refused (owner decision 2026-11-17)
    bet_json      a bet with its settlement, read from the match's stored 15' result every time
    performance   ROI with a session-bootstrap interval, hit rate vs break-even, by market / competition / grade /
                  rule-following, the profit curve
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Connection

from app.analysis.builder import AnalysisContext
from app.api.views import fixture_json, jsonable, result_of
from app.backtest.config import BacktestConfig
from app.calculation.ev import Bet
from app.decision.layers import verify
from app.domain.betting_config import BettingConfig
from app.domain.enums import BetOutcome, DataStatus, Decision, FixtureStatus, MarketCode
from app.domain.params import Params
from app.domain.registry import Registry
from app.domain.sessions import SessionCalendar
from app.paper.freeze import checks_json
from app.settlement.performance import Roi, roi
from app.settlement.settle import Settlement, result_15, settle
from app.settlement.statistics import block_bootstrap
from app.storage.repositories import betting as bet_repo
from app.storage.repositories import frontend as fe_repo
from app.storage.repositories import paper as paper_repo

FUTURE_TOLERANCE = timedelta(minutes=5)
MANUAL_MARKETS = {Bet.UNDER05: (MarketCode.FIRST15_GOAL, "Goal in 1-15 minutes (entered)"),
                  Bet.DRAW: (MarketCode.FIRST15_RESULT, "1x2 - 15 minutes (entered)")}
OFFERED = {Bet.UNDER05: "NO", Bet.DRAW: "DRAW"}


class BetRefused(ValueError):
    """Input that cannot be a real bet (unknown match, a bet in the future, an implausible stake)."""


def _frozen_for(conn: Connection, params: Params, match_id: UUID,
                session_id: str) -> paper_repo.FrozenPrediction | None:
    rows = [r for r in paper_repo.frozen(conn, session_id, params.params_version) if r.match_id == match_id]
    return rows[0] if rows else None


# --- the odds check --------------------------------------------------------------------------------------------


def quote_card(conn: Connection, params: Params, registry: Registry, match_id: UUID,
               quotes: Mapping[Bet, Mapping[str, float]], bookmaker: str, now: datetime,
               *, store: bool) -> dict[str, Any]:
    cal = SessionCalendar(params)
    f = fe_repo.match(conn, match_id)
    if f is None:
        raise LookupError(f"unknown match {match_id}")
    sid = str(cal.session_of(f["kickoff_utc"]))
    frozen = _frozen_for(conn, params, match_id, sid)
    as_of, mode = (frozen.as_of, "FROZEN") if frozen else (now, "PREVIEW")
    analysis, base = AnalysisContext(conn, params, registry, as_of).evaluate(match_id)
    v = verify(replace(analysis, quotes=quotes, bookmaker=bookmaker), params) if analysis is not None else base
    listed = (frozen.decision == Decision.QUALIFIED.value) if frozen else None
    started = f["status"] != FixtureStatus.SCHEDULED.value or f["kickoff_utc"] <= now
    stored = 0
    if store:
        book = bet_repo.bookmaker_id(conn, bookmaker)
        rows = [(MANUAL_MARKETS[b][0].value, MANUAL_MARKETS[b][1], sel, odds)
                for b, prices in quotes.items() for sel, odds in prices.items()]
        stored = bet_repo.insert_manual_odds(conn, match_id, book, rows, now)

    withdrawn = _withdrawn_at(conn, params, match_id)
    if started:
        advice, reason = "NO_BET", "the match has started"
    elif withdrawn is not None:
        advice, reason = "NO_BET", f"the match was withdrawn by the re-check at {withdrawn:%H:%M} UTC"
    elif listed is False:
        advice, reason = "NO_BET", "the match is not on the published session report"
    elif v.decision is not Decision.QUALIFIED or v.selected is None:
        advice, reason = "NO_BET", "no quoted market passes the price checks (see checks)"
    elif listed is None:
        advice, reason = "WAIT", "the session report is not published yet: this is a preview"
    else:
        advice, reason = "BET", f"bet {v.selected.value} at {quotes[v.selected][OFFERED[v.selected]]:.2f} or more"
    return {
        "match_id": str(match_id), "session_id": sid, "mode": mode, "as_of_utc": jsonable(as_of),
        "bookmaker": bookmaker, "fixture": fixture_json(f, cal),
        "listed": listed, "report_decision": frozen.decision if frozen else None,
        "decision": v.decision.value, "advice": advice, "reason": reason,
        "selected_market": v.selected.value if v.selected else None,
        "selected_odds": quotes[v.selected][OFFERED[v.selected]] if v.selected else None,
        "ev": v.ev, "p_under05": v.p_under05, "p_draw": v.p_draw,
        "min_odds_under05": v.min_odds_under05, "min_odds_draw": v.min_odds_draw,
        "checks": list(checks_json(v)), "stored_prices": stored,
    }


# --- placed bets -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BetInput:
    match_id: UUID
    market: Bet
    odds: float
    stake: float
    bookmaker: str
    placed_at: datetime | None
    note: str | None


def record_bet(conn: Connection, params: Params, cfg: BettingConfig, b: BetInput, now: datetime) -> UUID:
    f = fe_repo.match(conn, b.match_id)
    if f is None:
        raise LookupError(f"unknown match {b.match_id}")
    placed = b.placed_at or now
    if placed > now + FUTURE_TOLERANCE:
        raise BetRefused("placed_at is in the future")
    if b.stake > cfg.max_stake:
        raise BetRefused(f"stake {b.stake:g} is above the sanity bound {cfg.max_stake:g} {cfg.currency} "
                         "(config/betting.yaml max_stake): a typing error?")
    sid = str(SessionCalendar(params).session_of(f["kickoff_utc"]))
    frozen = _frozen_for(conn, params, b.match_id, sid)
    breaches: list[str] = []
    if frozen is None or frozen.decision != Decision.QUALIFIED.value:
        breaches.append("NOT_LISTED")
    min_odds = None
    if frozen is not None:
        min_odds = frozen.min_odds_under05 if b.market is Bet.UNDER05 else frozen.min_odds_draw
    if min_odds is not None and b.odds < min_odds - 1e-9:
        breaches.append("BELOW_MIN_ODDS")
    if any(m != b.market.value for m in bet_repo.active_markets(conn, b.match_id)):
        breaches.append("BOTH_MARKETS")
    if placed >= f["kickoff_utc"]:
        breaches.append("AFTER_KICKOFF")
    w = _withdrawn_at(conn, params, b.match_id)
    if w is not None and placed >= w:
        breaches.append("WITHDRAWN")
    return bet_repo.insert_bet(conn, bet_repo.NewBet(
        match_id=b.match_id, session_id=sid, prediction_id=_prediction_id(conn, frozen), market=b.market.value,
        odds=b.odds, stake=b.stake, currency=cfg.currency, bookmaker=b.bookmaker, placed_at=placed,
        min_odds=min_odds, rule_breaches=tuple(breaches), note=b.note))


def _withdrawn_at(conn: Connection, params: Params, match_id: UUID) -> datetime | None:
    r = paper_repo.latest_rechecks(conn, [match_id], params.params_version).get(match_id)
    return r["checked_at"] if r and r["status"] == "WITHDRAWN" else None


def _prediction_id(conn: Connection, frozen: paper_repo.FrozenPrediction | None) -> UUID | None:
    if frozen is None:
        return None
    return paper_repo.prediction_id(conn, frozen.chain_pos)


def settlement_of(row: Mapping[str, Any]) -> Settlement | None:
    """None for a cancelled bet."""
    if row["cancelled_at"] is not None:
        return None
    h15 = (row["h15_home"], row["h15_away"]) if row["h15_home"] is not None else None
    try:
        r = result_15(FixtureStatus(row["status"]), DataStatus(row["data_status"]), h15, row["goal_before_15"])
    except ValueError:  # contradictory stored result: wait (the data-health checks report it)
        return Settlement(Bet(row["market"]), float(row["odds"]), float(row["stake"]), BetOutcome.PENDING, 0.0)
    return settle(Bet(row["market"]), r, float(row["odds"]), float(row["stake"]))


def bet_json(row: Mapping[str, Any]) -> dict[str, Any]:
    s = settlement_of(row)
    out: dict[str, Any] = jsonable({k: v for k, v in row.items()
                                    if k not in ("h15_home", "h15_away", "goal_before_15", "data_status")})
    out["odds"], out["stake"] = float(row["odds"]), float(row["stake"])
    out["state"] = "CANCELLED" if s is None else s.outcome.value
    out["profit"] = None if s is None else s.profit
    out["result_15"] = result_of(row)
    out["follows_rules"] = not row["rule_breaches"]
    return out


# --- performance -----------------------------------------------------------------------------------------------


def _roi_json(r: Roi) -> dict[str, Any]:
    return {"settled": r.bets, "wins": r.wins, "voids": r.voids, "pending": r.pending, "staked": r.staked,
            "profit": r.profit, "roi": r.roi, "hit_rate": r.hit_rate, "break_even": r.break_even}


def performance(rows: Sequence[Mapping[str, Any]], cfg: BettingConfig, bt: BacktestConfig) -> dict[str, Any]:
    active = [(r, s) for r in rows if (s := settlement_of(r)) is not None]
    u = bt.uncertainty

    def summary(items: Sequence[tuple[Mapping[str, Any], Settlement]]) -> dict[str, Any]:
        out = _roi_json(roi([s for _, s in items]))
        blocks: dict[str, list[tuple[float, float]]] = {}
        for r, s in items:
            if s.outcome in (BetOutcome.WIN, BetOutcome.LOSE):
                blocks.setdefault(r["session_id"], []).append((s.profit, s.stake))
        iv = block_bootstrap(list(blocks.values()), u.bootstrap_samples, u.confidence, u.seed)
        out["roi_interval"] = None if iv is None else {"low": iv.low, "high": iv.high, "sessions": iv.blocks,
                                                       "reliable": iv.blocks >= u.min_sessions}
        return out

    def by(key: str) -> dict[str, Any]:
        groups: dict[str, list[tuple[Mapping[str, Any], Settlement]]] = {}
        for r, s in active:
            groups.setdefault(str(r.get(key) or "NONE"), []).append((r, s))
        return {k: summary(v) for k, v in sorted(groups.items())}

    curve, total = [], 0.0
    for r, s in sorted(active, key=lambda rs: (rs[0]["kickoff_utc"], str(rs[0]["id"]))):
        if s.outcome in (BetOutcome.WIN, BetOutcome.LOSE):
            total += s.profit
            curve.append({"bet_id": str(r["id"]), "kickoff_utc": jsonable(r["kickoff_utc"]), "profit": s.profit,
                          "cumulative": total})
    rules = {"FOLLOWED": [(r, s) for r, s in active if not r["rule_breaches"]],
             "BROKEN": [(r, s) for r, s in active if r["rule_breaches"]]}
    return {"currency": cfg.currency, "bets": len(active), "cancelled": len(rows) - len(active),
            "overall": summary(active), "by_market": by("market"), "by_competition": by("competition_code"),
            "by_grade": by("grade"), "by_rules": {k: summary(v) for k, v in rules.items()},
            "profit_curve": curve, "confidence": u.confidence}
