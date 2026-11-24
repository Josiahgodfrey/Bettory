"""Verification layers 0-5: one fixture -> QUALIFIED / SKIP / INSUFFICIENT_DATA / ERROR, with every reason. Pure.

Docs: DATA-SOURCES-AND-MARKET-MODEL.md 11.2, 14.5, 15, 17; FIFTEEN-MINUTE-ODDS-CALCULATION.md 8-10;
SYSTEM-DOCUMENTATION.md 7; ARCHITECTURE.md 11 (fail closed), 12.5-12.6, A14 (minimum-odds report).

    L0 SCREEN     competition baseline plausible, league P_L >= screen.league_p00_min, 15' markets offered
    L1 DATA       four full windows without INCOMPLETE matches, Models A and B v2 and the price available,
                  and the league-seasons behind the baseline trusted (no unaccepted FAILED cross-check)
    L2 MODELS     |P_A - P_B| <= value.max_model_gap_pts; |P_team - P_market| <= the same when Model C exists
    L3 VALUE      params <= 1.5: minimum odds = max((1 + EV_req) / P, Model C fair odds); EV_req = value.ev_min with
                  a market check, value.ev_min_no_market without one. With bookmaker 15' quotes: offered >= minimum
                  odds and |P - P_bookmaker| <= value.max_bookmaker_gap_pts; the higher-EV market is selected.
                  params 1.6 (`probability`): NO odds - P(0-0 at 15') >= probability.min_under05 or P(draw at 15')
                  >= probability.min_draw; the market furthest above its minimum is selected. Model C is ignored.
    L4 GRADE      A / B (grading.py); SKIP fails
    L5 RED FLAGS  favourite (odds, or win-rate proxy), early goals in recent matches, H2H, context flags

Every check is evaluated when its inputs exist, so the analysis view always shows the complete picture.
The verdict follows the first failing layer: L0 -> SKIP, L1 -> INSUFFICIENT_DATA, L2-L5 -> SKIP.
Any unexpected exception -> ERROR (never a bet).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

from app.calculation.baselines import BaselineStatus, CompetitionBaseline
from app.calculation.ev import Bet, evaluate_quote, expected_value, fair_odds
from app.calculation.models import TeamWindows
from app.calculation.models.model_a import ModelAResult
from app.calculation.models.model_b2 import ModelB2Result
from app.calculation.models.model_c import ModelCResult
from app.calculation.pricing import Price
from app.calculation.team_stats import H2HStats, StatsStatus, WindowStats
from app.decision.grading import grade, model_spread_pts
from app.decision.reasons import Check, Layer, gap, pts
from app.domain.enums import Decision, Grade
from app.domain.params import Params

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FixtureAnalysis:
    """Everything the calculation engine produced for one fixture, plus outside context."""

    fixture_id: str
    kickoff_utc: datetime
    baseline: CompetitionBaseline | None
    fifteen_minute_market: bool  # the competition's 15' markets are offered by the bookmaker
    windows: TeamWindows | None
    model_a: ModelAResult | None
    model_b2: ModelB2Result | None
    model_c: ModelCResult | None
    price: Price | None
    unavailable: tuple[str, ...] = ()  # ModelUnavailableError messages (why a model is missing)
    h2h: H2HStats | None = None  # ALL_VENUES
    favourite_odds: float | None = None  # shortest FT 1X2 win price of either team, if known
    context_flags: tuple[str, ...] = ()  # e.g. MUST_SCORE, LINEUP_ATTACKERS, ODDS_MOVEMENT (each blocks)
    quotes: Mapping[Bet, Mapping[str, float]] = field(default_factory=lambda: MappingProxyType({}))
    bookmaker: str = ""
    untrusted_data: tuple[str, ...] = ()  # league-seasons whose sources disagree too often (docs/DATA-HEALTH.md)


@dataclass(frozen=True)
class Verdict:
    fixture_id: str
    kickoff_utc: datetime
    decision: Decision
    grade: Grade
    checks: tuple[Check, ...]
    params_version: str
    pricing_version: str | None = None
    p_under05: float | None = None
    p_draw: float | None = None
    fair_under05: float | None = None
    fair_draw: float | None = None
    min_odds_under05: float | None = None  # bet only at or above these (report mode, A14)
    min_odds_draw: float | None = None
    model_spread_pts: float | None = None
    complete_sample: int | None = None  # smallest complete (exact 15') sample among the four windows
    selected: Bet | None = None  # quote mode: the qualifying market with the higher EV
    ev: float | None = None  # EV of the selected market
    error: str | None = None

    @property
    def failed(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.blocks)


# --- layers ----------------------------------------------------------------------------------------------


def _screen(a: FixtureAnalysis, p: Params) -> list[Check]:
    b = a.baseline
    out = []
    if b is not None and b.status is BaselineStatus.REVIEW:
        out.append(Check(Layer.SCREEN, "BASELINE_PLAUSIBLE", False, b.status.value, "OK",
                         "; ".join(b.reasons) + " - review the competition's data first"))
    if b is not None and b.p00_l is not None:
        out.append(Check(Layer.SCREEN, "LEAGUE_P00", b.p00_l >= p.screen.league_p00_min, pts(b.p00_l),
                         f">= {pts(p.screen.league_p00_min)}", "competition Model L P(0-0 at 15')"))
    out.append(Check(Layer.SCREEN, "FIFTEEN_MINUTE_MARKET", a.fifteen_minute_market,
                     "offered" if a.fifteen_minute_market else "not offered", "offered", "no market, no bet"))
    return out


def _window_check(name: str, w: WindowStats, history: int) -> Check:
    ok = w.status is StatsStatus.OK and w.sample_size == history
    return Check(Layer.DATA, f"WINDOW_{name}", ok, f"{w.sample_size} matches, {w.exact_sample_size} complete",
                 f"{history} matches, no INCOMPLETE", "; ".join(w.reasons))


def _data(a: FixtureAnalysis, p: Params) -> list[Check]:
    out = []
    b = a.baseline
    usable = b is not None and b.status is not BaselineStatus.INSUFFICIENT_DATA and b.lambda15 is not None
    out.append(Check(Layer.DATA, "COMPETITION_BASELINE", usable, b.status.value if b else "missing",
                     "15' values available", "; ".join(b.reasons) if b else ""))
    if a.windows is None:
        out.append(Check(Layer.DATA, "TEAM_WINDOWS", False, "missing", "4 windows"))
    else:
        h = p.data_gate.history_matches
        w = a.windows
        out += [_window_check("HOME_GENERAL", w.home_general, h), _window_check("HOME_HOME", w.home_home, h),
                _window_check("AWAY_GENERAL", w.away_general, h), _window_check("AWAY_AWAY", w.away_away, h)]
    out.append(Check(Layer.DATA, "SOURCE_AGREEMENT", not a.untrusted_data,
                     "; ".join(a.untrusted_data) or "trusted", "no unaccepted FAILED cross-check",
                     "review the disagreements, then accept or fix the data" if a.untrusted_data else ""))
    for code, obj in (("MODEL_A", a.model_a), ("MODEL_B2", a.model_b2), ("PRICE", a.price)):
        required = code != "MODEL_B2" or p.probability is None
        if not required:
            continue
        out.append(Check(Layer.DATA, f"{code}_AVAILABLE", obj is not None, "available" if obj else "missing",
                         "available", "; ".join(a.unavailable) if obj is None else ""))
    return out


def _market_available(a: FixtureAnalysis, p: Params | None = None) -> bool:
    if p is not None and p.probability is not None:
        return False  # params 1.6: probabilities from team and league data only, no odds
    return a.model_c is not None and a.model_c.available and a.price is not None and a.price.p_market_00 is not None


def _models(a: FixtureAnalysis, p: Params) -> list[Check]:
    if p.probability is not None:
        return []  # one model (A) + the league baseline: nothing to compare, nothing to block
    out = []
    limit = p.value.max_model_gap_pts
    if a.model_a is not None and a.model_b2 is not None:
        d = abs(a.model_a.p00 - a.model_b2.p00) * 100.0
        out.append(Check(Layer.MODELS, "MODEL_GAP_A_B", d <= limit, gap(d), f"<= {limit:g} pts",
                         f"P_A {pts(a.model_a.p00)} vs P_B {pts(a.model_b2.p00)}"))
    if a.price is not None and _market_available(a, p) and a.price.p_market_00 is not None:
        d = abs(a.price.p_team_00 - a.price.p_market_00) * 100.0
        out.append(Check(Layer.MODELS, "TEAM_MARKET_GAP", d <= limit, gap(d), f"<= {limit:g} pts",
                         f"P_team {pts(a.price.p_team_00)} vs P_market {pts(a.price.p_market_00)}"))
    elif p.probability is None:
        status = a.model_c.status.value if a.model_c is not None else "no main-market odds"
        out.append(Check(Layer.MODELS, "TEAM_MARKET_GAP", None, "not checked", "Model C available",
                         f"{status}: stricter EV threshold applies"))
    return out


def minimum_odds(a: FixtureAnalysis, p: Params) -> tuple[float, float, float]:
    """(min odds Under 0.5, min odds Draw, EV required)."""
    if a.price is None:
        raise ValueError("minimum odds need a price")
    market = _market_available(a, p)
    ev_req = p.value.ev_min if market else p.value.ev_min_no_market
    o = a.price.outcomes
    min_u = (1.0 + ev_req) / o.p_00
    min_d = (1.0 + ev_req) / o.p_draw
    if market and a.price.p_market_00 is not None:  # odds must also beat the market-implied fair price
        min_u = max(min_u, fair_odds(a.price.p_market_00))
        if a.price.p_market_draw is not None:
            min_d = max(min_d, fair_odds(a.price.p_market_draw))
    return min_u, min_d, ev_req


def _probability(a: FixtureAnalysis, p: Params) -> tuple[list[Check], Bet | None]:
    """Layer 3 of params 1.6: the probabilities against their minimums; no odds anywhere."""
    if a.price is None or p.probability is None:
        return [], None
    o, m = a.price.outcomes, p.probability
    margins = {Bet.UNDER05: o.p_00 - m.min_under05, Bet.DRAW: o.p_draw - m.min_draw}
    out = [Check(Layer.VALUE, "P_UNDER05", margins[Bet.UNDER05] >= 0, pts(o.p_00), f">= {pts(m.min_under05)}",
                 f"Total 15 min, Under 0.5 (fair odds {fair_odds(o.p_00):.2f})", blocking=False),
           Check(Layer.VALUE, "P_DRAW", margins[Bet.DRAW] >= 0, pts(o.p_draw), f">= {pts(m.min_draw)}",
                 f"Result 15 min, Draw (fair odds {fair_odds(o.p_draw):.2f})", blocking=False)]
    passing = [(margin, bet) for bet, margin in margins.items() if margin >= 0]
    if not passing:
        out.append(Check(Layer.VALUE, "PROBABILITY_QUALIFIES", False, "none",
                         "P(Under 0.5) or P(Draw) at its minimum"))
        return out, None
    _, bet = max(passing, key=lambda c: (c[0], c[1] is Bet.UNDER05))
    out.append(Check(Layer.VALUE, "SELECTED_MARKET", None, bet.value, "furthest above its minimum",
                     "both shown; you compare them with your betting app's odds"))
    return out, bet


def _value(a: FixtureAnalysis, p: Params) -> tuple[list[Check], Bet | None, float | None]:
    if p.probability is not None:
        checks, bet = _probability(a, p)
        return checks, bet, None
    if a.price is None:
        return [], None, None
    min_u, min_d, ev_req = minimum_odds(a, p)
    o = a.price.outcomes
    out = [Check(Layer.VALUE, "MINIMUM_ODDS", None, f"Under 0.5 >= {min_u:.2f}, Draw >= {min_d:.2f}",
                 f"EV >= {ev_req:.0%}" + (", >= Model C fair odds" if _market_available(a) else ""),
                 f"P(Under 0.5) {pts(o.p_00)}, P(Draw) {pts(o.p_draw)}")]
    if not a.quotes:
        return out, None, None  # report mode (A14): you bet only at or above the minimum odds

    candidates: list[tuple[float, Bet]] = []
    for bet, prices in a.quotes.items():
        prob, minimum = (o.p_00, min_u) if bet is Bet.UNDER05 else (o.p_draw, min_d)
        try:
            q = evaluate_quote(bet, prices, prob, p, a.bookmaker)
        except ValueError as exc:  # incomplete market, impossible price, implausible margin
            out.append(Check(Layer.VALUE, f"QUOTE_{bet.value}", False, "rejected", "valid complete market", str(exc),
                             blocking=False))
            continue
        price_ok = q.offered_odds >= minimum
        gap_ok = q.gap_pts <= p.value.max_bookmaker_gap_pts
        out.append(Check(Layer.VALUE, f"QUOTE_{bet.value}_PRICE", price_ok, f"{q.offered_odds:.2f} (EV {q.ev:+.1%})",
                         f">= {minimum:.2f}", f"{a.bookmaker} margin {q.overround:.1%}".strip(), blocking=False))
        out.append(Check(Layer.VALUE, f"QUOTE_{bet.value}_BOOKMAKER_GAP", gap_ok, gap(q.gap_pts),
                         f"<= {p.value.max_bookmaker_gap_pts:g} pts",
                         f"P {pts(prob)} vs bookmaker de-vigged {pts(q.p_bookmaker)}", blocking=False))
        if price_ok and gap_ok:
            candidates.append((expected_value(q.offered_odds, prob), bet))
    if not candidates:
        out.append(Check(Layer.VALUE, "QUOTED_MARKET_QUALIFIES", False, "none", "at least one quoted market"))
        return out, None, None
    ev, bet = max(candidates, key=lambda c: (c[0], c[1] is Bet.UNDER05))
    out.append(Check(Layer.VALUE, "SELECTED_MARKET", None, bet.value, "higher EV", f"EV {ev:+.1%}; never both"))
    return out, bet, ev


def _grade(a: FixtureAnalysis, p: Params) -> tuple[list[Check], Grade, float | None, int | None]:
    if a.model_a is None or a.windows is None:
        return [], Grade.SKIP, None, None
    if p.probability is None and a.model_b2 is None:
        return [], Grade.SKIP, None, None  # the odds-based rules (<= 1.5) need both models
    if p.probability is not None and a.model_b2 is None:  # one model: the grade is about sample completeness
        ws = (a.windows.home_general, a.windows.home_home, a.windows.away_general, a.windows.away_away)
        complete = min(w.exact_sample_size for w in ws)
        full = all(w.exact_sample_size == w.sample_size == p.data_gate.history_matches for w in ws)
        g = grade(0.0, full, p)
        history = p.data_gate.history_matches
        check = Check(Layer.GRADE, "GRADE", g is not Grade.SKIP, g.value,
                      f"A: {history} complete matches in every window; B: fewer",
                      f"one model (A); complete samples {complete}/{history}")
        return [check], g, None, complete
    probs = [a.model_a.p00, a.model_b2.p00]
    if _market_available(a, p) and a.model_c is not None and a.model_c.p00 is not None:
        probs.append(a.model_c.p00)
    spread = model_spread_pts(probs)
    ws = (a.windows.home_general, a.windows.home_home, a.windows.away_general, a.windows.away_away)
    complete = min(w.exact_sample_size for w in ws)
    full = all(w.exact_sample_size == w.sample_size == p.data_gate.history_matches for w in ws)
    g = grade(spread, full, p)
    history = p.data_gate.history_matches
    check = Check(Layer.GRADE, "GRADE", g is not Grade.SKIP, g.value,
                  f"A: <= {p.grades.a_max_gap_pts:g} pts and full samples; B: <= {p.grades.b_max_gap_pts:g} pts",
                  f"{len(probs)} models within {gap(spread)}; complete samples {complete}/{history}")
    return [check], g, spread, complete


def _red_flags(a: FixtureAnalysis, p: Params) -> list[Check]:
    rf = p.red_flags
    out = []
    if a.favourite_odds is not None and p.probability is None:
        out.append(Check(Layer.RED_FLAGS, "FAVOURITE_ODDS", not a.favourite_odds < rf.favourite_odds_below,
                         f"{a.favourite_odds:.2f}", f">= {rf.favourite_odds_below:.2f}", "shortest FT win price"))
    elif a.windows is not None:
        home_wr, away_wr = a.windows.home_home.win_rate, a.windows.away_away.win_rate
        if home_wr is not None and away_wr is not None:
            proxy = home_wr >= rf.favourite_proxy_home_win_rate and away_wr <= rf.favourite_proxy_away_win_rate
            out.append(Check(Layer.RED_FLAGS, "FAVOURITE_PROXY", not proxy,
                             f"home win rate at home {pts(home_wr)}, away win rate away {pts(away_wr)}",
                             f"not (>= {pts(rf.favourite_proxy_home_win_rate)} and "
                             f"<= {pts(rf.favourite_proxy_away_win_rate)})", "no match odds: win-rate proxy"))
    if a.windows is not None:
        for name, w in (("HOME", a.windows.home_general), ("AWAY", a.windows.away_general)):
            r = w.recent_early_goal_matches
            if r is not None:
                out.append(Check(Layer.RED_FLAGS, f"RECENT_EARLY_GOALS_{name}", r.count < rf.recent_early_goals_last5,
                                 f"{r.count}/{r.n}", f"< {rf.recent_early_goals_last5} of last {rf.recent_window}",
                                 f"{name.lower()} team, all competitions"))
    h = a.h2h
    if h is not None:
        if h.status is not StatsStatus.OK or h.early_goal is None or h.zero_zero is None:
            out.append(Check(Layer.RED_FLAGS, "H2H", None, "not usable", "complete meetings", "; ".join(h.reasons)))
        elif h.meetings >= rf.h2h_min_meetings:
            rate = h.early_goal.value or 0.0
            out.append(Check(Layer.RED_FLAGS, "H2H_EARLY_GOALS", rate < rf.h2h_early_goal_rate,
                             f"{h.early_goal.count}/{h.meetings}", f"< {pts(rf.h2h_early_goal_rate)}",
                             f"meetings in the last {p.h2h.max_years} years"))
            zz = h.zero_zero.value or 0.0
            if zz >= rf.h2h_bonus_zero_zero_rate:
                out.append(Check(Layer.RED_FLAGS, "H2H_SUPPORT", None, f"{h.zero_zero.count}/{h.meetings} 0-0 at 15'",
                                 f">= {pts(rf.h2h_bonus_zero_zero_rate)}", "supporting evidence only"))
        else:
            out.append(Check(Layer.RED_FLAGS, "H2H", None, f"{h.meetings} meetings",
                             f">= {rf.h2h_min_meetings} to count", "too few meetings: context only"))
    out += [Check(Layer.RED_FLAGS, f"CONTEXT_{flag}", False, flag, "absent") for flag in a.context_flags]
    return out


# --- decision --------------------------------------------------------------------------------------------

_DECISION_BY_LAYER = {Layer.SCREEN: Decision.SKIP, Layer.DATA: Decision.INSUFFICIENT_DATA}


def _verify(a: FixtureAnalysis, p: Params) -> Verdict:
    checks = _screen(a, p) + _data(a, p) + _models(a, p)
    value_checks, selected, ev = _value(a, p)
    grade_checks, g, spread, complete = _grade(a, p)
    checks += value_checks + grade_checks + _red_flags(a, p)

    failed = sorted({c.layer for c in checks if c.blocks})
    decision = _DECISION_BY_LAYER.get(failed[0], Decision.SKIP) if failed else Decision.QUALIFIED
    pr = a.price
    min_u = min_d = None
    if pr is not None and p.probability is None:  # params 1.6: no minimum odds, the owner's app has the odds
        min_u, min_d, _ = minimum_odds(a, p)
    for value in (min_u, min_d, ev):
        if value is not None and not math.isfinite(value):
            raise ValueError("non-finite number in verdict")
    return Verdict(
        fixture_id=a.fixture_id, kickoff_utc=a.kickoff_utc, decision=decision,
        grade=g if decision is Decision.QUALIFIED else Grade.SKIP, checks=tuple(checks),
        params_version=p.params_version, pricing_version=pr.pricing_version if pr else None,
        p_under05=pr.outcomes.p_00 if pr else None, p_draw=pr.outcomes.p_draw if pr else None,
        fair_under05=pr.fair.under05 if pr else None, fair_draw=pr.fair.draw if pr else None,
        min_odds_under05=min_u, min_odds_draw=min_d, model_spread_pts=spread, complete_sample=complete,
        selected=selected if decision is Decision.QUALIFIED else None,
        ev=ev if decision is Decision.QUALIFIED else None,
    )


def verify(a: FixtureAnalysis, params: Params) -> Verdict:
    """Fail closed (ARCHITECTURE 11): any unexpected exception -> ERROR, never a bet."""
    try:
        return _verify(a, params)
    except Exception as exc:  # deliberately broad: whatever went wrong, this fixture must not be bet
        log.exception("verification failed for fixture %s", a.fixture_id)
        return Verdict(fixture_id=a.fixture_id, kickoff_utc=a.kickoff_utc, decision=Decision.ERROR, grade=Grade.SKIP,
                       checks=(), params_version=params.params_version, error=f"{type(exc).__name__}: {exc}")
