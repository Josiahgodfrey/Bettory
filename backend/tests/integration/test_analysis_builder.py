"""Fixture-analysis builder: database -> point-in-time reads -> engine -> verdict, on a synthetic league."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from itertools import permutations
from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg.rows import dict_row
from sqlalchemy import Connection, create_engine

from app.analysis.builder import AnalysisContext
from app.domain.enums import Decision, SourceCode
from app.domain.registry import CompetitionDef, Registry
from tests.integration.conftest import ADMIN_URL
from tests.support import PARAMS

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")
T0 = datetime(2025, 8, 1, 15, 0, tzinfo=UTC)
TEAMS = [f"Team {c}" for c in "ABCDEFGHIJKL"]
# (FT, HT, 15') score patterns, cycled: a realistic mix (some early goals, most 15' scores 0-0)
PATTERNS = [((1, 0), (0, 0), (0, 0)), ((2, 1), (1, 0), (1, 0)), ((0, 0), (0, 0), (0, 0)), ((1, 1), (0, 1), (0, 0)),
            ((3, 1), (2, 0), (0, 0)), ((0, 2), (0, 1), (0, 1)), ((2, 2), (1, 1), (0, 0)), ((1, 2), (0, 0), (0, 0))]
REGISTRY = Registry([CompetitionDef(code="TST1", name="Test League", country="Testland", type="LEAGUE",
                                    timezone="UTC", season_format="SPLIT", fifteen_minute_market=True,
                                    team_sources=(SourceCode.OPENFOOTBALL,),
                                    providers={SourceCode.OPENFOOTBALL: "tt.1"})])


def one(c: psycopg.Connection, sql: str, *p: object) -> Any:
    row = c.execute(sql, p).fetchone()
    assert row is not None
    return row["id"]


@pytest.fixture
def world(fresh_db_url: str) -> Iterator[tuple[Connection, dict[str, Any]]]:
    with psycopg.connect(fresh_db_url, row_factory=dict_row, autocommit=True) as c:
        league = one(c, "insert into core.competitions (code, name, country, competition_type, level) "
                        "values ('TST1', 'Test League', 'Testland', 'LEAGUE', 1) returning id")
        season_sql = "insert into core.competition_seasons (competition_id, label) values (%s, %s) returning id"
        current, older = one(c, season_sql, league, "2025-26"), one(c, season_sql, league, "2024-25")
        team = {n: one(c, "insert into core.teams (name, country) values (%s, 'Testland') returning id", n)
                for n in TEAMS}

        def match(season: UUID, home: str, away: str, ko: datetime, k: int, **kw: Any) -> UUID:
            ft, ht, h15 = PATTERNS[k % len(PATTERNS)]
            v = {"competition_season_id": season, "home_team_id": team[home], "away_team_id": team[away],
                 "kickoff_utc": ko, "status": "FINISHED", "ft_home": ft[0], "ft_away": ft[1], "ht_home": ht[0],
                 "ht_away": ht[1], "h15_home": h15[0], "h15_away": h15[1], "goal_before_15": sum(h15) > 0,
                 "data_status": "COMPLETE", **kw}
            return one(c, f"insert into core.matches ({', '.join(v)}) values ({', '.join(['%s'] * len(v))}) "
                          "returning id", *v.values())

        # double round robin: every ordered pair once per season (11 home + 11 away matches per team)
        ids: dict[tuple[str, str, str], UUID] = {}
        pairs = list(permutations(TEAMS, 2))
        for label, season, start in (("2024-25", older, T0 - timedelta(days=365)), ("2025-26", current, T0)):
            for k, (h, a) in enumerate(pairs):
                extra = {}
                if label == "2024-25" and PATTERNS[k % 8][1] != (0, 0):   # older season: no goal minutes
                    extra = {"h15_home": None, "h15_away": None, "goal_before_15": None, "data_status": "INCOMPLETE"}
                ids[(label, h, a)] = match(season, h, a, start + timedelta(hours=12 * k), k, **extra)
        ko = T0 + timedelta(days=90)
        fixture = one(c, "insert into core.matches (competition_season_id, home_team_id, away_team_id, kickoff_utc, "
                         "status) values (%s, %s, %s, %s, 'SCHEDULED') returning id",
                      current, team["Team A"], team["Team B"], ko)
        bm = one(c, "insert into core.bookmakers (name) values ('Pinnacle') returning id")
        for market, line, sel, odds in (("FT_1X2", None, "HOME", 2.10), ("FT_1X2", None, "DRAW", 3.40),
                                        ("FT_1X2", None, "AWAY", 3.60), ("HT_TOTAL", 0.5, "OVER", 1.40),
                                        ("HT_TOTAL", 0.5, "UNDER", 2.80)):
            c.execute("insert into core.odds_snapshots (match_id, bookmaker_id, source_id, market, "
                      "provider_market_name, selection, line, odds, snapshot_type, captured_at) "
                      "values (%s, %s, 1, %s, 'x', %s, %s, %s, 'ANALYSIS', %s)",
                      (fixture, bm, market, sel, line, odds, ko - timedelta(hours=3)))
        info = {"fixture": fixture, "kickoff": ko, "ids": ids, "current": current}

    eng = create_engine(fresh_db_url.replace("postgresql://", "postgresql+psycopg://"))
    with eng.connect() as conn:
        yield conn, info
    eng.dispose()


def test_full_analysis_from_the_database(world: tuple[Connection, dict[str, Any]]) -> None:
    conn, w = world
    ctx = AnalysisContext(conn, PARAMS, REGISTRY, w["kickoff"] - timedelta(hours=2))
    a = ctx.analyse(w["fixture"])
    assert a.windows is not None and a.windows.home_home.sample_size == 10 and a.windows.away_away.sample_size == 10
    assert a.baseline is not None and a.baseline.seasons == ("2025-26",)        # incomplete 2024-25 not pooled
    assert a.model_a is not None and a.model_b2 is not None and a.price is not None, a.unavailable
    assert a.model_c is not None and a.model_c.available and a.favourite_odds == 2.10
    assert a.fifteen_minute_market and a.h2h is not None and a.h2h.meetings == 4    # 2 per season
    v = ctx.verdict(w["fixture"])
    assert v.decision is not Decision.ERROR and v.params_version == PARAMS.params_version
    assert v.p_under05 is not None and 0.5 < v.p_under05 < 0.95 and v.min_odds_under05 is not None


def test_as_of_after_kickoff_is_an_error_never_a_bet(world: tuple[Connection, dict[str, Any]]) -> None:
    conn, w = world
    v = AnalysisContext(conn, PARAMS, REGISTRY, w["kickoff"] + timedelta(minutes=1)).verdict(w["fixture"])
    assert v.decision is Decision.ERROR and v.error is not None and "not a pre-match analysis" in v.error


def test_incomplete_history_is_insufficient_data(world: tuple[Connection, dict[str, Any]], fresh_db_url: str) -> None:
    conn, w = world
    with psycopg.connect(fresh_db_url, autocommit=True) as c:                    # Team A's latest home match
        latest = max((k for k in w["ids"] if k[0] == "2025-26" and k[1] == "Team A"),
                     key=lambda k: TEAMS.index(k[2]))                    # pairs are created in this order
        c.execute("update core.matches set h15_home = null, h15_away = null, goal_before_15 = null, "
                  "data_status = 'INCOMPLETE' where id = %s", (w["ids"][latest],))
    v = AnalysisContext(conn, PARAMS, REGISTRY, w["kickoff"] - timedelta(hours=2)).verdict(w["fixture"])
    assert v.decision is Decision.INSUFFICIENT_DATA
    assert {"WINDOW_HOME_GENERAL", "COMPETITION_BASELINE"} & {c.code for c in v.failed}


def test_unregistered_or_unoffered_competition_is_skipped(world: tuple[Connection, dict[str, Any]]) -> None:
    conn, w = world
    no_market = Registry([REGISTRY.get("TST1").model_copy(update={"fifteen_minute_market": False})])
    v = AnalysisContext(conn, PARAMS, no_market, w["kickoff"] - timedelta(hours=2)).verdict(w["fixture"])
    assert v.decision in (Decision.SKIP, Decision.INSUFFICIENT_DATA)
    assert "FIFTEEN_MINUTE_MARKET" in {c.code for c in v.failed}


def test_untrusted_league_season_cannot_qualify(world: tuple[Connection, dict[str, Any]], fresh_db_url: str) -> None:
    """docs/DATA-HEALTH.md 2: the latest cross-check of a pooled season FAILED -> INSUFFICIENT_DATA, until the
    owner accepts it after review; a newer FAIL blocks again."""
    conn, w = world
    as_of = w["kickoff"] - timedelta(hours=2)
    insert = ("insert into core.crosscheck_results (competition_season_id, status, fifteen_verified, matches, "
              "multi_source, ft_compared, ft_agreed, ht_compared, ht_agreed, h15_compared, h15_agreed, "
              "h15_vs_ht_compared, h15_vs_ht_agreed, min_agreement, min_compared, failed_checks, checked_at) "
              "values (%s, 'FAIL', false, 132, 132, 132, 120, 132, 132, 0, 0, 0, 0, 0.98, 20, '{FT}', %s) returning id")
    with psycopg.connect(fresh_db_url, row_factory=dict_row, autocommit=True) as c:
        first = one(c, insert, w["current"], datetime(2026, 1, 1, tzinfo=UTC))
    v = AnalysisContext(conn, PARAMS, REGISTRY, as_of).verdict(w["fixture"])
    assert v.decision is Decision.INSUFFICIENT_DATA
    (check,) = [ch for ch in v.failed if ch.code == "SOURCE_AGREEMENT"]
    assert "2025-26: FT below" in check.value

    with psycopg.connect(fresh_db_url, autocommit=True) as c:
        c.execute("update core.crosscheck_results set accepted_at = now(), accepted_note = 'reviewed' where id = %s",
                  (first,))
    accepted = AnalysisContext(conn, PARAMS, REGISTRY, as_of).verdict(w["fixture"])
    assert "SOURCE_AGREEMENT" not in {ch.code for ch in accepted.failed}

    with psycopg.connect(fresh_db_url, row_factory=dict_row, autocommit=True) as c:
        one(c, insert, w["current"], datetime(2026, 2, 1, tzinfo=UTC))          # new data, new FAIL
    again = AnalysisContext(conn, PARAMS, REGISTRY, as_of).verdict(w["fixture"])
    assert "SOURCE_AGREEMENT" in {ch.code for ch in again.failed}
