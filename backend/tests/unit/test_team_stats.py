"""Team window and H2H statistics: perspective, counts, selection-bias rule, point-in-time checks."""

from datetime import UTC, datetime, timedelta

import pytest

from app.calculation.invariants import InvariantError
from app.calculation.team_stats import (
    H2HScope,
    Rate,
    StatsStatus,
    TeamMatch,
    View,
    h2h_stats,
    window_stats,
)
from app.domain.enums import DataStatus as DS
from app.domain.models import Score

AS_OF = datetime(2026, 3, 1, 15, 0, tzinfo=UTC)


def tm(days_ago: int, is_home: bool, ft: tuple[int, int], ht: tuple[int, int] | None = (0, 0),
       h15: tuple[int, int] | None = (0, 0), status: DS = DS.COMPLETE, early: bool | None = None) -> TeamMatch:
    goal = early if early is not None else (None if h15 is None else sum(h15) > 0)
    return TeamMatch(AS_OF - timedelta(days=days_ago), is_home, status, Score(home=ft[0], away=ft[1]),
                     Score(home=ht[0], away=ht[1]) if ht else None,
                     Score(home=h15[0], away=h15[1]) if h15 else None, goal)


def ten_home() -> list[TeamMatch]:
    """10 home matches, newest first. 3 with an early goal (2 scored by us, 1 conceded)."""
    return [
        tm(7, True, (2, 1), (1, 0), (1, 0)),     # win, early goal scored
        tm(14, True, (1, 1), (0, 1), (0, 1)),    # draw, early goal conceded
        tm(21, True, (3, 0), (2, 0), (1, 0)),    # win, early goal scored
        tm(28, True, (0, 0)),
        tm(35, True, (1, 0), (0, 0)),
        tm(42, True, (0, 2), (0, 1)),            # loss, first-half goal after 15'
        tm(49, True, (1, 1), (1, 1), (0, 0)),
        tm(56, True, (2, 0), (0, 0), (0, 0), DS.DERIVED),
        tm(63, True, (0, 1), (0, 0), (0, 0), DS.DERIVED),
        tm(70, True, (1, 0), (0, 0)),
    ]


def test_home_window_counts_from_the_teams_side() -> None:
    w = window_stats(ten_home(), View.HOME, AS_OF)
    assert w.status is StatsStatus.OK and w.sample_size == 10 and w.exact_sample_size == 10
    assert (w.wins, w.draws, w.losses) == (5, 3, 2) and w.win_rate == 0.5
    assert (w.goals_scored, w.goals_conceded) == (11, 6)
    assert (w.ht_goals_scored, w.ht_goals_conceded) == (4, 3)
    assert w.zero_zero == Rate(7, 10) and w.zero_zero.value == 0.7          # shown as 70% (7/10)
    assert w.early_goal == Rate(3, 10) and w.level == Rate(7, 10)
    assert (w.early_scored, w.early_conceded, w.early_goals_total) == (2, 1, 3)
    assert w.exact_ft_goals_total == 17
    assert w.recent_early_goal_matches == Rate(3, 5)                        # red flag: 3+ of last 5
    assert w.avg_scored == pytest.approx(1.1) and w.avg_conceded == pytest.approx(0.6)


def test_away_perspective_swaps_sides() -> None:
    w = window_stats([tm(5, False, (0, 2), (0, 1), (0, 1))], View.AWAY, AS_OF, window=1)
    assert (w.wins, w.goals_scored, w.goals_conceded, w.early_scored, w.early_conceded) == (1, 2, 0, 1, 0)


def test_incomplete_match_makes_window_insufficient_and_is_not_dropped() -> None:
    ms = ten_home()
    ms[4] = tm(35, True, (1, 0), (1, 0), None, DS.INCOMPLETE)
    w = window_stats(ms, View.HOME, AS_OF)
    assert w.status is StatsStatus.INSUFFICIENT_DATA and "INCOMPLETE" in w.reasons[0]
    assert w.sample_size == 10                                              # still counted, not replaced
    assert w.zero_zero is None and w.early_goal is None and w.early_scored is None
    assert w.goals_scored == 11                                             # FT values remain available


def test_window_uses_only_the_newest_matches() -> None:
    ms = [*ten_home(), tm(80, True, (5, 5), (5, 5), (3, 3))]               # 11th match: ignored
    w = window_stats(ms, View.HOME, AS_OF)
    assert w.sample_size == 10 and w.goals_scored == 11
    assert w.last_kickoff == AS_OF - timedelta(days=7) and w.first_kickoff == AS_OF - timedelta(days=70)


