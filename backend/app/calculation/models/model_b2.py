"""Model B v2: competition-adjusted early-goal team index (DATA-SOURCES-AND-MARKET-MODEL.md 16). Pure.

    expected_i = lambda15 of the competition (and season) match i was played in
    actual_i   = goals before 15' in match i (both teams)
    index      = (sum actual + k) / (sum expected + k)            k = 3, per view
    Index_home = 0.6 x home team HOME index + 0.4 x home team GENERAL index
    Index_away = 0.6 x away team AWAY index + 0.4 x away team GENERAL index
    lambda15_B = lambda15 of the fixture's competition x (Index_home + Index_away) / 2
    P_B        = e^-lambda15_B
Pricing engine factor (FIFTEEN-MINUTE-ODDS-CALCULATION.md 6): I = index^w_I, w_I = 0.5.

Matches: COMPLETE / DERIVED count exactly; PARTIAL_DRAW_UNKNOWN counts 1 early goal (lower bound, flagged);
an INCOMPLETE match makes the view unusable (DATA-MODEL 7.1). Baseline choice: 16.6 / 16.7.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.calculation.baselines import CompetitionBaseline
from app.calculation.invariants import InvariantError, check_lambda, check_probability
from app.calculation.models import ModelUnavailableError
from app.calculation.team_stats import TeamMatch, View, check_history
from app.domain.enums import CompetitionType, DataStatus
from app.domain.params import Params


class BaselineMethod(StrEnum):
    OWN = "OWN"
    DOMESTIC_FALLBACK = "DOMESTIC_FALLBACK"
    AVERAGE_OF_DOMESTIC = "AVERAGE_OF_DOMESTIC"


def _lambda15(b: CompetitionBaseline | None) -> float | None:
    return b.lambda15 if b is not None and b.lambda15 is not None else None


def expected_for_history_match(own: CompetitionBaseline | None, team_domestic: CompetitionBaseline | None,
                               params: Params) -> tuple[float, BaselineMethod]:
    """16.7: the match's own competition-season baseline; below competition_baselines.min_matches (or
    missing) the team's domestic league baseline of that season."""
    min_matches = params.competition_baselines.min_matches
    if own is not None and own.matches >= min_matches and (lam := _lambda15(own)) is not None:
        return lam, BaselineMethod.OWN
    if (lam := _lambda15(team_domestic)) is not None:
        return lam, BaselineMethod.DOMESTIC_FALLBACK
    raise ModelUnavailableError("no usable baseline for a history match (own and domestic missing)")


def fixture_baseline(competition_type: CompetitionType, own: CompetitionBaseline | None,
                     home_domestic: CompetitionBaseline | None, away_domestic: CompetitionBaseline | None,
                     params: Params) -> tuple[float, BaselineMethod]:
    """16.6: league fixture -> that league; cup / continental -> its own baseline if it has at least
    competition_baselines.min_matches matches, otherwise the average of the two teams' domestic baselines."""
    min_matches = params.competition_baselines.min_matches
    if competition_type is CompetitionType.FRIENDLY:
        raise ModelUnavailableError("friendlies are not priced")
    if competition_type is CompetitionType.LEAGUE:
        if (lam := _lambda15(own)) is None:
            raise ModelUnavailableError("league fixture without a usable league baseline")
        return lam, BaselineMethod.OWN
    if own is not None and own.matches >= min_matches and (lam := _lambda15(own)) is not None:
        return lam, BaselineMethod.OWN
    h, a = _lambda15(home_domestic), _lambda15(away_domestic)
    if h is None or a is None:
        raise ModelUnavailableError("cup fixture: competition sample too small and a domestic baseline is missing")
    return (h + a) / 2.0, BaselineMethod.AVERAGE_OF_DOMESTIC


@dataclass(frozen=True)
class IndexMatch:
    match: TeamMatch
    expected_lambda15: float  # from expected_for_history_match
    method: BaselineMethod = BaselineMethod.OWN


@dataclass(frozen=True)
class TeamIndex:
    view: View
    matches: int
    actual: int
    expected: float
    lower_bound_matches: int  # PARTIAL_DRAW_UNKNOWN counted as 1 early goal
    index: float


def team_index(history: Sequence[IndexMatch], view: View, as_of: datetime, params: Params) -> TeamIndex:
    """Index of one view from the newest data_gate.history_matches matches (newest first, before as_of),
    shrunk with k = shrinkage.team_index_k."""
    k, window = params.shrinkage.team_index_k, params.data_gate.history_matches
    check_history([h.match for h in history], as_of)
    if view is View.HOME and not all(h.match.is_home for h in history):
        raise InvariantError("HOME view received an away match")
    if view is View.AWAY and any(h.match.is_home for h in history):
        raise InvariantError("AWAY view received a home match")
    hs = list(history[:window])
    if len(hs) < window:
        raise ModelUnavailableError(f"{view} index needs {window} matches, has {len(hs)}")
    actual = 0
    lower = 0
    for h in hs:
        check_lambda(h.expected_lambda15, "expected lambda15")
        m = h.match
        if m.data_status is DataStatus.INCOMPLETE:
            raise ModelUnavailableError(f"{view} window has INCOMPLETE matches (DATA-MODEL 7.1)")
        if m.h15 is not None:
            actual += m.h15.total
        else:  # PARTIAL_DRAW_UNKNOWN: an early goal is certain, the exact count is not
            actual += 1
            lower += 1
    expected = math.fsum(h.expected_lambda15 for h in hs)
    if expected + k == 0:
        raise InvariantError("index undefined: expected = 0 and k = 0")
    return TeamIndex(view, len(hs), actual, expected, lower, (actual + k) / (expected + k))


@dataclass(frozen=True)
class ModelB2Result:
    index_home: float  # blended home team index
    index_away: float
    factor_home: float  # I = index^w_I for the pricing engine
    factor_away: float
    fixture_lambda15: float
    baseline_method: BaselineMethod
    lambda15: float
    p00: float  # P_B
    flags: tuple[str, ...]


def model_b2(home_general: TeamIndex, home_home: TeamIndex, away_general: TeamIndex, away_away: TeamIndex,
             fixture_lambda15: float, params: Params,
             baseline_method: BaselineMethod = BaselineMethod.OWN) -> ModelB2Result:
    venue_weight, general_weight = params.blend.venue_weight, params.blend.general_weight
    w_i = params.shrinkage.early_index_trust_w_I
    views = ((home_general, View.GENERAL), (home_home, View.HOME), (away_general, View.GENERAL),
             (away_away, View.AWAY))
    for idx, view in views:
        if idx.view is not view:
            raise InvariantError(f"expected a {view} index, got {idx.view}")
    fixture_lambda15 = check_lambda(fixture_lambda15, "fixture lambda15")
    i_home = venue_weight * home_home.index + general_weight * home_general.index
    i_away = venue_weight * away_away.index + general_weight * away_general.index
    lam = fixture_lambda15 * (i_home + i_away) / 2.0
    lower = sum(i.lower_bound_matches for i, _ in views)
    flags = (f"{lower} PARTIAL matches counted as 1 early goal (lower bound)",) if lower else ()
    return ModelB2Result(i_home, i_away, i_home ** w_i, i_away ** w_i, fixture_lambda15, baseline_method,
                         lam, check_probability(math.exp(-lam), "P_B"), flags)
