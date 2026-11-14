from datetime import UTC, datetime

import pytest

from app.calculation.recovery import derive_fifteen
from app.domain.enums import DataStatus, FixtureStatus, ReconciliationStatus, Side, SourceCode
from app.domain.models import Score, SourceGoal, SourceMatch
from app.normalisation.names import normalize_team_name
from app.normalisation.reconcile import reconcile

KO = datetime(2025, 8, 15, 19, 0, tzinfo=UTC)


def g(side: Side, label: str) -> SourceGoal:
    return SourceGoal.from_label(side, label)


def rec(source: SourceCode, ft: tuple[int, int] | None, ht: tuple[int, int] | None,
        goals: tuple[SourceGoal, ...] | None = None, status: FixtureStatus = FixtureStatus.FINISHED) -> SourceMatch:
    return SourceMatch(
        source=source, provider_match_key="k", competition_code="ENG1", season_label="2025-26",
        kickoff_date=KO.date(), kickoff_utc=KO, home_name="Alpha", away_name="Beta", status=status,
        ft=Score(home=ft[0], away=ft[1]) if ft else None, ht=Score(home=ht[0], away=ht[1]) if ht else None,
        goals=goals or (), has_goal_minutes=goals is not None,
    )


# --- names --------------------------------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("Liverpool FC", "Liverpool"), ("AFC Bournemouth", "Bournemouth"),
    ("FC Bayern München", "FC Bayern Munchen"), ("Brighton & Hove Albion FC", "Brighton and Hove Albion"),
])
def test_names_normalise_equal(a: str, b: str) -> None:
    assert normalize_team_name(a) == normalize_team_name(b)


@pytest.mark.parametrize("a,b", [("Man United", "Manchester United FC"), ("Wolves", "Wolverhampton Wanderers FC")])
def test_different_names_stay_different(a: str, b: str) -> None:
    assert normalize_team_name(a) != normalize_team_name(b)   # -> review queue, never guessed


# --- 15' derivation -------------------------------------------------------------------------

def test_complete_from_goals() -> None:
    goals = (g(Side.AWAY, "9"), g(Side.AWAY, "15"), g(Side.HOME, "16"))
    r = derive_fifteen(FixtureStatus.FINISHED, Score(home=1, away=2), Score(home=1, away=2), goals)
    assert (r.data_status, r.h15, r.goal_before_15) == (DataStatus.COMPLETE, Score(home=0, away=2), True)
    assert r.first_goal == goals[0]


def test_minute_16_is_outside_period() -> None:
    r = derive_fifteen(FixtureStatus.FINISHED, Score(home=1, away=0), Score(home=1, away=0), (g(Side.HOME, "16"),))
    assert (r.h15, r.goal_before_15) == (Score(home=0, away=0), False)


def test_ht_zero_derives_no_early_goal() -> None:
    r = derive_fifteen(FixtureStatus.FINISHED, Score(home=2, away=1), Score(home=0, away=0), None)
    assert (r.data_status, r.h15, r.goal_before_15) == (DataStatus.DERIVED, Score(home=0, away=0), False)


def test_unknown_minutes_never_assumed_zero() -> None:
    r = derive_fifteen(FixtureStatus.FINISHED, Score(home=2, away=1), Score(home=1, away=0), None)
    assert (r.data_status, r.h15, r.goal_before_15) == (DataStatus.INCOMPLETE, None, None)


def test_unplayed_is_pending_and_abandoned_incomplete() -> None:
    assert derive_fifteen(FixtureStatus.SCHEDULED, None, None, None).data_status is DataStatus.PENDING
    assert derive_fifteen(FixtureStatus.ABANDONED, None, None, None).data_status is DataStatus.INCOMPLETE


# --- reconciliation ------------------------------------------------------------------------

GOALS = (g(Side.HOME, "37"), g(Side.HOME, "49"), g(Side.AWAY, "64"), g(Side.AWAY, "76"),
         g(Side.HOME, "88"), g(Side.HOME, "90+4"))


def test_single_source() -> None:
    t = reconcile([rec(SourceCode.OPENFOOTBALL, (4, 2), (1, 0), GOALS)])
    assert t.reconciliation is ReconciliationStatus.SINGLE_SOURCE
    assert t.fifteen.data_status is DataStatus.COMPLETE and t.goals_source is SourceCode.OPENFOOTBALL


def test_agreed_sources_and_goals_from_goal_source() -> None:
    t = reconcile([rec(SourceCode.FOOTBALL_DATA_ORG, (4, 2), (1, 0)),
                   rec(SourceCode.OPENFOOTBALL, (4, 2), (1, 0), GOALS)])
    assert t.reconciliation is ReconciliationStatus.AGREED
    assert t.scores_source is SourceCode.FOOTBALL_DATA_ORG and t.goals_source is SourceCode.OPENFOOTBALL
    assert t.fifteen.h15 == Score(home=0, away=0)


def test_ft_conflict() -> None:
    t = reconcile([rec(SourceCode.FOOTBALL_DATA_ORG, (4, 2), (1, 0)), rec(SourceCode.OPENFOOTBALL, (4, 1), (1, 0))])
    assert t.reconciliation is ReconciliationStatus.CONFLICT
    assert any(i.code == "FT_CONFLICT" for i in t.issues)


def test_inconsistent_goal_list_is_not_used() -> None:
    bad = (g(Side.HOME, "37"),)   # one goal listed but FT 4-2
    t = reconcile([rec(SourceCode.OPENFOOTBALL, (4, 2), (1, 0), bad)])
    assert t.fifteen.data_status is DataStatus.INCOMPLETE and t.goals_source is None
    assert any(i.code == "GOALS_INCONSISTENT" for i in t.issues)


def test_h15_conflict_between_goal_sources() -> None:
    other = (g(Side.HOME, "12"), g(Side.HOME, "49"), g(Side.AWAY, "64"), g(Side.AWAY, "76"),
             g(Side.HOME, "88"), g(Side.HOME, "90+4"))
    t = reconcile([rec(SourceCode.API_FOOTBALL, (4, 2), (1, 0), other), rec(SourceCode.OPENFOOTBALL, (4, 2), (1, 0), GOALS)])
    assert t.reconciliation is ReconciliationStatus.CONFLICT
    assert any(i.code == "H15_CONFLICT" for i in t.issues)


def test_scheduled_match() -> None:
    t = reconcile([rec(SourceCode.FOOTBALL_DATA_ORG, None, None, status=FixtureStatus.SCHEDULED)])
    assert t.status is FixtureStatus.SCHEDULED and t.ft is None and t.fifteen.data_status is DataStatus.PENDING
