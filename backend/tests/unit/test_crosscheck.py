"""Cross-source agreement check: FT, HT, 15' between goal-minute sources, and 15' against other sources' HT."""

from datetime import UTC, date, datetime

from app.domain.enums import FixtureStatus, Side, SourceCode
from app.domain.models import Score, SourceGoal, SourceMatch
from app.domain.quality_config import CrossCheckRules, load_quality_config
from app.normalisation.crosscheck import CheckStatus, CrossCheckReport, cross_check, evaluate
from tests.support import PARAMS

CUT = PARAMS.settlement.cutoff_label_minute
RULES = CrossCheckRules(min_agreement=0.98, min_compared=1, max_disagreements_stored=10)


def status(r: CrossCheckReport) -> CheckStatus:
    return evaluate(r, RULES).status


def rec(source: SourceCode, ft: tuple[int, int], ht: tuple[int, int] | None,
        goals: tuple[tuple[Side, str], ...] | None = None) -> SourceMatch:
    return SourceMatch(
        source=source, provider_match_key="k", competition_code="ENG1", season_label="2025-26",
        kickoff_date=date(2025, 9, 1), kickoff_utc=datetime(2025, 9, 1, 15, tzinfo=UTC),
        home_name="Alpha", away_name="Beta", status=FixtureStatus.FINISHED,
        ft=Score(home=ft[0], away=ft[1]), ht=Score(home=ht[0], away=ht[1]) if ht else None,
        goals=tuple(SourceGoal.from_label(s, m) for s, m in goals or ()), has_goal_minutes=goals is not None)


H, A = Side.HOME, Side.AWAY
OF, AF, FD = SourceCode.OPENFOOTBALL, SourceCode.API_FOOTBALL, SourceCode.FOOTBALL_DATA_ORG


def test_full_agreement() -> None:
    m = [rec(OF, (2, 1), (1, 0), ((H, "12"), (A, "55"), (H, "80"))),
         rec(AF, (2, 1), (1, 0), ((H, "12"), (A, "55"), (H, "80"))),
         rec(FD, (2, 1), (1, 0))]
    r = cross_check([("m1", m)], CUT)
    assert (r.ft.rate, r.ht.rate, r.h15.rate, r.h15_vs_ht.rate) == (1.0, 1.0, 1.0, 1.0)
    assert status(r) is CheckStatus.PASS and evaluate(r, RULES).fifteen_verified and r.disagreements == ()


def test_goal_minute_disagreement_is_reported() -> None:
    """Same scores, but one source puts the goal at 14' and the other at 16': the 15' results differ."""
    m = [rec(OF, (1, 0), (1, 0), ((H, "14"),)), rec(AF, (1, 0), (1, 0), ((H, "16"),))]
    r = cross_check([("m1", m)], CUT)
    assert r.ft.rate == 1.0 and r.h15.rate == 0.0 and status(r) is CheckStatus.FAIL
    assert evaluate(r, RULES).failed == ("H15",)
    (d,) = [d for d in r.disagreements if d.kind == "H15"]
    assert dict(d.values) == {OF: "1-0", AF: "0-0"}


def test_score_disagreements_and_threshold() -> None:
    good = [rec(OF, (1, 0), (0, 0), ((H, "60"),)), rec(AF, (1, 0), (0, 0), ((H, "60"),))]
    bad_ft = [rec(OF, (2, 0), (0, 0), ((H, "60"), (H, "70"))), rec(FD, (1, 0), (0, 0))]
    matches = [(f"g{i}", good) for i in range(49)] + [("x", bad_ft)]
    r = cross_check(matches, CUT)
    assert r.ft.compared == 50 and r.ft.agreed == 49 and r.h15.rate == 1.0
    assert status(r) is CheckStatus.PASS                            # 49/50 = 98 %: exactly at the threshold
    assert [d.kind for d in r.disagreements] == ["FT"]
    worse = cross_check([*matches, ("y", bad_ft)], CUT)             # 49/51 = 96 %
    assert evaluate(worse, RULES).failed == ("FT",) and status(worse) is CheckStatus.FAIL


def test_small_comparisons_are_reported_not_judged() -> None:
    """The real config: a value compared on fewer than min_compared matches is not judged, even at 0 %."""
    rules = load_quality_config().crosscheck
    m = [rec(OF, (1, 0), (1, 0), ((H, "14"),)), rec(AF, (1, 0), (1, 0), ((H, "16"),))]
    r = cross_check([(f"m{i}", m) for i in range(rules.min_compared - 1)], CUT)
    v = evaluate(r, rules)
    assert r.h15.rate == 0.0 and "H15" not in v.judged and not v.fifteen_verified and v.failed == ()
    assert evaluate(cross_check([], CUT), rules).status is CheckStatus.NOT_MEASURABLE


def test_15_minute_score_must_fit_every_other_source_ht() -> None:
    """openfootball says a goal at 10', football-data.org says HT 0-0: one of them is wrong."""
    m = [rec(OF, (1, 0), (1, 0), ((H, "10"),)), rec(FD, (1, 0), (0, 0))]
    r = cross_check([("m1", m)], CUT)
    assert r.h15_vs_ht.rate == 0.0 and {d.kind for d in r.disagreements} == {"HT", "H15_VS_HT"}


def test_single_source_and_unusable_goal_lists_are_not_compared() -> None:
    single = [rec(OF, (1, 0), (0, 0), ((H, "60"),))]
    inconsistent = [rec(OF, (2, 0), (1, 0), ((H, "60"),)), rec(AF, (2, 0), (1, 0), ((H, "5"), (H, "60")))]
    r = cross_check([("s", single), ("i", inconsistent)], CUT)
    assert r.matches == 2 and r.multi_source == 1
    v = evaluate(r, RULES)                                          # OF's list does not add up: not a 15' source
    assert r.h15.compared == 0 and "H15" not in v.judged and not v.fifteen_verified
