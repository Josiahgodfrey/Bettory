from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.domain.enums import FixtureStatus, Side, SourceCode
from app.domain.models import Score, SourceGoal, SourceMatch

KICKOFF = datetime(2025, 8, 15, 19, 0, tzinfo=UTC)


def match(**overrides: object) -> SourceMatch:
    base: dict[str, object] = {
        "source": SourceCode.OPENFOOTBALL,
        "provider_match_key": "2025-08-15|Liverpool|Bournemouth",
        "competition_code": "ENG1",
        "season_label": "2025-26",
        "kickoff_date": KICKOFF.date(),
        "kickoff_utc": KICKOFF,
        "home_name": "Liverpool",
        "away_name": "Bournemouth",
        "status": FixtureStatus.FINISHED,
        "ft": Score(home=4, away=2),
        "ht": Score(home=1, away=0),
        "has_goal_minutes": True,
        "goals": (
            SourceGoal.from_label(Side.HOME, "37"),
            SourceGoal.from_label(Side.HOME, "49"),
            SourceGoal.from_label(Side.AWAY, "64"),
            SourceGoal.from_label(Side.AWAY, "76"),
            SourceGoal.from_label(Side.HOME, "88"),
            SourceGoal.from_label(Side.HOME, "90+4"),
        ),
    }
    base.update(overrides)
    return SourceMatch(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "label,minute,extra", [("37", 37, None), ("90+4", 90, 4), ("45+2", 45, 2), (" 15 ", 15, None)]
)
def test_minute_labels(label: str, minute: int, extra: int | None) -> None:
    goal = SourceGoal.from_label(Side.HOME, label)
    assert (goal.minute, goal.extra_minute) == (minute, extra)


@pytest.mark.parametrize("label", ["", "abc", "90+", "+4", "0", "131"])
def test_invalid_minute_labels_rejected(label: str) -> None:
    with pytest.raises((ValueError, ValidationError)):
        SourceGoal.from_label(Side.HOME, label)


def test_stoppage_time_in_first_half_counts_as_first_half() -> None:
    assert SourceGoal.from_label(Side.HOME, "45+2").is_first_half
    assert not SourceGoal.from_label(Side.HOME, "46").is_first_half


def test_consistent_goal_list() -> None:
    assert match().goals_consistent()


def test_goal_list_inconsistent_with_ft() -> None:
    assert not match(ft=Score(home=5, away=2)).goals_consistent()


def test_goal_list_inconsistent_with_ht() -> None:
    assert not match(ht=Score(home=0, away=0)).goals_consistent()


def test_kickoff_date_must_match_utc_date() -> None:
    with pytest.raises(ValidationError):
        match(kickoff_date=KICKOFF.date().replace(day=16))


def test_naive_kickoff_rejected() -> None:
    with pytest.raises(ValidationError):
        match(kickoff_utc=datetime(2025, 8, 15, 19, 0))


def test_finished_without_score_rejected() -> None:
    with pytest.raises(ValidationError):
        match(ft=None, ht=None, goals=())


def test_scheduled_with_score_rejected() -> None:
    with pytest.raises(ValidationError):
        match(status=FixtureStatus.SCHEDULED)


def test_ht_above_ft_rejected() -> None:
    with pytest.raises(ValidationError):
        match(ht=Score(home=5, away=0), goals=(), has_goal_minutes=False)


def test_same_team_rejected() -> None:
    with pytest.raises(ValidationError):
        match(away_name="Liverpool")


def test_models_are_immutable() -> None:
    m = match()
    with pytest.raises(ValidationError):
        m.home_name = "Other"  # type: ignore[misc]


def test_unknown_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        match(unexpected="x")
