"""Closing odds and CLV (docs/PAPER-BETTING.md 11): the cycle captures the odds of a match with a real bet just
before kick-off, stored as CLOSING, once; after kick-off the bet shows its CLV against the fair closing price and
performance averages it. Fake API, no network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from app.paper.cycle import run_cycle
from app.storage.repositories import betting as bet_repo
from tests.integration.conftest import ADMIN_URL
from tests.integration.test_api_frontend import Clock, api
from tests.integration.test_paper_capture import FIXTURES, FakeClient, capture
from tests.integration.test_paper_capture import engine as capture_engine  # noqa: F401 - fake API world
from tests.integration.test_paper_cycle import inputs

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")
KICKOFF = FIXTURES[0][2]


def test_closing_odds_and_clv(capture_engine: Engine, tmp_path: Path) -> None:  # noqa: F811
    engine = capture_engine
    capture(engine, tmp_path, 2)                                           # fixtures (9901 linked), no odds
    with engine.begin() as c:
        match_id = c.execute(text("select match_id from core.match_source_records where provider_match_key = "
                                  "'9901'")).scalar_one()
        bet_repo.insert_bet(c, bet_repo.NewBet(match_id, f"{KICKOFF.date()}-NOON", None,
                                                "UNDER05", 1.50, 1000, "TZS", "BookA", datetime.now(UTC), None,
                                                ("NOT_LISTED",), None))
    FakeClient.calls.clear()
    FakeClient.updates = {9901: [("BookA", KICKOFF - timedelta(minutes=8), "1.40", "1.55")]}
    x = inputs(engine, tmp_path)

    early = run_cycle(x, now=KICKOFF - timedelta(minutes=30))
    assert not [ln for ln in early.lines if "closing" in ln] and FakeClient.calls == []
    near = run_cycle(x, now=KICKOFF - timedelta(minutes=6))
    assert "closing odds: 1 of 1 matches" in near.lines and [c.get("fixture") for c in FakeClient.calls] == ["9901"]
    with engine.connect() as c:
        types = {r[0] for r in c.execute(text("select distinct snapshot_type::text from core.odds_snapshots"))}
    assert types == {"CLOSING"}
    FakeClient.calls.clear()
    assert not [ln for ln in run_cycle(x, now=KICKOFF - timedelta(minutes=1)).lines if "closing" in ln]
    assert FakeClient.calls == []                                          # once per match

    clock = Clock()
    clock.now = KICKOFF - timedelta(minutes=1)
    client = api(engine, clock)
    assert client.get("/bets").json()[0]["clv"] is None                    # the closing line is not final yet
    clock.now = KICKOFF + timedelta(minutes=1)
    clv = client.get("/bets").json()[0]["clv"]
    assert clv["books"] == 1 and clv["book_closing_odds"] == 1.40
    assert abs(clv["clv_fair"] - (1.50 * clv["fair_closing_p"] - 1)) < 1e-9 and clv["clv_fair"] > 0
    perf = client.get("/performance").json()
    assert perf["overall"]["clv_n"] == 1 and perf["overall"]["clv_beat"] == 1.0
