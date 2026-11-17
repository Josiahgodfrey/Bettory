"""The odds check and real bets (docs/API.md step 3) on the synthetic league: the decision card at the frozen
cut-off, prices stored as MANUAL odds, bets recorded with their rule breaches (never refused), cancellation
(append-only), settlement read from the stored 15' result, and performance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.paper.freeze import freeze_session
from tests.integration.conftest import ADMIN_URL
from tests.integration.test_api_frontend import Clock, api, same_time
from tests.integration.test_backtest_runner import REGISTRY, league  # noqa: F401 - the synthetic league fixture
from tests.integration.test_paper_freeze import upcoming_session
from tests.support import PARAMS

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")
# P(Under 0.5) of the synthetic fixtures is about 0.78 (minimum odds about 1.38)
GOOD = {"bookmaker": "Good", "under05": {"yes": 3.30, "no": 1.40}}
SHORT = {"bookmaker": "Short", "under05": {"yes": 3.40, "no": 1.30}}


def real_now() -> str:
    """The app clock runs days ahead in these tests; the database refuses a placed_at after its own clock."""
    return datetime.now(UTC).isoformat()


def played_match(engine: Any) -> tuple[str, bool]:
    """A finished synthetic match with a known 15' score, and whether it ended 0-0 at 15'."""
    with engine.connect() as c:
        row = c.execute(text("select id, h15_home + h15_away = 0 from core.matches where status = 'FINISHED' "
                             "and data_status = 'COMPLETE' order by kickoff_utc desc limit 1")).one()
    return str(row[0]), bool(row[1])


def setup(db: Any) -> tuple[TestClient, Any, list[Any], datetime]:
    engine, url, _ = db
    sid, ids, as_of = upcoming_session(url)
    freeze_session(engine, PARAMS, REGISTRY, sid, now=as_of)
    clock = Clock()
    clock.now = as_of + timedelta(minutes=10)
    return api(engine, clock), engine, ids, as_of


def test_quote_card_and_stored_prices(league: Any, tmp_path: Path) -> None:  # noqa: F811
    c, _, ids, as_of = setup(league)
    card = c.post(f"/matches/{ids[0]}/quotes", json=GOOD).json()
    assert (card["mode"], card["listed"], card["advice"], card["selected_market"]) == ("FROZEN", True, "BET",
                                                                                         "UNDER05")
    assert card["selected_odds"] == 1.40 and card["ev"] > 0 and same_time(card["as_of_utc"], as_of)
    assert card["stored_prices"] == 2 and any(ch["code"] == "QUOTE_UNDER05_PRICE" for ch in card["checks"])

    short = c.post(f"/matches/{ids[0]}/quotes", json={**SHORT, "store": False}).json()
    assert short["advice"] == "NO_BET" and short["selected_market"] is None and short["stored_prices"] == 0

    odds = c.get(f"/matches/{ids[0]}/odds").json()["odds"]
    assert {(o["bookmaker"], o["selection"], o["source"], o["snapshot_type"]) for o in odds} == {
        ("Good", "NO", "MANUAL", "BET_TIME"), ("Good", "YES", "MANUAL", "BET_TIME")}
    assert [b["name"] for b in c.get("/bookmakers").json()] == ["Good"]

    assert c.post(f"/matches/{ids[0]}/quotes", json={"bookmaker": "X"}).status_code == 422
    assert c.post(f"/matches/{ids[0]}/quotes", json={"bookmaker": "X", "under05": {"yes": 0.9, "no": 1.4}}
                  ).status_code == 422
    assert c.post(f"/matches/{ids[0]}/quotes", json={"bookmaker": "X", "under05": {"no": 1.4}}).status_code == 422
    assert c.post("/matches/00000000-0000-0000-0000-000000000000/quotes", json=GOOD).status_code == 404


def test_bets_are_flagged_never_refused_and_settled(league: Any, tmp_path: Path) -> None:  # noqa: F811
    c, engine, ids, _ = setup(league)
    ok = c.post("/bets", json={"match_id": str(ids[0]), "market": "UNDER05", "odds": 1.40, "stake": 5000,
                               "bookmaker": "Good", "placed_at": real_now()})
    assert ok.status_code == 201
    b = ok.json()
    assert (b["rule_breaches"], b["follows_rules"], b["state"], b["currency"]) == ([], True, "PENDING", "TZS")
    assert b["min_odds"] > 1 and b["prediction_id"] and b["home_team"] and b["profit"] == 0

    both = c.post("/bets", json={"match_id": str(ids[0]), "market": "DRAW", "odds": 1.10, "stake": 1000,
                                 "bookmaker": "Good", "placed_at": real_now()}).json()
    assert set(both["rule_breaches"]) == {"BOTH_MARKETS", "BELOW_MIN_ODDS"}

    played, zero_zero = played_match(engine)
    late = c.post("/bets", json={"match_id": played, "market": "UNDER05", "odds": 1.50, "stake": 2000,
                                 "bookmaker": "Good", "placed_at": "2025-01-01T10:00:00+03:00"}).json()
    assert set(late["rule_breaches"]) >= {"NOT_LISTED"} and late["min_odds"] is None
    assert late["state"] == ("WIN" if zero_zero else "LOSE")
    assert abs(late["profit"] - (1000.0 if zero_zero else -2000.0)) < 1e-6

    cancelled = c.post(f"/bets/{both['id']}/cancel", json={"reason": "wrong market typed"}).json()
    assert cancelled["state"] == "CANCELLED" and cancelled["cancel_reason"] == "wrong market typed"
    assert c.post(f"/bets/{both['id']}/cancel", json={"reason": "again"}).status_code == 409
    assert c.post("/bets/00000000-0000-0000-0000-000000000000/cancel", json={"reason": "xyz"}).status_code == 404
    with pytest.raises(Exception, match="append-only"), engine.begin() as conn:
        conn.execute(text("update betting.placed_bets set stake = 1"))

    # after cancelling the DRAW bet, a new DRAW bet is still BOTH_MARKETS (the UNDER05 bet is active)
    again = c.post("/bets", json={"match_id": str(ids[0]), "market": "DRAW", "odds": 1.60, "stake": 1000,
                                  "bookmaker": "Good", "placed_at": real_now()}).json()
    assert "BOTH_MARKETS" in again["rule_breaches"]

    assert len(c.get("/bets").json()) == 4
    assert len(c.get("/bets", params={"include_cancelled": "false"}).json()) == 3
    one = c.get(f"/bets/{b['id']}").json()
    assert one["id"] == b["id"]

    perf = c.get("/performance").json()
    assert perf["currency"] == "TZS" and perf["bets"] == 3 and perf["cancelled"] == 1
    assert perf["overall"]["settled"] == 1 and perf["overall"]["pending"] == 2
    assert set(perf["by_rules"]) == {"FOLLOWED", "BROKEN"} and perf["by_rules"]["FOLLOWED"]["pending"] == 1
    assert len(perf["profit_curve"]) == 1


def test_refused_input(league: Any, tmp_path: Path) -> None:  # noqa: F811
    c, _, ids, _ = setup(league)
    base = {"match_id": str(ids[0]), "market": "UNDER05", "odds": 1.40, "stake": 100, "bookmaker": "Good",
            "placed_at": real_now()}
    future = (datetime.now(UTC) + timedelta(days=400)).isoformat()
    assert c.post("/bets", json={**base, "placed_at": future}).status_code == 422
    assert c.post("/bets", json={**base, "placed_at": "2026-11-21T14:05:00"}).status_code == 422   # no timezone
    assert c.post("/bets", json={**base, "stake": 10**12}).status_code == 422
    assert c.post("/bets", json={**base, "market": "OVER05"}).status_code == 422
    assert c.post("/bets", json={**base, "extra": 1}).status_code == 422
    assert c.post("/bets", json={**base, "match_id": "00000000-0000-0000-0000-000000000000"}).status_code == 404
    assert c.get("/bets", params={"session": "nope"}).status_code == 422
