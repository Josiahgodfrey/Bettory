"""The re-check about an hour before kick-off (docs/PAPER-BETTING.md 10): a listed match that no longer passes the
verification is WITHDRAWN (final, one-way), one whose lineups are both stored is CONFIRMED, otherwise
LINEUPS_PENDING; the database guards; the paper bet of a withdrawn match settles VOID; a real bet placed after
the withdrawal is flagged; the API shows the lineup status. And the fetch: lineups + odds through the fake API."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID

import psycopg
import pytest
from sqlalchemy import Engine, text

from app.analysis.builder import AnalysisContext
from app.decision.reasons import Check, Layer
from app.domain.enums import Decision
from app.domain.sessions import SessionCalendar
from app.ingestion import pipeline
from app.ingestion.base import Fetched
from app.paper import recheck as recheck_mod
from app.paper.config import load_paper_config
from app.paper.freeze import freeze_session
from app.paper.recheck import run_rechecks
from app.paper.settle import settle_due
from app.storage.raw_store import LocalRawStore
from tests.integration.conftest import ADMIN_URL
from tests.integration.test_api_frontend import Clock, api
from tests.integration.test_backtest_runner import REGISTRY, league  # noqa: F401 - the synthetic league fixture
from tests.integration.test_paper_bets import add_odds, latest, set_result
from tests.integration.test_paper_capture import REG, FakeClient, capture
from tests.integration.test_paper_capture import engine as capture_engine  # noqa: F401 - fake API world
from tests.integration.test_paper_freeze import schedule
from tests.support import PARAMS

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")
CFG = load_paper_config().recheck


def failing_for(match_id: UUID, monkeypatch: pytest.MonkeyPatch) -> None:
    """The re-verification of `match_id` now fails a red flag (e.g. the market moved); others are unchanged."""
    original = AnalysisContext.evaluate

    def evaluate(self: AnalysisContext, mid: UUID, *a: Any, **k: Any) -> Any:
        analysis, v = original(self, mid, *a, **k)
        if mid != match_id:
            return analysis, v
        flag = Check(Layer.RED_FLAGS, "CONTEXT_ODDS_MOVEMENT", False, "ODDS_MOVEMENT", "absent")
        return analysis, replace(v, decision=Decision.SKIP, checks=(*v.checks, flag))

    monkeypatch.setattr(recheck_mod.AnalysisContext, "evaluate", evaluate)


def add_lineups(url: str, match_id: UUID) -> None:
    with psycopg.connect(url, autocommit=True) as c:
        row = c.execute("select home_team_id, away_team_id from core.matches where id = %s", (match_id,)).fetchone()
        assert row is not None
        for team in row:
            c.execute("insert into core.match_lineups (match_id, team_id, formation, players, source_id) "
                      "values (%s, %s, '4-4-2', '[]', 1)", (match_id, team))


def test_recheck_withdraws_confirms_and_voids(league: Any, tmp_path: Path,  # noqa: F811
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    engine, url, _ = league
    ko = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=25)
    bet_match, other = schedule(url, [ko, ko + timedelta(seconds=1)])
    cal = SessionCalendar(PARAMS)
    sid = cal.session_of(ko)
    as_of = cal.run_start(sid, ko)
    add_odds(url, bet_match, as_of - timedelta(hours=1))
    assert freeze_session(engine, PARAMS, REGISTRY, sid, now=as_of).bets == 1

    failing_for(bet_match, monkeypatch)
    now = ko - timedelta(minutes=60)
    args: dict[str, Any] = {"now": now, "api_key": "", "max_requests": 0, "requests_per_minute": 10}
    first = run_rechecks(engine, lambda: LocalRawStore(tmp_path), REGISTRY, PARAMS, CFG, **args)
    status = {r.match_id: r.status for r in first.results}
    assert status == {str(bet_match): "WITHDRAWN", str(other): "LINEUPS_PENDING"}
    assert "CONTEXT_ODDS_MOVEMENT" in next(r for r in first.results if r.status == "WITHDRAWN").failed_checks

    add_lineups(url, other)
    later = {**args, "now": now + timedelta(minutes=CFG.retry_minutes)}
    assert run_rechecks(engine, lambda: LocalRawStore(tmp_path), REGISTRY, PARAMS, CFG, **later).due == 0
    with engine.begin() as c:                                         # (the retry interval passes)
        c.execute(text("alter table paper.rechecks disable trigger rechecks_no_change"))
        c.execute(text("update paper.rechecks set checked_at = checked_at - interval '20 minutes'"))
        c.execute(text("alter table paper.rechecks enable trigger rechecks_no_change"))
    second = run_rechecks(engine, lambda: LocalRawStore(tmp_path), REGISTRY, PARAMS, CFG, **later)
    assert [(r.match_id, r.status) for r in second.results] == [(str(other), "CONFIRMED")]
    assert run_rechecks(engine, lambda: LocalRawStore(tmp_path), REGISTRY, PARAMS, CFG, **later).due == 0

    with engine.connect() as c:
        pid, phash = c.execute(text("select id, row_hash from paper.predictions where match_id = :m"),
                               {"m": bet_match}).one()
    with pytest.raises(Exception, match="withdrawal is final"), engine.begin() as c:
        c.execute(text("insert into paper.rechecks (prediction_id, prediction_hash, status, lineups_home, "
                       "lineups_away, decision) values (:p, :h, 'CONFIRMED', true, true, 'QUALIFIED')"),
                  {"p": pid, "h": phash})
    with pytest.raises(Exception, match="append-only"), engine.begin() as c:
        c.execute(text("update paper.rechecks set status = 'CONFIRMED'"))

    clock = Clock()
    clock.now = now
    client = api(engine, clock)
    entries = {e["match_id"]: e for e in client.get(f"/sessions/{sid}").json()["entries"]}
    assert entries[str(bet_match)]["lineup_status"] == "WITHDRAWN"
    assert entries[str(bet_match)]["recheck"]["failed_checks"][-1] == "CONTEXT_ODDS_MOVEMENT"
    assert entries[str(other)]["lineup_status"] == "CONFIRMED"
    listing = client.get("/sessions", params={"from": str(sid.day), "to": str(sid.day)}).json()
    assert next(x for x in listing if x["id"] == str(sid))["withdrawn"] == 1
    clock.now = datetime.now(UTC)                                     # the bet is recorded now
    bet = client.post("/bets", json={"match_id": str(bet_match), "market": "UNDER05", "odds": 1.40, "stake": 100,
                                     "bookmaker": "Good", "placed_at": datetime.now(UTC).isoformat()}).json()
    assert "WITHDRAWN" in bet["rule_breaches"]

    time.sleep(max(0.0, (ko + timedelta(seconds=2) - datetime.now(UTC)).total_seconds()))
    set_result(engine, bet_match, (0, 0), (0, 0), (0, 0))             # the paper bet would have won
    settle_due(engine, datetime.now(UTC) + timedelta(hours=3))
    s = latest(engine, bet_match)
    assert (s["bet_outcome"], s["profit"], s["under05"]) == ("VOID", 0.0, True)


class FakeWithLineups(FakeClient):
    lineup_calls: ClassVar[list[str]] = []

    def lineups(self, fixture_id: str) -> Fetched:
        FakeWithLineups.lineup_calls.append(fixture_id)
        fid = int(fixture_id)
        items = [{"team": {"id": 10 * fid + side, "name": f"Club {10 * fid + side}"}, "formation": "4-3-3",
                  "startXI": [{"player": {"id": i, "name": f"P{i}", "number": i, "pos": "F", "grid": "1:1"}}
                              for i in range(11)],
                  "substitutes": [{"player": {"id": 99, "name": "Sub", "number": 99, "pos": "M", "grid": None}}]}
                 for side in (0, 1)]
        self.requests_used += 1
        body = json.dumps({"errors": [], "results": 2, "response": items}).encode()
        return Fetched(endpoint="/fixtures/lineups", params={"fixture": fixture_id}, http_status=200, body=body,
                       ext="json")


def test_fetch_stores_lineups_and_odds(capture_engine: Engine, tmp_path: Path,  # noqa: F811
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "ApiFootball", FakeWithLineups)
    FakeWithLineups.lineup_calls = []
    capture(capture_engine, tmp_path, 2)                                   # creates the fixtures (teams mapped)
    st = pipeline.api_football_recheck(capture_engine, LocalRawStore(tmp_path), REG, "key", ["9901"], 6,
                                       requests_per_minute=10, job_name="paper_recheck:test")
    assert FakeWithLineups.lineup_calls == ["9901"] and st["fixtures_requested"] == 1
    assert st["lineups_announced"] == 2 and st["lineups"] == 2, sorted(st.items())
    with capture_engine.connect() as c:
        rows = c.execute(text("select formation, jsonb_array_length(players) from core.match_lineups")).all()
    assert sorted(rows) == [("4-3-3", 12), ("4-3-3", 12)]
