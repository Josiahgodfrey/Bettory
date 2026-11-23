"""Decision layer: every layer's pass and fail paths, quote mode, red flags, fail-closed, ranking, cap, re-check.

Each test starts from one fully qualifying fixture (`good()`) and changes one input, so the failing rule
is always the one under test.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.calculation.baselines import BaselineStatus, CompetitionBaseline
from app.calculation.ev import Bet
from app.calculation.models import Expectancy, Strength, TeamWindows
from app.calculation.models.model_a import combine
from app.calculation.models.model_b2 import BaselineMethod, ModelB2Result
from app.calculation.models.model_c import MarketStatus, ModelCResult
from app.calculation.models.model_l import model_l_from_strength
from app.calculation.pricing import price
from app.calculation.team_stats import H2HScope, H2HStats, Rate, StatsStatus, View, WindowStats
from app.decision import layers
from app.decision.grading import grade, model_spread_pts
from app.decision.layers import FixtureAnalysis, Verdict, verify
from app.decision.ranking import rank_and_cap, recheck
from app.domain.enums import Decision, Grade
from tests.support import LIVE, PARAMS, params

KO = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
S = 0.125


def window(view: View, *, n: int = 10, exact: int | None = None, wins: int = 4, recent: int = 1,
           status: StatsStatus = StatsStatus.OK) -> WindowStats:
    ok = status is StatsStatus.OK
    return WindowStats(view=view, status=status, reasons=() if ok else ("1 INCOMPLETE matches",), sample_size=n,
                       exact_sample_size=n if exact is None else exact, first_kickoff=None, last_kickoff=None,
                       wins=wins, draws=3, losses=n - wins - 3, goals_scored=12, goals_conceded=10,
                       ht_goals_scored=5, ht_goals_conceded=4,
                       zero_zero=Rate(7, n) if ok else None, level=Rate(7, n) if ok else None,
                       early_goal=Rate(3, n) if ok else None, early_scored=2 if ok else None,
                       early_conceded=1 if ok else None, recent_early_goal_matches=Rate(recent, 5) if ok else None)


def windows(**overrides: WindowStats) -> TeamWindows:
    base = {"home_general": window(View.GENERAL), "home_home": window(View.HOME),
            "away_general": window(View.GENERAL), "away_away": window(View.AWAY, wins=2)}
    return TeamWindows(**{**base, **overrides})


def baseline(**kw: Any) -> CompetitionBaseline:
    values: dict[str, Any] = {"status": BaselineStatus.OK, "reasons": (), "matches": 380, "seasons": ("2025-26",),
                              "avg_home_goals": 1.45, "avg_away_goals": 1.15, "avg_ht_home_goals": 0.64,
                              "avg_ht_away_goals": 0.51, "ht_share": 0.44, "s": S, "r": 0.28, "lambda15": 0.32,
                              "lambda15_l": 0.32, "p00_l": 0.726}
    values.update(kw)
    return CompetitionBaseline(**values)


def model_a(mu_home: float = 1.40, mu_away: float = 1.10):  # type: ignore[no-untyped-def]
    l_res = model_l_from_strength(Strength(1.0, 1.0, 1.0, 1.0), 0.64, 0.51, 0.28)
    return combine(Expectancy(home=mu_home, away=mu_away), S, l_res, ft_weight=1.0)   # P_A = e^-0.3125 = 73.2%


def model_b2(p00: float = 0.740) -> ModelB2Result:
    return ModelB2Result(1.0, 1.0, 1.0, 1.0, 0.32, BaselineMethod.OWN, 0.30, p00, ())


def model_c(p00: float = 0.735, p_draw: float = 0.752) -> ModelCResult:
    return ModelCResult(MarketStatus.OK, (), lambda15=0.308, lambda15_home=0.17, lambda15_away=0.138,
                        p00=p00, p_draw=p_draw, source="HT_TOTAL 0.5")


def good(**kw: Any) -> FixtureAnalysis:
    """A fixture that passes every layer in report mode (no market odds)."""
    a, b = kw.pop("model_a", model_a()), kw.pop("model_b2", model_b2())
    c = kw.pop("model_c", None)
    values: dict[str, Any] = {
        "fixture_id": "f1", "kickoff_utc": KO, "baseline": baseline(), "fifteen_minute_market": True,
        "windows": windows(), "model_a": a, "model_b2": b, "model_c": c,
        "price": price(a, S, kw.pop("price_params", PARAMS), b, c),
    }
    values.update(kw)
    return FixtureAnalysis(**values)


def failed_codes(v: Verdict) -> set[str]:
    """Checks that decided the fixture."""
    return {c.code for c in v.failed}


def missed_codes(v: Verdict) -> set[str]:
    """Failed checks that did not decide the fixture on their own (one quoted market)."""
    return {c.code for c in v.checks if c.passed is False and not c.blocking}


# --- report mode -------------------------------------------------------------------------------------------


def test_good_fixture_qualifies_with_minimum_odds() -> None:
    v = verify(good(), PARAMS)
    assert v.decision is Decision.QUALIFIED and v.failed == () and v.grade is Grade.A
    assert v.p_under05 is not None and v.p_draw is not None
    assert v.min_odds_under05 == pytest.approx(1.08 / v.p_under05)          # no market check: EV >= 8%
    assert v.min_odds_draw == pytest.approx(1.08 / v.p_draw)
    assert v.selected is None and v.params_version == PARAMS.params_version
    assert any(c.code == "TEAM_MARKET_GAP" and c.passed is None for c in v.checks)


def test_with_market_minimum_odds_use_ev_5_and_model_c_fair_floor() -> None:
    v = verify(good(model_c=model_c()), PARAMS)
    assert v.decision is Decision.QUALIFIED and v.p_under05 is not None and v.p_draw is not None
    assert v.min_odds_under05 == pytest.approx(max(1.05 / v.p_under05, 1 / 0.735))
    assert v.min_odds_draw == pytest.approx(max(1.05 / v.p_draw, 1 / 0.752))
    floor = verify(good(model_c=model_c(p00=0.69, p_draw=0.70)), PARAMS)     # market less optimistic
    assert floor.min_odds_under05 == pytest.approx(1 / 0.69)                  # cannot bet below market fair


# --- layer 0 / 1 -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(("change", "code"), [
    ({"baseline": baseline(p00_l=0.65)}, "LEAGUE_P00"),
    ({"fifteen_minute_market": False}, "FIFTEEN_MINUTE_MARKET"),
    ({"baseline": baseline(status=BaselineStatus.REVIEW, reasons=("s = 0.190 outside [0.09, 0.16]",))},
     "BASELINE_PLAUSIBLE"),
])
def test_screen_failures_skip(change: dict[str, Any], code: str) -> None:
    v = verify(good(**change), PARAMS)
    assert v.decision is Decision.SKIP and code in failed_codes(v)


@pytest.mark.parametrize(("change", "code"), [
    ({"windows": windows(away_away=window(View.AWAY, status=StatsStatus.INSUFFICIENT_DATA))}, "WINDOW_AWAY_AWAY"),
    ({"windows": windows(home_home=window(View.HOME, n=8))}, "WINDOW_HOME_HOME"),
    ({"model_b2": None, "unavailable": ("HOME index needs 10 matches, has 8",)}, "MODEL_B2_AVAILABLE"),
    ({"baseline": baseline(status=BaselineStatus.INSUFFICIENT_DATA, lambda15=None, s=None, p00_l=None)},
     "COMPETITION_BASELINE"),
    ({"windows": None}, "TEAM_WINDOWS"),
])
def test_data_failures_are_insufficient_data(change: dict[str, Any], code: str) -> None:
    v = verify(good(**change), PARAMS)
    assert v.decision is Decision.INSUFFICIENT_DATA and code in failed_codes(v) and v.grade is Grade.SKIP


def test_reason_of_a_missing_model_is_kept() -> None:
    v = verify(good(model_b2=None, unavailable=("HOME index needs 10 matches, has 8",)), PARAMS)
    (check,) = [c for c in v.checks if c.code == "MODEL_B2_AVAILABLE"]
    assert "needs 10 matches" in check.detail and "FAIL" in check.line()


def test_first_failing_layer_decides() -> None:
    v = verify(good(fifteen_minute_market=False, model_b2=None), PARAMS)
    assert v.decision is Decision.SKIP and {"FIFTEEN_MINUTE_MARKET", "MODEL_B2_AVAILABLE"} <= failed_codes(v)


# --- layer 2 / grade -------------------------------------------------------------------------------------


def test_model_disagreement_skips() -> None:
    v = verify(good(model_b2=model_b2(p00=0.85)), PARAMS)                   # 73.2% vs 85.0%: 11.8 pts
    assert v.decision is Decision.SKIP and "MODEL_GAP_A_B" in failed_codes(v)


def test_team_market_disagreement_skips() -> None:
    v = verify(good(model_c=model_c(p00=0.60, p_draw=0.62)), PARAMS)
    assert v.decision is Decision.SKIP and "TEAM_MARKET_GAP" in failed_codes(v)


def test_system_doc_section_8_grade() -> None:
    """SYSTEM-DOCUMENTATION 8: P_A 81.5%, P_B 74.0% -> model gap 7.5 pts -> grade B."""
    spread = model_spread_pts([0.815, 0.740])
    assert round(spread, 1) == 7.5 and grade(spread, True, PARAMS) is Grade.B
    assert grade(4.0, True, PARAMS) is Grade.A and grade(4.0, False, PARAMS) is Grade.B
    assert grade(10.5, True, PARAMS) is Grade.SKIP


def test_partial_samples_downgrade_a_to_b() -> None:
    v = verify(good(windows=windows(home_home=window(View.HOME, exact=9))), PARAMS)
    assert v.decision is Decision.QUALIFIED and v.grade is Grade.B and v.complete_sample == 9


def test_grade_spread_includes_model_c() -> None:
    v = verify(good(model_b2=model_b2(p00=0.725), model_c=model_c(p00=0.795, p_draw=0.81)), PARAMS)
    assert v.model_spread_pts is not None and v.model_spread_pts == pytest.approx((0.795 - 0.725) * 100)
    assert v.grade is Grade.B


# --- quote mode -------------------------------------------------------------------------------------------


def quotes(under: float, over: float, draw: float | None = None) -> dict[Bet, dict[str, float]]:
    q: dict[Bet, dict[str, float]] = {Bet.UNDER05: {"NO": under, "YES": over}}
    if draw is not None:
        q[Bet.DRAW] = {"HOME": 6.0, "DRAW": draw, "AWAY": 7.5}
    return q


def test_quote_at_or_above_minimum_qualifies_and_selects_higher_ev() -> None:
    v = verify(good(quotes=quotes(1.50, 2.70, draw=1.36), bookmaker="Book"), PARAMS)
    assert v.decision is Decision.QUALIFIED and v.selected is Bet.UNDER05
    assert missed_codes(v) == {"QUOTE_DRAW_PRICE"}                               # draw below minimum: not selectable
    assert v.ev is not None and v.p_under05 is not None and v.ev == pytest.approx(1.50 * v.p_under05 - 1)
    draw_quote = {Bet.UNDER05: {"NO": 1.50, "YES": 2.70}, Bet.DRAW: {"HOME": 5.0, "DRAW": 1.49, "AWAY": 6.0}}
    both = verify(good(quotes=draw_quote), PARAMS)                              # draw EV +11.6% > under +9.7%
    assert both.decision is Decision.QUALIFIED and both.selected is Bet.DRAW and missed_codes(both) == set()


def test_quote_below_minimum_skips() -> None:
    v = verify(good(quotes=quotes(1.30, 3.20)), PARAMS)                          # pricing doc: EV -4.9%
    assert v.decision is Decision.SKIP and failed_codes(v) == {"QUOTED_MARKET_QUALIFIES"}
    assert missed_codes(v) == {"QUOTE_UNDER05_PRICE"}
    assert v.selected is None and v.ev is None


def test_quote_far_from_bookmaker_view_skips() -> None:
    v = verify(good(quotes=quotes(1.90, 1.90)), PARAMS)                          # bookmaker sees 50/50
    assert v.decision is Decision.SKIP and "QUOTE_UNDER05_BOOKMAKER_GAP" in missed_codes(v)


def test_malformed_quote_is_a_failed_check_not_an_error() -> None:
    v = verify(good(quotes={Bet.UNDER05: {"NO": 1.10, "YES": 2.0}}), PARAMS)    # 41% margin
    assert v.decision is Decision.SKIP and "QUOTE_UNDER05" in missed_codes(v)
    assert "QUOTED_MARKET_QUALIFIES" in failed_codes(v)


# --- red flags --------------------------------------------------------------------------------------------


def h2h(meetings: int, early: int, zero: int | None = None, status: StatsStatus = StatsStatus.OK) -> H2HStats:
    zz = meetings - early if zero is None else zero
    ok = status is StatsStatus.OK
    return H2HStats(H2HScope.ALL_VENUES, status, () if ok else ("1 INCOMPLETE meetings",), meetings, 1, 1, 1,
                    2.5, 1.1, KO.date() - timedelta(days=200),
                    Rate(zz, meetings) if ok else None, Rate(early, meetings) if ok else None)


@pytest.mark.parametrize(("change", "code"), [
    ({"favourite_odds": 1.40}, "FAVOURITE_ODDS"),
    ({"windows": windows(home_home=window(View.HOME, wins=8), away_away=window(View.AWAY, wins=1))},
     "FAVOURITE_PROXY"),
    ({"windows": windows(home_general=window(View.GENERAL, recent=3))}, "RECENT_EARLY_GOALS_HOME"),
    ({"windows": windows(away_general=window(View.GENERAL, recent=4))}, "RECENT_EARLY_GOALS_AWAY"),
    ({"h2h": h2h(meetings=6, early=3)}, "H2H_EARLY_GOALS"),
    ({"context_flags": ("MUST_SCORE",)}, "CONTEXT_MUST_SCORE"),
])
def test_red_flags_skip(change: dict[str, Any], code: str) -> None:
    v = verify(good(**change), PARAMS)
    assert v.decision is Decision.SKIP and code in failed_codes(v)


def test_red_flag_passes_and_information_only_cases() -> None:
    v = verify(good(favourite_odds=1.80, h2h=h2h(meetings=5, early=1, zero=4)), PARAMS)
    assert v.decision is Decision.QUALIFIED
    codes = {c.code: c.passed for c in v.checks}
    assert codes["FAVOURITE_ODDS"] is True and "FAVOURITE_PROXY" not in codes    # odds take precedence
    assert codes["H2H_EARLY_GOALS"] is True and codes["H2H_SUPPORT"] is None    # 4/5 0-0: note only
    few = verify(good(h2h=h2h(meetings=3, early=3)), PARAMS)                     # too few meetings: context
    assert few.decision is Decision.QUALIFIED and any(c.code == "H2H" and c.passed is None for c in few.checks)
    inc = verify(good(h2h=h2h(meetings=6, early=0, status=StatsStatus.INSUFFICIENT_DATA)), PARAMS)
    assert inc.decision is Decision.QUALIFIED


# --- fail closed -------------------------------------------------------------------------------------------


def test_unexpected_exception_is_error_never_a_bet(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: object) -> list[object]:
        raise ZeroDivisionError("division by zero")
    monkeypatch.setattr(layers, "_red_flags", boom)
    v = verify(good(), PARAMS)
    assert v.decision is Decision.ERROR and v.grade is Grade.SKIP and v.selected is None
    assert v.error is not None and "ZeroDivisionError" in v.error


# --- ranking, cap, re-check ---------------------------------------------------------------------------------


def q(fid: str, g: Grade = Grade.B, p: float = 0.75, ev: float | None = None, spread: float = 5.0,
      sample: int = 10, decision: Decision = Decision.QUALIFIED) -> Verdict:
    return Verdict(fixture_id=fid, kickoff_utc=KO, decision=decision, grade=g, checks=(), params_version="1.5",
                   p_under05=p, model_spread_pts=spread, complete_sample=sample, ev=ev)


def test_ranking_order() -> None:
    vs = [q("b-low", p=0.74), q("a", g=Grade.A, p=0.70), q("b-high", p=0.80), q("b-ev", ev=0.03, p=0.71),
          q("b-high-wide", p=0.80, spread=9.0), q("b-high-small", p=0.80, sample=9), q("skip", decision=Decision.SKIP)]
    r = rank_and_cap(vs, PARAMS)
    assert [v.fixture_id for v in r.listed] == ["a", "b-ev", "b-high", "b-high-small", "b-high-wide", "b-low"]
    assert [v.fixture_id for v in r.not_qualified] == ["skip"] and r.over_cap == ()


def test_cap_is_a_maximum_never_a_target() -> None:
    small = rank_and_cap([q(f"f{i}") for i in range(7)], PARAMS)
    assert len(small.listed) == 7 and small.over_cap == ()                       # never filled up
    capped = rank_and_cap([q(f"f{i}", p=0.70 + i / 100) for i in range(5)],
                          params(sessions__max_matches_per_session=2))
    assert [v.fixture_id for v in capped.listed] == ["f4", "f3"]
    assert {v.decision for v in capped.over_cap} == {Decision.QUALIFIED_OVER_CAP} and len(capped.over_cap) == 3


def test_ranking_rejects_duplicates_and_incomplete_verdicts() -> None:
    with pytest.raises(ValueError, match="twice"):
        rank_and_cap([q("x"), q("x")], PARAMS)
    with pytest.raises(ValueError, match="without grade"):
        rank_and_cap([replace(q("x"), grade=Grade.SKIP)], PARAMS)


@pytest.mark.parametrize(("previous", "new", "expected"), [
    (Decision.QUALIFIED, Decision.QUALIFIED, Decision.QUALIFIED),
    (Decision.QUALIFIED, Decision.SKIP, Decision.WITHDRAWN),
    (Decision.QUALIFIED, Decision.ERROR, Decision.WITHDRAWN),
    (Decision.QUALIFIED_OVER_CAP, Decision.QUALIFIED, Decision.QUALIFIED_OVER_CAP),
    (Decision.QUALIFIED_OVER_CAP, Decision.SKIP, Decision.SKIP),
    (Decision.SKIP, Decision.QUALIFIED, Decision.SKIP),                          # never added after publication
    (Decision.INSUFFICIENT_DATA, Decision.QUALIFIED, Decision.INSUFFICIENT_DATA),
    (Decision.WITHDRAWN, Decision.QUALIFIED, Decision.WITHDRAWN),
])
def test_recheck_is_one_way(previous: Decision, new: Decision, expected: Decision) -> None:
    assert recheck(previous, q("x", decision=new)) is expected


def test_model_spread_needs_two_models() -> None:
    from app.calculation.invariants import InvariantError
    with pytest.raises(InvariantError):
        model_spread_pts([0.73])


# --- params 1.6: probabilities only (owner decision 2026-11-23) ------------------------------------------------


def live(**kw: Any) -> FixtureAnalysis:
    return good(price_params=LIVE, **kw)


def test_v16_passes_on_probability_and_never_uses_odds() -> None:
    a = live(model_c=model_c(), favourite_odds=1.20,
             quotes={Bet.UNDER05: {"UNDER": 1.01, "OVER": 9.0}}, bookmaker="X")  # odds present: all ignored
    v = verify(a, LIVE)
    assert v.decision is Decision.QUALIFIED and v.params_version == "1.6"
    assert v.min_odds_under05 is None and v.min_odds_draw is None and v.ev is None
    assert v.p_under05 is not None and v.p_draw is not None and 0 < v.p_under05 <= v.p_draw < 1
    codes = {c.code for c in v.checks}
    assert {"P_UNDER05", "P_DRAW", "SELECTED_MARKET"} <= codes
    assert not codes & {"MINIMUM_ODDS", "TEAM_MARKET_GAP", "FAVOURITE_ODDS", "QUOTE_UNDER05_PRICE"}
    assert v.selected in (Bet.UNDER05, Bet.DRAW)
    # Model C does not move the probabilities: the same with and without it
    assert verify(live(), LIVE).p_under05 == pytest.approx(v.p_under05)


def test_v16_fails_with_the_reason_when_both_probabilities_are_below_their_minimums() -> None:
    strict = LIVE.with_values({"probability.min_under05": 0.99, "probability.min_draw": 0.99})
    v = verify(live(), strict)
    assert v.decision is Decision.SKIP and "PROBABILITY_QUALIFIES" in failed_codes(v)
    assert {"P_UNDER05", "P_DRAW"} <= missed_codes(v)


def test_v16_selects_the_market_furthest_above_its_minimum() -> None:
    v = verify(live(), LIVE)
    assert v.p_under05 is not None and v.p_draw is not None and LIVE.probability is not None
    margin_u, margin_d = v.p_under05 - LIVE.probability.min_under05, v.p_draw - LIVE.probability.min_draw
    expected = Bet.UNDER05 if margin_u >= margin_d else Bet.DRAW
    assert v.selected is expected
    only_draw = LIVE.with_values({"probability.min_under05": 0.99, "probability.min_draw": 0.50})
    assert verify(live(), only_draw).selected is Bet.DRAW


def test_v16_data_and_screen_layers_still_decide() -> None:
    v = verify(live(fifteen_minute_market=False), LIVE)
    assert v.decision is Decision.SKIP and "FIFTEEN_MINUTE_MARKET" in failed_codes(v)
    v = verify(live(windows=None), LIVE)
    assert v.decision is Decision.INSUFFICIENT_DATA
