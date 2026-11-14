"""Model B v2: team index, view blend, baseline choice, selection-bias rule."""

from datetime import UTC, datetime, timedelta

import pytest

from app.calculation.baselines import BaselineStatus, CompetitionBaseline
from app.calculation.invariants import InvariantError
from app.calculation.models import ModelUnavailableError
from app.calculation.models.model_b2 import (
    BaselineMethod,
    IndexMatch,
    TeamIndex,
    expected_for_history_match,
    fixture_baseline,
    model_b2,
    team_index,
)
from app.calculation.team_stats import TeamMatch, View
from app.domain.enums import CompetitionType
from app.domain.enums import DataStatus as DS
from app.domain.models import Score

AS_OF = datetime(2026, 3, 1, tzinfo=UTC)


def im(i: int, early: int, expected: float, is_home: bool = True, status: DS = DS.COMPLETE) -> IndexMatch:
    h15 = None if status in (DS.INCOMPLETE, DS.PARTIAL_DRAW_UNKNOWN) else Score(home=early, away=0)
    goal = None if status is DS.INCOMPLETE else (early > 0 if h15 else True)
    m = TeamMatch(AS_OF - timedelta(days=7 * (i + 1)), is_home, status, Score(home=max(early, 1), away=1),
                  Score(home=early, away=1 if status is DS.PARTIAL_DRAW_UNKNOWN else 0), h15, goal)
    return IndexMatch(m, expected)


def doc_window() -> list[IndexMatch]:
    """16.5: 7 league matches (lambda15 0.32) + 3 cup matches (0.40), 2 early goals in total."""
    exp = [0.32] * 7 + [0.40] * 3
    early = [1, 0, 0, 0, 0, 0, 0, 1, 0, 0]
    return [im(i, early[i], exp[i], is_home=i % 2 == 0) for i in range(10)]


def test_doc_16_5_index() -> None:
    idx = team_index(doc_window(), View.GENERAL, AS_OF)
    assert idx.actual == 2 and idx.expected == pytest.approx(3.44)                 # doc: 3.44
    assert round(idx.index, 2) == 0.78                                             # doc: (2 + 3) / (3.44 + 3)
    assert round(idx.index * 0.32, 2) == 0.25                                      # doc: lambda15 contribution
    assert round(idx.index ** 0.5, 2) == 0.88                                      # pricing doc 6: factor 0.88


def idx(view: View, value: float) -> TeamIndex:
    return TeamIndex(view, 10, 0, 0.0, 0, value)


def test_view_blend_and_fixture_lambda() -> None:
    r = model_b2(idx(View.GENERAL, 0.9), idx(View.HOME, 0.7), idx(View.GENERAL, 1.1), idx(View.AWAY, 1.2), 0.32)
    assert r.index_home == pytest.approx(0.6 * 0.7 + 0.4 * 0.9)
    assert r.index_away == pytest.approx(0.6 * 1.2 + 0.4 * 1.1)
    assert r.lambda15 == pytest.approx(0.32 * (r.index_home + r.index_away) / 2)
    assert r.factor_home == pytest.approx(r.index_home ** 0.5)
    assert r.p00 == pytest.approx(2.718281828459045 ** -r.lambda15)


def test_normal_teams_give_the_competition_value() -> None:
    one = [idx(View.GENERAL, 1.0), idx(View.HOME, 1.0), idx(View.GENERAL, 1.0), idx(View.AWAY, 1.0)]
    r = model_b2(*one, fixture_lambda15=0.32)
    assert r.lambda15 == pytest.approx(0.32) and r.factor_home == 1.0


def test_shrinkage_pulls_to_one() -> None:
    zero = [im(i, 0, 0.32) for i in range(10)]
    assert team_index(zero, View.HOME, AS_OF).index == pytest.approx(3 / (3.2 + 3))   # not 0
    assert team_index(zero, View.HOME, AS_OF, k=0).index == 0.0


def test_incomplete_blocks_and_partial_is_lower_bound() -> None:
    w = doc_window()
    w[3] = im(3, 0, 0.32, is_home=False, status=DS.INCOMPLETE)
    with pytest.raises(ModelUnavailableError, match="INCOMPLETE"):
        team_index(w, View.GENERAL, AS_OF)
    w[3] = im(3, 1, 0.32, is_home=False, status=DS.PARTIAL_DRAW_UNKNOWN)
    t = team_index(w, View.GENERAL, AS_OF)
    assert t.actual == 3 and t.lower_bound_matches == 1
    r = model_b2(t, idx(View.HOME, 1.0), idx(View.GENERAL, 1.0), idx(View.AWAY, 1.0), 0.32)
    assert r.flags and "lower bound" in r.flags[0]


def test_window_rules() -> None:
    with pytest.raises(ModelUnavailableError, match="needs 10"):
        team_index(doc_window()[:8], View.GENERAL, AS_OF)
    with pytest.raises(InvariantError):
        team_index(doc_window(), View.HOME, AS_OF)                                    # contains away matches
    with pytest.raises(InvariantError):
        team_index(list(reversed(doc_window())), View.GENERAL, AS_OF)
    with pytest.raises(InvariantError):
        model_b2(idx(View.HOME, 1), idx(View.HOME, 1), idx(View.GENERAL, 1), idx(View.AWAY, 1), 0.32)


def b(lam: float | None, matches: int = 380) -> CompetitionBaseline:
    status = BaselineStatus.OK if lam is not None else BaselineStatus.INSUFFICIENT_DATA
    return CompetitionBaseline(status, (), matches, (), 1.4, 1.1, 0.6, 0.5, 0.44, lambda15=lam)


def test_history_match_baseline_choice() -> None:
    assert expected_for_history_match(b(0.40, 60), b(0.32)) == (0.40, BaselineMethod.OWN)
    assert expected_for_history_match(b(0.40, 30), b(0.32)) == (0.32, BaselineMethod.DOMESTIC_FALLBACK)
    assert expected_for_history_match(None, b(0.32)) == (0.32, BaselineMethod.DOMESTIC_FALLBACK)
    with pytest.raises(ModelUnavailableError):
        expected_for_history_match(b(None), b(None))


def test_fixture_baseline_choice() -> None:
    assert fixture_baseline(CompetitionType.LEAGUE, b(0.32), None, None) == (0.32, BaselineMethod.OWN)
    assert fixture_baseline(CompetitionType.CUP, b(0.40, 80), b(0.30), b(0.34)) == (0.40, BaselineMethod.OWN)
    lam, method = fixture_baseline(CompetitionType.CONTINENTAL, b(0.40, 20), b(0.30), b(0.34))
    assert lam == pytest.approx(0.32) and method is BaselineMethod.AVERAGE_OF_DOMESTIC
    with pytest.raises(ModelUnavailableError):
        fixture_baseline(CompetitionType.LEAGUE, b(None), b(0.3), b(0.3))
    with pytest.raises(ModelUnavailableError):
        fixture_baseline(CompetitionType.CUP, None, b(0.3), None)
    with pytest.raises(ModelUnavailableError):
        fixture_baseline(CompetitionType.FRIENDLY, b(0.3), b(0.3), b(0.3))
