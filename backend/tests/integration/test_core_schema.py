"""Integration tests for supabase/migrations: the database itself enforces the data rules.

Runs only when TEST_DATABASE_URL points at a disposable PostgreSQL server (superuser), e.g.
    docker run -d --name bettory-pg -e POSTGRES_PASSWORD=test -p 54329:5432 postgres:17
    TEST_DATABASE_URL=postgresql://postgres:test@localhost:54329/postgres pytest tests/integration
A fresh database is created for the test session and dropped afterwards.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg import errors
from psycopg.rows import dict_row

from tests.integration.conftest import ADMIN_URL

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def conn(db_url: str) -> Iterator[psycopg.Connection]:
    """Each test runs in a transaction that is rolled back."""
    with psycopg.connect(db_url, row_factory=dict_row) as c:
        yield c
        c.rollback()


def one(conn: psycopg.Connection, sql: str, *params: object) -> dict:
    row = conn.execute(sql, params).fetchone()
    assert row is not None
    return row


def setup_world(conn: psycopg.Connection) -> dict[str, object]:
    comp = one(conn, "insert into core.competitions (name, country, competition_type, level) "
                     "values ('Premier League', 'England', 'LEAGUE', 1) returning id")["id"]
    friendly = one(conn, "insert into core.competitions (name, country, competition_type) "
                         "values ('Club Friendlies', 'World', 'FRIENDLY') returning id")["id"]
    season = one(conn, "insert into core.competition_seasons (competition_id, label) "
                       "values (%s, '2025-26') returning id", comp)["id"]
    f_season = one(conn, "insert into core.competition_seasons (competition_id, label) "
                         "values (%s, '2025') returning id", friendly)["id"]
    teams = {
        n: one(conn, "insert into core.teams (name, country) values (%s, 'England') returning id", n)["id"]
        for n in ("Alpha", "Beta", "Gamma")
    }
    return {"season": season, "friendly_season": f_season, **teams}


def add_match(conn: psycopg.Connection, season: object, home: object, away: object,
              kickoff: datetime, **cols: object) -> object:
    values = {
        "competition_season_id": season, "home_team_id": home, "away_team_id": away,
        "kickoff_utc": kickoff, "status": "FINISHED", "ft_home": 1, "ft_away": 0,
        "ht_home": 0, "ht_away": 0, "h15_home": 0, "h15_away": 0, "goal_before_15": False,
        "data_status": "COMPLETE", **cols,
    }
    names = ", ".join(values)
    marks = ", ".join(["%s"] * len(values))
    return one(conn, f"insert into core.matches ({names}) values ({marks}) returning id",
               *values.values())["id"]


T0 = datetime(2025, 9, 1, 15, 0, tzinfo=UTC)


def test_sources_seeded(conn: psycopg.Connection) -> None:
    codes = [r["code"] for r in conn.execute("select code from core.sources order by id")]
    assert codes == ["API_FOOTBALL", "FOOTBALL_DATA_ORG", "OPENFOOTBALL", "FOOTBALL_DATA_CO_UK", "MANUAL"]


def test_valid_match_accepted(conn: psycopg.Connection) -> None:
    w = setup_world(conn)
    mid = add_match(conn, w["season"], w["Alpha"], w["Beta"], T0)
    assert one(conn, "select kickoff_date from core.matches where id = %s", mid)["kickoff_date"] == T0.date()


@pytest.mark.parametrize(
    "bad",
    [
        {"ft_home": -1},                                         # negative score
        {"ft_away": None},                                        # half a score pair
        {"ht_home": 3},                                           # HT above FT
        {"h15_home": 1, "goal_before_15": True},                  # 15' above HT
        {"goal_before_15": True},                                 # inconsistent with 0-0 at 15'
        {"ft_home": None, "ft_away": None},                       # FINISHED without score
        {"data_status": "INCOMPLETE"},                            # INCOMPLETE but 15' score set
        {"data_status": "PARTIAL_DRAW_UNKNOWN"},                  # PARTIAL but 15' score set
        {"status": "SCHEDULED"},                                  # scheduled with scores
        {"first_goal_minute": 20},                                # minute without team
        {"data_status": "BOGUS"},                                 # not an allowed value
    ],
)
def test_invalid_match_rejected(conn: psycopg.Connection, bad: dict[str, object]) -> None:
    w = setup_world(conn)
    with pytest.raises((errors.CheckViolation, errors.InvalidTextRepresentation, errors.NotNullViolation)):
        add_match(conn, w["season"], w["Alpha"], w["Beta"], T0, **bad)


def test_same_team_twice_rejected(conn: psycopg.Connection) -> None:
    w = setup_world(conn)
    with pytest.raises(errors.CheckViolation):
        add_match(conn, w["season"], w["Alpha"], w["Alpha"], T0)


def test_duplicate_match_same_day_rejected(conn: psycopg.Connection) -> None:
    w = setup_world(conn)
    add_match(conn, w["season"], w["Alpha"], w["Beta"], T0)
    with pytest.raises(errors.UniqueViolation):
        add_match(conn, w["season"], w["Alpha"], w["Beta"], T0 + timedelta(hours=2))


def test_partial_draw_unknown_accepted(conn: psycopg.Connection) -> None:
    w = setup_world(conn)
    add_match(conn, w["season"], w["Alpha"], w["Beta"], T0, ft_home=2, ft_away=1, ht_home=1, ht_away=1,
              h15_home=None, h15_away=None, goal_before_15=True, data_status="PARTIAL_DRAW_UNKNOWN",
              first_goal_minute=7, first_goal_team_id=w["Alpha"])


def test_match_updates_are_recorded(conn: psycopg.Connection) -> None:
    w = setup_world(conn)
    mid = add_match(conn, w["season"], w["Alpha"], w["Beta"], T0)
    conn.execute("update core.matches set round = 'Matchday 3' where id = %s", (mid,))
    rev = one(conn, "select operation, old_row->>'round' as old_round from core.match_revisions where match_id = %s", mid)
    assert rev == {"operation": "UPDATE", "old_round": None}


def _payload(conn: psycopg.Connection) -> object:
    run = one(conn, "insert into raw.ingestion_runs (source_id, job_name) values (3, 'test') returning id")["id"]
    return one(conn, "insert into raw.payloads (run_id, source_id, endpoint, storage_path, sha256, byte_size) "
                     "values (%s, 3, 'en.1-full.json', 'raw/openfootball/x.json.gz', %s, 10) returning id",
               run, "a" * 64)["id"]


def test_payloads_are_append_only(conn: psycopg.Connection) -> None:
    pid = _payload(conn)
    with pytest.raises(errors.RaiseException):
        conn.execute("update raw.payloads set byte_size = 11 where id = %s", (pid,))


def test_duplicate_payload_rejected(conn: psycopg.Connection) -> None:
    _payload(conn)
    run = one(conn, "select id from raw.ingestion_runs limit 1")["id"]
    with pytest.raises(errors.UniqueViolation):
        conn.execute("insert into raw.payloads (run_id, source_id, endpoint, storage_path, sha256, byte_size) "
                     "values (%s, 3, 'e', 'p', %s, 1)", (run, "a" * 64))


def test_source_record_link_once_then_frozen(conn: psycopg.Connection) -> None:
    w = setup_world(conn)
    mid = add_match(conn, w["season"], w["Alpha"], w["Beta"], T0)
    pid = _payload(conn)
    rid = one(conn, "insert into core.match_source_records (source_id, provider_match_key, payload_id, kickoff_date, home_name, "
                    "away_name, status, ft_home, ft_away, has_goal_minutes, parser_version) "
                    "values (3, 'k', %s, '2025-09-01', 'Alpha', 'Beta', 'FINISHED', 1, 0, false, 'p1') returning id", pid)["id"]
    conn.execute("update core.match_source_records set match_id = %s where id = %s", (mid, rid))   # link: allowed
    with pytest.raises(errors.RaiseException), conn.transaction():
        conn.execute("update core.match_source_records set ft_home = 2 where id = %s", (rid,))  # data change
    with pytest.raises(errors.RaiseException), conn.transaction():
        conn.execute("delete from core.match_source_records where id = %s", (rid,))


def test_odds_validated_and_append_only(conn: psycopg.Connection) -> None:
    w = setup_world(conn)
    mid = add_match(conn, w["season"], w["Alpha"], w["Beta"], T0)
    bm = one(conn, "insert into core.bookmakers (name) values ('Marathonbet') returning id")["id"]
    sql = ("insert into core.odds_snapshots (match_id, bookmaker_id, source_id, market, provider_market_name, "
           "selection, odds, snapshot_type, captured_at) values (%s, %s, 1, 'FIRST15_RESULT', '1x2 - 15 minutes', "
           "'DRAW', %s, 'ANALYSIS', now()) returning id")
    oid = one(conn, sql, mid, bm, 1.36)["id"]
    with pytest.raises(errors.RaiseException), conn.transaction():
        conn.execute("update core.odds_snapshots set odds = 1.40 where id = %s", (oid,))
    with pytest.raises(errors.CheckViolation), conn.transaction():
        conn.execute(sql, (mid, bm, 0.95))


def test_team_history_point_in_time_and_categories(conn: psycopg.Connection) -> None:
    w = setup_world(conn)
    s = w["season"]
    m1 = add_match(conn, s, w["Alpha"], w["Beta"], T0)                           # Alpha home
    m2 = add_match(conn, s, w["Gamma"], w["Alpha"], T0 + timedelta(days=7))      # Alpha away
    m3 = add_match(conn, s, w["Alpha"], w["Gamma"], T0 + timedelta(days=14))     # Alpha home
    add_match(conn, s, w["Beta"], w["Alpha"], T0 + timedelta(days=21),          # CONFLICT: excluded
              reconciliation_status="CONFLICT")
    add_match(conn, w["friendly_season"], w["Alpha"], w["Beta"], T0 + timedelta(days=3))  # friendly: excluded
    add_match(conn, s, w["Beta"], w["Gamma"], T0 + timedelta(days=1))             # not Alpha

    def hist(cat: str, as_of: datetime, limit: int = 10) -> list[object]:
        rows = conn.execute("select id from core.team_history(%s, %s, %s, %s)", (w["Alpha"], cat, as_of, limit))
        return [r["id"] for r in rows]

    as_of = T0 + timedelta(days=14)               # exactly m3's kick-off -> m3 must NOT be returned
    assert hist("GENERAL", as_of) == [m2, m1]
    assert hist("HOME", as_of) == [m1]
    assert hist("AWAY", as_of) == [m2]
    assert hist("GENERAL", as_of + timedelta(seconds=1)) == [m3, m2, m1]
    assert hist("GENERAL", as_of + timedelta(days=30), limit=2) == [m3, m2]


def test_h2h(conn: psycopg.Connection) -> None:
    w = setup_world(conn)
    s = w["season"]
    a = add_match(conn, s, w["Alpha"], w["Beta"], T0)
    b = add_match(conn, s, w["Beta"], w["Alpha"], T0 + timedelta(days=30))
    add_match(conn, s, w["Alpha"], w["Gamma"], T0 + timedelta(days=10))
    rows = conn.execute("select id from core.h2h(%s, %s, %s)", (w["Alpha"], w["Beta"], T0 + timedelta(days=60)))
    assert [r["id"] for r in rows] == [b, a]


def test_rls_enabled_on_every_table(conn: psycopg.Connection) -> None:
    rows = conn.execute("select schemaname, tablename, rowsecurity from pg_tables "
                        "where schemaname in ('raw', 'core')").fetchall()
    assert rows and all(r["rowsecurity"] for r in rows)
