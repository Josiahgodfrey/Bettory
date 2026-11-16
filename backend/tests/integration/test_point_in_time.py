"""Point-in-time reader against a real database.

Phase 3 exit criteria: correct last-10 windows, nothing after as_of.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg.rows import dict_row
from sqlalchemy import Connection, create_engine

from app.domain.enums import MarketCode
from app.storage.point_in_time import PointInTimeError, PointInTimeReader
from tests.integration.conftest import ADMIN_URL

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")
T0 = datetime(2025, 8, 1, 15, 0, tzinfo=UTC)
TEAMS = [f"Team {c}" for c in "ABCDEFGHIJKL"]


def one(c: psycopg.Connection, sql: str, *p: object) -> Any:
    row = c.execute(sql, p).fetchone()
    assert row is not None
    return row["id"]


@pytest.fixture
def world(fresh_db_url: str) -> Iterator[tuple[Connection, dict[str, Any]]]:
    """12 teams, a 2025-26 league with a double round robin of 22 rounds (one match per team per week),
    a cup match, a friendly, a CONFLICT match, a future fixture and odds snapshots."""
    with psycopg.connect(fresh_db_url, row_factory=dict_row, autocommit=True) as c:
        league = one(c, "insert into core.competitions (code, name, country, competition_type, level) "
                        "values ('TST1', 'Test League', 'Testland', 'LEAGUE', 1) returning id")
        cup = one(c, "insert into core.competitions (name, country, competition_type) "
                     "values ('Test Cup', 'Testland', 'CUP') returning id")
        friendly = one(c, "insert into core.competitions (name, country, competition_type) "
                          "values ('Friendlies', 'World', 'FRIENDLY') returning id")
        season_sql = "insert into core.competition_seasons (competition_id, label) values (%s, %s) returning id"
        ls = one(c, season_sql, league, "2025-26")
        cs = one(c, season_sql, cup, "2025-26")
        fs = one(c, season_sql, friendly, "2025")
        team_sql = "insert into core.teams (name, country) values (%s, 'Testland') returning id"
        team = {n: one(c, team_sql, n) for n in TEAMS}

        def match(season: UUID, home: str, away: str, ko: datetime, **kw: Any) -> UUID:
            v = {"competition_season_id": season, "home_team_id": team[home], "away_team_id": team[away],
                 "kickoff_utc": ko, "status": "FINISHED", "ft_home": 1, "ft_away": 0, "ht_home": 0, "ht_away": 0,
                 "h15_home": 0, "h15_away": 0, "goal_before_15": False, "data_status": "COMPLETE", **kw}
            cols = ", ".join(v)
            return one(c, f"insert into core.matches ({cols}) values ({', '.join(['%s'] * len(v))}) returning id",
                       *v.values())

        # circle-method round robin: every team plays once per round; second half swaps home and away
        names, ids = TEAMS[:], {}
        for rnd in range(22):
            order = [names[0], *names[1:][rnd % 11:], *names[1:][:rnd % 11]]
            for i in range(6):
                h, a = order[i], order[11 - i]
                if (rnd + i) % 2:
                    h, a = a, h
                if rnd >= 11:
                    h, a = a, h
                ids[(rnd, h, a)] = match(ls, h, a, T0 + timedelta(days=7 * rnd, hours=i))
        cup_id = match(cs, "Team A", "Team L", T0 + timedelta(days=7 * 22))
        friendly_id = match(fs, "Team A", "Team B", T0 + timedelta(days=7 * 22 + 1))
        conflict_id = match(ls, "Team B", "Team A", T0 + timedelta(days=7 * 22 + 2), reconciliation_status="CONFLICT")
        future_id = match(ls, "Team A", "Team C", T0 + timedelta(days=7 * 30), status="SCHEDULED", ft_home=None,
                          ft_away=None, ht_home=None, ht_away=None, h15_home=None, h15_away=None, goal_before_15=None,
                          data_status="PENDING")
        bm = one(c, "insert into core.bookmakers (name) values ('Book') returning id")
        for sel, odds, hours in (("UNDER", 2.90, 48), ("OVER", 1.38, 48), ("UNDER", 2.80, 2), ("OVER", 1.40, 2),
                                 ("UNDER", 9.99, -1)):                             # last one: after as_of
            c.execute("insert into core.odds_snapshots (match_id, bookmaker_id, source_id, market, "
                      "provider_market_name, selection, line, odds, snapshot_type, captured_at) "
                      "values (%s, %s, 1, 'HT_TOTAL', 'Goals Over/Under First Half', %s, 0.5, %s, 'ANALYSIS', %s)",
                      (future_id, bm, sel, odds, T0 + timedelta(days=7 * 30) - timedelta(hours=hours)))
        info = {"team": team, "league_season": ls, "cup": cup_id, "friendly": friendly_id, "conflict": conflict_id,
                "future": future_id, "rounds": ids, "kickoff": T0 + timedelta(days=7 * 30)}

    eng = create_engine(fresh_db_url.replace("postgresql://", "postgresql+psycopg://"))
    with eng.connect() as conn:
        yield conn, info
    eng.dispose()


def test_last_10_windows_are_correct_and_ordered(world: tuple[Connection, dict[str, Any]]) -> None:
    conn, w = world
    r = PointInTimeReader(conn, w["kickoff"])
    a = w["team"]["Team A"]
    general = r.team_history(a, "GENERAL", 10)
    home = r.team_history(a, "HOME", 10)
    away = r.team_history(a, "AWAY", 10)
    assert len(general) == len(home) == len(away) == 10
    assert all(m.home_team_id == a for m in home) and all(m.away_team_id == a for m in away)
    assert all(a in (m.home_team_id, m.away_team_id) for m in general)
    for rows in (general, home, away):
        assert [m.kickoff_utc for m in rows] == sorted((m.kickoff_utc for m in rows), reverse=True)
    assert general[0].id == w["cup"]                                     # cups count; friendly / CONFLICT do not
    ids = {m.id for m in general}
    assert w["friendly"] not in ids and w["conflict"] not in ids and w["future"] not in ids


def test_nothing_at_or_after_as_of(world: tuple[Connection, dict[str, Any]]) -> None:
    conn, w = world
    rnd5 = min(k for k in w["rounds"] if k[0] == 5 and "Team A" in k)    # Team A's match in round 5
    as_of = conn.exec_driver_sql("select kickoff_utc from core.matches where id = %(i)s",
                                 {"i": w["rounds"][rnd5]}).scalar_one()
    r = PointInTimeReader(conn, as_of)                                   # exactly that kick-off
    hist = r.team_history(w["team"]["Team A"], "GENERAL", 10)
    assert len(hist) == 5 and all(m.kickoff_utc < as_of for m in hist)
    assert w["rounds"][rnd5] not in {m.id for m in hist}
    later = PointInTimeReader(conn, as_of + timedelta(seconds=1))
    assert len(later.team_history(w["team"]["Team A"], "GENERAL", 10)) == 6
    season = r.competition_matches(w["league_season"])
    assert season and all(m.kickoff_utc < as_of for m in season)


def test_h2h_and_seasons(world: tuple[Connection, dict[str, Any]]) -> None:
    conn, w = world
    r = PointInTimeReader(conn, w["kickoff"])
    meetings = r.h2h(w["team"]["Team A"], w["team"]["Team B"], 10, 5)
    assert len(meetings) == 2                    # 2 league meetings; friendly and CONFLICT excluded
    assert r.domestic_league_season(w["team"]["Team A"], "2025-26") == w["league_season"]
    assert [s.label for s in r.all_league_seasons()] == ["2025-26"]
    assert PointInTimeReader(conn, T0).competition_seasons(r.fixture(w["future"]).competition_id) == []


def test_odds_are_latest_before_as_of(world: tuple[Connection, dict[str, Any]]) -> None:
    conn, w = world
    odds = PointInTimeReader(conn, w["kickoff"]).odds(w["future"], [MarketCode.HT_TOTAL])
    by_sel = {o.selection: o.odds for o in odds}
    assert by_sel == {"UNDER": 2.80, "OVER": 1.40}                       # the 2-hours-before snapshot, not 9.99
    assert all(o.line == 0.5 and o.captured_at < w["kickoff"] for o in odds)


def test_fixture_row_and_guards(world: tuple[Connection, dict[str, Any]]) -> None:
    conn, w = world
    f = PointInTimeReader(conn, w["kickoff"]).fixture(w["future"])
    assert f.competition_code == "TST1" and f.season_label == "2025-26" and f.home_team_id == w["team"]["Team A"]
    with pytest.raises(PointInTimeError):
        PointInTimeReader(conn, datetime(2026, 1, 1))                    # naive timestamp
    bad = PointInTimeReader(conn, w["kickoff"])
    rows = bad.team_history(w["team"]["Team A"], "GENERAL", 3)
    bad.as_of = rows[-1].kickoff_utc                                     # simulate a query that ignored as_of
    with pytest.raises(PointInTimeError, match="not before as_of"):
        bad._guard(rows)