def test_short_window_is_insufficient() -> None:
    w = window_stats(ten_home()[:7], View.HOME, AS_OF)
    assert w.status is StatsStatus.INSUFFICIENT_DATA and "only 7 matches" in w.reasons[0]


def test_partial_draw_unknown() -> None:
    ms = ten_home()
    ms[3] = tm(28, True, (2, 1), (1, 1), None, DS.PARTIAL_DRAW_UNKNOWN, early=True)
    w = window_stats(ms, View.HOME, AS_OF)
    assert w.status is StatsStatus.OK
    assert w.zero_zero == Rate(6, 10)                                       # 0-0 known for PARTIAL
    assert w.level is not None and w.level.n == 9                           # draw rate excludes PARTIAL
    assert w.early_scored is None and w.early_conceded is None             # which side scored is unknown


def test_missing_ht_score_only_blanks_ht_totals() -> None:
    ms = ten_home()
    ms[3] = tm(28, True, (0, 0), None, (0, 0))
    w = window_stats(ms, View.HOME, AS_OF)
    assert w.status is StatsStatus.OK and w.ht_goals_scored is None


def test_point_in_time_and_order_are_enforced() -> None:
    with pytest.raises(InvariantError, match="point-in-time"):
        window_stats([tm(0, True, (1, 0))], View.HOME, AS_OF)              # kick-off == as_of
    with pytest.raises(InvariantError, match="newest first"):
        window_stats(list(reversed(ten_home())), View.HOME, AS_OF)
    with pytest.raises(InvariantError):
        window_stats(ten_home(), View.AWAY, AS_OF)                           # home matches in AWAY view
    with pytest.raises(InvariantError):
        window_stats([tm(3, False, (1, 0))], View.HOME, AS_OF)
    with pytest.raises(InvariantError):
        window_stats(ten_home(), View.HOME, AS_OF.replace(tzinfo=None))


def test_general_view_mixes_venues() -> None:
    ms = [tm(3, False, (0, 1), (0, 1), (0, 1)), *ten_home()[:9]]
    w = window_stats(ms, View.GENERAL, AS_OF)
    assert w.status is StatsStatus.OK and w.wins == 5 and w.early_scored == 3


@pytest.mark.parametrize("kwargs", [
    {"status": DS.COMPLETE, "h15": None},
    {"status": DS.INCOMPLETE, "h15": (0, 0)},
    {"status": DS.PENDING, "h15": None},
    {"status": DS.COMPLETE, "h15": (0, 0), "early": True},
])
def test_inconsistent_match_rejected(kwargs: dict) -> None:  # type: ignore[type-arg]
    with pytest.raises(InvariantError):
        tm(3, True, (1, 0), (1, 0), **kwargs)


def test_naive_kickoff_rejected() -> None:
    with pytest.raises(InvariantError):
        TeamMatch(datetime(2026, 1, 1), True, DS.COMPLETE, Score(home=0, away=0), None, Score(home=0, away=0), False)


def test_h2h_all_and_same_venue() -> None:
    meetings = [
        tm(100, True, (1, 0), (0, 0)),                   # fixture home team at home: win
        tm(300, False, (2, 2), (1, 1), (1, 0)),          # away: draw, early goal
        tm(500, True, (0, 1), (0, 0)),                   # at home: loss
        tm(700, False, (0, 3), (0, 2), (0, 1)),          # away: fixture home team won 3-0 away
    ]
    a = h2h_stats(meetings, H2HScope.ALL_VENUES, AS_OF)
    assert (a.meetings, a.home_wins, a.draws, a.away_wins) == (4, 2, 1, 1)
    assert a.zero_zero == Rate(2, 4) and a.early_goal == Rate(2, 4)
    assert a.avg_ft_goals == pytest.approx(9 / 4) and a.avg_ht_goals == pytest.approx(4 / 4)
    assert a.last_meeting == (AS_OF - timedelta(days=100)).date()
    s = h2h_stats(meetings, H2HScope.SAME_VENUE, AS_OF)
    assert (s.meetings, s.home_wins, s.away_wins) == (2, 1, 1) and s.zero_zero == Rate(2, 2)


def test_h2h_incomplete_and_empty() -> None:
    inc = h2h_stats([tm(50, True, (1, 0), (1, 0), None, DS.INCOMPLETE)], H2HScope.ALL_VENUES, AS_OF)
    assert inc.status is StatsStatus.INSUFFICIENT_DATA and inc.zero_zero is None and inc.home_wins == 1
    empty = h2h_stats([], H2HScope.ALL_VENUES, AS_OF)
    assert empty.meetings == 0 and empty.avg_ft_goals is None and empty.zero_zero == Rate(0, 0)
    assert Rate(0, 0).value is None
