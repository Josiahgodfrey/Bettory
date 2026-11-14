"""Competition baselines: tallies, selection-bias rule, shrinkage, pooling, review flags."""

from typing import Any

import pytest

from app.calculation.baselines import (
    BaselineMatch,
    BaselineStatus,
    GlobalShares,
    Tally,
    competition_baseline,
    global_shares,
    pool_seasons,
    shrink,
    tally,
)
from app.calculation.invariants import InvariantError
from app.domain.enums import DataStatus as DS
from app.domain.models import Score

SHARES = GlobalShares(s=0.125, r=0.28, goals=10_000, competitions=5)


def m(ft: tuple[int, int], ht: tuple[int, int] | None, h15: tuple[int, int] | None,
      status: DS = DS.COMPLETE, season: str = "2025-26", early: bool | None = None) -> BaselineMatch:
    goal = early if early is not None else (None if h15 is None else sum(h15) > 0)
    return BaselineMatch(season, status, Score(home=ft[0], away=ft[1]),
                         Score(home=ht[0], away=ht[1]) if ht else None,
                         Score(home=h15[0], away=h15[1]) if h15 else None, goal)


def league(n: int = 60, season: str = "2025-26") -> list[BaselineMatch]:
    """n matches: 2-1 (HT 1-0, goal before 15'), 1-1 (HT 0-0, DERIVED), 0-0 - repeating."""
    pattern = [m((2, 1), (1, 0), (1, 0), season=season), m((1, 1), (0, 0), (0, 0), DS.DERIVED, season),
               m((0, 0), (0, 0), (0, 0), season=season)]
    return [pattern[i % 3] for i in range(n)]


def test_tally_counts() -> None:
    t = tally(league(3))
    assert (t.matches, t.ft_home, t.ft_away, t.ht_home, t.early_goals, t.exact_goals) == (3, 3, 2, 1, 1, 5)
    assert (t.zero_known, t.zero_zero, t.incomplete) == (3, 2, 0)


def test_tallies_add_up() -> None:
    a, b = league(30), league(31)
    assert tally(a) + tally(b) == tally(a + b)


def test_complete_league_baseline() -> None:
    b = competition_baseline(tally(league(60)), SHARES, ("2025-26",))
    assert b.status is BaselineStatus.REVIEW                         # s_raw = 1/5 = 0.20 > 0.16: flagged
    assert b.s_raw == pytest.approx(0.2) and b.r_raw == pytest.approx(1.0)
    assert b.s == pytest.approx((20 + 300 * 0.125) / (100 + 300))     # shrunk toward the global share
    assert b.avg_goals == pytest.approx(5 / 3) and b.zero_zero_rate == pytest.approx(2 / 3)
    assert b.s is not None and b.r is not None
    assert b.lambda15 == pytest.approx(b.s * 5 / 3)
    assert b.lambda15_l == pytest.approx(b.r * 1 / 3)


def test_incomplete_match_makes_15_minute_values_insufficient_but_keeps_averages() -> None:
    matches = [*league(60), m((1, 0), (1, 0), None, DS.INCOMPLETE)]
    b = competition_baseline(tally(matches), SHARES)
    assert b.status is BaselineStatus.INSUFFICIENT_DATA
    assert any("INCOMPLETE" in r for r in b.reasons)
    assert b.s is None and b.r is None and b.lambda15 is None and b.zero_zero_rate is None
    assert b.avg_home_goals == pytest.approx(61 / 61) and b.avg_ht_home_goals == pytest.approx(21 / 61)


def test_partial_draw_unknown_blocks_shares() -> None:
    matches = [*league(60), m((2, 1), (1, 1), None, DS.PARTIAL_DRAW_UNKNOWN, early=True)]
    b = competition_baseline(tally(matches), SHARES)
    assert b.status is BaselineStatus.INSUFFICIENT_DATA and b.counts["partial"] == 1


def test_too_few_matches() -> None:
    b = competition_baseline(tally(league(49)), SHARES)
    assert b.status is BaselineStatus.INSUFFICIENT_DATA and "49 matches" in b.reasons[0]
    empty = competition_baseline(Tally(), SHARES)
    assert empty.status is BaselineStatus.INSUFFICIENT_DATA and empty.matches == 0


def test_missing_ht_scores_block_r() -> None:
    matches = [*league(60), m((0, 0), None, (0, 0))]
    b = competition_baseline(tally(matches), SHARES)
    assert b.status is BaselineStatus.INSUFFICIENT_DATA and b.avg_ht_home_goals is None


def test_plausible_share_is_ok() -> None:
    # 8 goals per 3 matches, 1 of them before 15': s_raw = 1/8 = 0.125
    pattern = [m((3, 2), (1, 1), (1, 0)), m((1, 1), (0, 0), (0, 0)), m((1, 0), (0, 0), (0, 0))]
    b = competition_baseline(tally(pattern * 20), SHARES)
    assert b.status is BaselineStatus.OK and b.reasons == () and b.s_raw == pytest.approx(0.125)


def test_pool_seasons_takes_the_newest() -> None:
    by_season = {s: tally(league(10, s)) for s in ("2022-23", "2025-26", "2023-24", "2024-25")}
    pooled, labels = pool_seasons(by_season)
    assert labels == ("2025-26", "2024-25", "2023-24") and pooled.matches == 30
    with pytest.raises(InvariantError):
        pool_seasons(by_season, 0)


def test_global_shares_use_only_complete_competitions() -> None:
    good = tally(league(60))
    bad = tally([*league(60), m((5, 5), (5, 5), None, DS.INCOMPLETE)])
    g = global_shares([good, bad])
    assert g.competitions == 1 and g.s == pytest.approx(0.2)
    with pytest.raises(InvariantError):
        global_shares([bad])


def test_shrink_validation() -> None:
    assert shrink(0, 0, 0.125, 300) == 0.125                         # no data: the prior
    for args in ((5, 4, 0.1, 3), (-1, 4, 0.1, 3), (0, 0, 0.1, 0)):
        with pytest.raises(InvariantError):
            shrink(*args)


@pytest.mark.parametrize("bad", [
    {"status": DS.COMPLETE, "h15": None},                            # COMPLETE needs the 15' score
    {"status": DS.INCOMPLETE, "h15": (0, 0)},                        # INCOMPLETE must not have one
    {"status": DS.PENDING, "h15": None},                             # not finished
    {"status": DS.COMPLETE, "h15": (0, 0), "early": True},           # flag disagrees with the score
])
def test_inconsistent_match_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(InvariantError):
        m((1, 0), (1, 0), bad["h15"], bad["status"], early=bad.get("early"))
