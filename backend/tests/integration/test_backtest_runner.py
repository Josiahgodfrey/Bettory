"""Backtest runner on a synthetic league: sessions, as_of, the cap, settlement, and no look-ahead."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from itertools import permutations
from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg.rows import dict_row
from sqlalchemy import Engine, create_engine

from app.backtest.config import load_backtest_config
from app.backtest.report import summarise
from app.backtest.runner import BacktestRow, BacktestRun, run_backtest
from app.domain.enums import Decision, SourceCode
from app.domain.registry import CompetitionDef, Registry
from app.domain.sessions import SessionCalendar
from tests.integration.conftest import ADMIN_URL
from tests.support import PARAMS, params

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")
CFG = load_backtest_config()
TEAMS = [f"Team {c}" for c in "ABCDEFGHIJKL"]
PATTERNS = [((1, 0), (0, 0), (0, 0)), ((2, 1), (1, 0), (1, 0)), ((0, 0), (0, 0), (0, 0)), ((1, 1), (0, 1), (0, 0)),
            ((3, 1), (2, 0), (0, 0)), ((0, 2), (0, 1), (0, 1)), ((2, 2), (1, 1), (0, 0)), ((1, 2), (0, 0), (0, 0))]
REGISTRY = Registry([CompetitionDef(code="TST1", name="Test League", country="Testland", type="LEAGUE",
                                    timezone="UTC", season_format="SPLIT", fifteen_minute_market=True,
                                    team_sources=(SourceCode.ESPN,),
                                    providers={SourceCode.ESPN: "tt.1"})])
# two kick-offs a day, both in the NOON session (15:00 and 19:00 EAT)
SLOTS = (timedelta(hours=12), timedelta(hours=16))
SEASON_START = {"2024-25": date(2024, 8, 1), "2025-26": date(2025, 8, 1)}


def one(c: psycopg.Connection[dict[str, Any]], sql: str, *p: object) -> Any:
    r = c.execute(sql, p).fetchone()
    assert r is not None
    return r["id"]


def seed_league(url: str) -> dict[tuple[str, int], UUID]:
    """The synthetic league TST1: 12 teams, two finished seasons (also used by the e2e server, tests/e2e)."""
    ids: dict[tuple[str, int], UUID] = {}
    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as c:
        comp = one(c, "insert into core.competitions (code, name, country, competition_type, level) "
                      "values ('TST1', 'Test League', 'Testland', 'LEAGUE', 1) returning id")
        team = {n: one(c, "insert into core.teams (name, country) values (%s, 'Testland') returning id", n)
                for n in TEAMS}
        for label, start in SEASON_START.items():
            season = one(c, "insert into core.competition_seasons (competition_id, label) values (%s, %s) "
                            "returning id", comp, label)
            for k, (h, a) in enumerate(permutations(TEAMS, 2)):     # 132 matches, 2 a day
                ft, ht, h15 = PATTERNS[k % len(PATTERNS)]
                ko = datetime.combine(start + timedelta(days=k // 2), datetime.min.time(), UTC) + SLOTS[k % 2]
                ids[(label, k)] = one(
                    c, "insert into core.matches (competition_season_id, home_team_id, away_team_id, kickoff_utc, "
                       "status, ft_home, ft_away, ht_home, ht_away, h15_home, h15_away, goal_before_15, data_status) "
                       "values (%s, %s, %s, %s, 'FINISHED', %s, %s, %s, %s, %s, %s, %s, 'COMPLETE') returning id",
                    season, team[h], team[a], ko, *ft, *ht, *h15, sum(h15) > 0)
    return ids


@pytest.fixture
def league(fresh_db_url: str) -> Iterator[tuple[Engine, str, dict[tuple[str, int], UUID]]]:
    ids = seed_league(fresh_db_url)
    eng = create_engine(fresh_db_url.replace("postgresql://", "postgresql+psycopg://"))
    yield eng, fresh_db_url, ids
    eng.dispose()


FIRST, LAST = date(2025, 9, 1), date(2025, 9, 20)       # 2025-26 days 31-50 (the season has 66 days)


def backtest(engine: Engine, p: Any = PARAMS) -> BacktestRun:
    return run_backtest(engine, p, REGISTRY, CFG, FIRST, LAST)


def probabilities(run: BacktestRun) -> dict[str, tuple[Any, ...]]:
    return {r.fixture_id: (r.decision, r.p_under05, r.p_draw, r.min_odds_under05, r.p_league_00) for r in run.rows}


def test_sessions_as_of_and_settlement(league: tuple[Engine, str, dict[tuple[str, int], UUID]]) -> None:
    engine, _, _ = league
    run = backtest(engine)
    cal = SessionCalendar(PARAMS)
    assert len(run.rows) == 40 and len(run.sessions) == 20                     # 2 fixtures per NOON session
    for r in run.rows:
        first_ko = min(x.kickoff_utc for x in run.rows if x.session == r.session)
        assert cal.session_of(r.kickoff_utc) == r.session
        assert r.as_of == cal.run_start(r.session, first_ko) < r.kickoff_utc   # one as_of per session, as live
        assert r.result is not None and r.result.under05 is not None
        assert r.error is None
    priced = [r for r in run.rows if r.p_under05 is not None]
    assert priced and all(r.p_league_00 is not None and r.p_model_a is not None for r in priced)
    s = summarise(run, CFG, date(2025, 9, 11))
    assert sum(s.decisions.values()) == 40 and len(s.gate) == 4
    assert s.calibration["UNDER05"]["priced"][0] is not None


def test_no_look_ahead(league: tuple[Engine, str, dict[tuple[str, int], UUID]]) -> None:
    """Changing a result inside a session changes no verdict in that session (as_of is before both
    kick-offs) - only its own settlement."""
    engine, url, _ = league
    before = backtest(engine)
    target = next(r for r in before.rows if r.session.day == LAST and r.kickoff_utc.hour == 12)  # earlier slot
    with psycopg.connect(url, autocommit=True) as c:
        flipped = (1, 1) if target.result is not None and target.result.under05 else (0, 0)
        c.execute("update core.matches set h15_home = %s, h15_away = %s, goal_before_15 = %s, ft_home = 3, "
                  "ft_away = 3, ht_home = 2, ht_away = 2 where id = %s",
                  (*flipped, flipped != (0, 0), target.fixture_id))
    after = backtest(engine)
    assert probabilities(after) == probabilities(before)
    changed = {r.fixture_id for r, s in zip(before.rows, after.rows, strict=True) if r.result != s.result}
    assert changed == {target.fixture_id}


def test_the_session_cap_is_applied(league: tuple[Engine, str, dict[tuple[str, int], UUID]]) -> None:
    engine, _, _ = league
    loose = params(value__max_model_gap_pts=100, grades__a_max_gap_pts=100, grades__b_max_gap_pts=100,
                   red_flags__recent_early_goals_last5=6, red_flags__h2h_min_meetings=100,
                   red_flags__favourite_proxy_home_win_rate=1.0, screen__league_p00_min=0.0)
    uncapped = backtest(engine, loose)
    assert max(s.qualified for s in uncapped.sessions) == 2, [r.failed for r in uncapped.rows][:3]
    capped = backtest(engine, loose.with_values({"sessions.max_matches_per_session": 1}))
    listed: dict[str, list[BacktestRow]] = {}
    for r in capped.rows:
        if r.decision is Decision.QUALIFIED:
            listed.setdefault(str(r.session), []).append(r)
    assert all(len(v) == 1 for v in listed.values())
    assert any(r.decision is Decision.QUALIFIED_OVER_CAP for r in capped.rows)


def test_backtest_never_writes(league: tuple[Engine, str, dict[tuple[str, int], UUID]]) -> None:
    engine, url, _ = league
    with psycopg.connect(url) as c:
        counts = c.execute("select (select count(*) from core.matches), (select count(*) from raw.ingestion_runs)"
                           ).fetchone()
    backtest(engine)
    with psycopg.connect(url) as c:
        assert c.execute("select (select count(*) from core.matches), (select count(*) from raw.ingestion_runs)"
                         ).fetchone() == counts
    with pytest.raises(ValueError, match="unknown competitions"):
        run_backtest(engine, PARAMS, REGISTRY, CFG, FIRST, LAST, ["XXX"])


def test_shared_read_cache_is_identical_and_skips_the_database(
        league: tuple[Engine, str, dict[tuple[str, int], UUID]]) -> None:
    """Parameter variations share one ReadCache: same results as without it, and a warm run reads no history."""
    from sqlalchemy import event

    from app.storage.point_in_time import ReadCache

    engine, _, _ = league
    statements: list[str] = []
    event.listen(engine, "before_cursor_execute", lambda *a: statements.append(a[2]))
    plain = backtest(engine)
    cache = ReadCache()
    statements.clear()
    cold = run_backtest(engine, PARAMS, REGISTRY, CFG, FIRST, LAST, cache=cache)
    cold_reads = len(statements)
    statements.clear()
    other = params(model_a__rating_prior_matches={"FT": 80, "HT": 80})
    warm = run_backtest(engine, other, REGISTRY, CFG, FIRST, LAST, cache=cache)
    assert cold.rows == plain.rows                                       # the cache changes nothing
    assert cache.hits > 0 and len(statements) < 5 < cold_reads           # warm: only the fixture list and shares
    assert not any("team_history" in s or "competition_matches" in s for s in statements)
    assert [r.p_under05 for r in warm.rows] != [r.p_under05 for r in cold.rows]   # params still matter
