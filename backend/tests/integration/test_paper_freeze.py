"""Paper betting step 1: predictions frozen before kick-off on the synthetic league - the session's as_of, idempotence,
the database guards (recording clock, kick-off, append-only), the hash chain and its tamper detection, and the replay
check against the live code."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg.rows import dict_row
from sqlalchemy import text

from app.domain.sessions import SessionCalendar, SessionId, SessionKind
from app.paper.freeze import Provenance, due_sessions, freeze_session, replay_session
from app.storage.repositories import paper as paper_repo
from tests.integration.conftest import ADMIN_URL
from tests.integration.test_backtest_runner import REGISTRY, league  # noqa: F401 - the synthetic league fixture
from tests.support import PARAMS

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")
CAL = SessionCalendar(PARAMS)
PROV = Provenance("params_vtest.yaml", "a" * 64, "abc123")


def schedule(url: str, kickoffs: list[datetime]) -> list[UUID]:
    """SCHEDULED fixtures (in the 2025-26 season of the synthetic league) between its teams."""
    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as c:
        season = c.execute("select id from core.competition_seasons where label = '2025-26'").fetchone()
        teams = [r["id"] for r in c.execute("select id from core.teams order by name").fetchall()]
        assert season is not None
        out = []
        for i, ko in enumerate(kickoffs):
            r = c.execute("insert into core.matches (competition_season_id, home_team_id, away_team_id, "
                          "kickoff_utc, status, data_status) values (%s, %s, %s, %s, 'SCHEDULED', 'PENDING') "
                          "returning id", (season["id"], teams[2 * i], teams[2 * i + 1], ko)).fetchone()
            assert r is not None
            out.append(r["id"])
    return out


def upcoming_session(url: str) -> tuple[SessionId, list[UUID], datetime]:
    """Two fixtures in the NOON session three days ahead (15:00 and 19:00 EAT) and the session's as_of."""
    day = datetime.now(UTC).date() + timedelta(days=3)
    base = datetime.combine(day, datetime.min.time(), UTC)
    kos = [base + timedelta(hours=12), base + timedelta(hours=16)]
    ids = schedule(url, kos)
    sid = SessionId(day, SessionKind.NOON)
    return sid, ids, CAL.run_start(sid, kos[0])


def test_freeze_is_point_in_time_idempotent_and_chained(league: Any) -> None:  # noqa: F811
    engine, url, _ = league
    sid, ids, as_of = upcoming_session(url)
    early = freeze_session(engine, PARAMS, REGISTRY, sid, now=as_of - timedelta(minutes=1), provenance=PROV)
    assert early.status == "TOO_EARLY" and early.as_of == as_of and early.frozen == 0

    r = freeze_session(engine, PARAMS, REGISTRY, sid, now=as_of, provenance=PROV)
    assert (r.status, r.as_of, r.fixtures, r.frozen, r.already, r.started) == ("FROZEN", as_of, 2, 2, 0, 0)
    assert (r.chain_from, r.chain_to) == (1, 2)
    again = freeze_session(engine, PARAMS, REGISTRY, sid, now=as_of + timedelta(hours=1), provenance=PROV)
    assert (again.frozen, again.already) == (0, 2)

    with engine.connect() as conn:
        rows = paper_repo.frozen(conn, str(sid))
        chain = paper_repo.verify_chain(conn)
        stored = conn.execute(text("select plan_sha256, code_version, recorded_at from paper.predictions "
                                   "order by chain_pos")).all()
    assert [x.match_id for x in rows] == ids and all(x.as_of == as_of for x in rows)
    assert chain.intact and chain.rows == 2 and chain.head_pos == 2 and chain.head_hash == rows[-1].row_hash
    assert stored[0][0] == "a" * 64 and stored[0][1] == "abc123"
    assert all(abs((s[2] - datetime.now(UTC)).total_seconds()) < 600 for s in stored)  # the database clock

    rr = replay_session(engine, PARAMS, REGISTRY, str(sid))
    assert rr.compared == 2 and rr.identical


def test_database_refuses_changes_late_rows_and_wrong_kickoffs(league: Any) -> None:  # noqa: F811
    engine, url, ids = league
    sid, _, as_of = upcoming_session(url)
    freeze_session(engine, PARAMS, REGISTRY, sid, now=as_of, provenance=PROV)
    for sql in ("update paper.predictions set p_under05 = 0.5", "delete from paper.predictions",
                "truncate paper.predictions cascade"):
        with pytest.raises(Exception, match="append-only"), engine.begin() as conn:
            conn.execute(text(sql))

    with engine.connect() as conn:
        past = conn.execute(text("select id, kickoff_utc from core.matches where id = :i"),
                            {"i": ids[("2025-26", 0)]}).one()
    row = paper_repo.NewPrediction(
        match_id=past[0], competition_code="TST1", kickoff_utc=past[1], session_id="2025-08-01-NOON",
        as_of=past[1] - timedelta(hours=3), params_version="9.9", formula_version="x", plan_file=None,
        plan_sha256=None, code_version=None, decision="SKIP", grade="SKIP", failed_checks=(), error=None,
        p_under05=None, p_draw=None, p_model_a=None, p_model_b2=None, p_market=None, p_market_draw=None,
        p_league_00=None, p_league_draw=None, min_odds_under05=None, min_odds_draw=None)
    with pytest.raises(Exception, match="not after the recording time"), engine.begin() as conn:
        paper_repo.insert_prediction(conn, row)                     # a match already played
    future = datetime.now(UTC) + timedelta(days=30)
    with pytest.raises(Exception, match=r"differs from core\.matches"), engine.begin() as conn:
        paper_repo.insert_prediction(conn, replace(row, kickoff_utc=future, as_of=future - timedelta(hours=3)))


def test_chain_detects_tampering_around_the_triggers(league: Any) -> None:  # noqa: F811
    engine, url, _ = league
    sid, _, as_of = upcoming_session(url)
    freeze_session(engine, PARAMS, REGISTRY, sid, now=as_of, provenance=PROV)
    sid2 = SessionId(sid.day + timedelta(days=1), SessionKind.NOON)
    base = datetime.combine(sid2.day, datetime.min.time(), UTC)
    schedule(url, [base + timedelta(hours=12)])
    freeze_session(engine, PARAMS, REGISTRY, sid2, now=CAL.run_start(sid2, base + timedelta(hours=12)),
                   provenance=PROV)
    with psycopg.connect(url, autocommit=True) as c:                # a superuser can switch the triggers off
        c.execute("alter table paper.predictions disable trigger predictions_no_change")
        c.execute("update paper.predictions set p_draw = 0.99 where chain_pos = 1")
        c.execute("delete from paper.predictions where chain_pos = 2")
        c.execute("alter table paper.predictions enable trigger predictions_no_change")
    with engine.connect() as conn:
        chain = paper_repo.verify_chain(conn)
    assert not chain.intact
    assert dict(chain.problems) == {1: "content changed after recording", 3: "a row before this one is missing"}


def test_replay_reports_data_that_changed_after_the_freeze(league: Any) -> None:  # noqa: F811
    engine, url, _ = league
    sid, _, as_of = upcoming_session(url)
    freeze_session(engine, PARAMS, REGISTRY, sid, now=as_of, provenance=PROV)
    with engine.connect() as conn:
        priced = [x for x in paper_repo.frozen(conn, str(sid)) if x.p_under05 is not None]
    assert priced, "the synthetic league should price the scheduled fixtures"
    # a result that reaches the database only after the freeze, for a match played before as_of
    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as c:
        m = c.execute("select home_team_id, competition_season_id from core.matches where id = %s",
                      (priced[0].match_id,)).fetchone()
        assert m is not None
        other = c.execute("select id from core.teams where id <> %s order by name desc limit 1",
                          (m["home_team_id"],)).fetchone()
        assert other is not None
        c.execute("insert into core.matches (competition_season_id, home_team_id, away_team_id, kickoff_utc, status, "
                  "ft_home, ft_away, ht_home, ht_away, h15_home, h15_away, goal_before_15, data_status) values "
                  "(%s, %s, %s, %s, 'FINISHED', 5, 4, 3, 2, 2, 1, true, 'COMPLETE')",
                  (m["competition_season_id"], m["home_team_id"], other["id"], as_of - timedelta(days=1)))
    rr = replay_session(engine, PARAMS, REGISTRY, str(sid))
    assert not rr.identical and {d.field for d in rr.differences} >= {"p_under05"}


def test_due_sessions() -> None:
    now = datetime(2026, 11, 18, 7, 0, tzinfo=UTC)               # 10:00 EAT on the 18th
    due = [str(s) for s in due_sessions(CAL, now)]
    assert due == ["2026-11-18-NIGHT", "2026-11-18-NOON", "2026-11-19-NIGHT", "2026-11-19-NOON"]
    assert all(CAL.times(s).end_utc > now for s in due_sessions(CAL, now))

