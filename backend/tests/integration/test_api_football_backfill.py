"""API-Football backfill job against a real database, with a fake API (no network).

Payloads follow the real /fixtures format (see tests/fixtures/api_football_fixture_events.json).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, text

from app.domain.enums import SourceCode
from app.domain.registry import CompetitionDef, Registry
from app.ingestion import pipeline
from app.ingestion.api_football import ApiFootball
from app.ingestion.base import Fetched, FetchError
from app.storage.raw_store import LocalRawStore
from tests.integration.conftest import ADMIN_URL

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")
LEAGUE, SEASON = "990", 2024
REG = Registry([CompetitionDef(code="TST1", name="Test", country="Testland", type="LEAGUE", timezone="UTC",
                               season_format="SPLIT", fifteen_minute_market=True,
                               team_sources=(SourceCode.API_FOOTBALL,), providers={SourceCode.API_FOOTBALL: LEAGUE})])
TEAMS = [(100 + i, f"Club {i}") for i in range(6)]
N = 30  # finished fixtures in the season


def fixture_item(n: int, with_events: bool) -> dict[str, Any]:
    (hid, hname), (aid, aname) = TEAMS[n % 6], TEAMS[(n + 1 + n // 6) % 6]
    if hid == aid:
        aid, aname = TEAMS[(n + 2) % 6]
    ko = datetime(2024, 8, 10, 15, tzinfo=UTC) + timedelta(days=n)
    # 1-0: goal at 12' (home) for even fixtures, at 70' for odd ones
    minute = 12 if n % 2 == 0 else 70
    item: dict[str, Any] = {
        "fixture": {"id": 5000 + n, "timezone": "UTC", "date": ko.isoformat(), "timestamp": int(ko.timestamp()),
                    "status": {"long": "Match Finished", "short": "FT", "elapsed": 90, "extra": None}},
        "league": {"id": int(LEAGUE), "name": "Test", "country": "Testland", "season": SEASON, "round": f"R{n}"},
        "teams": {"home": {"id": hid, "name": hname}, "away": {"id": aid, "name": aname}},
        "goals": {"home": 1, "away": 0},
        "score": {"halftime": {"home": 1 if minute <= 45 else 0, "away": 0}, "fulltime": {"home": 1, "away": 0}},
    }
    if with_events:
        item["events"] = [{"time": {"elapsed": minute, "extra": None}, "team": {"id": hid, "name": hname},
                           "player": {"id": 1, "name": "Scorer"}, "type": "Goal", "detail": "Normal Goal"}]
    return item


def body(items: list[dict[str, Any]], errors: Any = None) -> bytes:
    return json.dumps({"get": "fixtures", "errors": errors or [], "results": len(items), "response": items}).encode()


class FakeApi:
    """Counts requests like the real client; `free` answers bulk ids with the real free-plan error."""

    def __init__(self, free: bool) -> None:
        self.free, self.requests_used, self.calls = free, 0, []

    def _f(self, endpoint: str, params: dict[str, Any], payload: bytes) -> Fetched:
        self.requests_used += 1
        self.calls.append(params)
        return Fetched(endpoint=endpoint, params=params, http_status=200, body=payload, ext="json")

    def fixtures_by_league_season(self, league_id: str, season: int) -> Fetched:
        assert (league_id, season) == (LEAGUE, SEASON)
        return self._f("/fixtures", {"league": league_id, "season": season},
                       body([fixture_item(n, False) for n in range(N)]))

    def fixture_with_events(self, fixture_id: str) -> Fetched:
        return self._f("/fixtures", {"id": fixture_id}, body([fixture_item(int(fixture_id) - 5000, True)]))

    def fixtures_by_ids(self, ids: list[str]) -> Fetched:
        assert 1 <= len(ids) <= 20
        if self.free:
            return self._f("/fixtures", {"ids": "-".join(ids)},
                           body([], {"plan": "Free plans do not have access to the Ids parameter."}))
        return self._f("/fixtures", {"ids": "-".join(ids)}, body([fixture_item(int(i) - 5000, True) for i in ids]))


@pytest.fixture
def engine(fresh_db_url: str) -> Iterator[Engine]:
    eng = create_engine(fresh_db_url.replace("postgresql://", "postgresql+psycopg://"))
    yield eng
    eng.dispose()


def install(monkeypatch: pytest.MonkeyPatch, fake: FakeApi) -> None:
    monkeypatch.setattr(pipeline, "ApiFootball", lambda key, max_requests, requests_per_minute: fake)


def run(engine: Engine, tmp_path: Path, bulk: bool, max_requests: int) -> Any:
    return pipeline.api_football_backfill(engine, LocalRawStore(tmp_path), REG, "key", "TST1", "2024-25",
                                          bulk, max_requests, requests_per_minute=10)


def minutes(engine: Engine) -> int:
    with engine.connect() as c:
        return c.execute(text("select count(*) from core.matches where data_status = 'COMPLETE'")).scalar_one()


def test_bulk_backfill_on_a_paid_plan(engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeApi(free=False)
    install(monkeypatch, fake)
    stats = run(engine, tmp_path, bulk=True, max_requests=100)
    assert fake.requests_used == 1 + 2                                     # listing + ceil(30 / 20)
    assert stats["fixtures_requested_for_minutes"] == N and stats["fixtures_still_without_minutes"] == 0
    assert minutes(engine) == N
    with engine.connect() as c:                                            # goal at 12' -> 15' score 1-0
        early = c.execute(text("select count(*) from core.matches where goal_before_15")).scalar_one()
    assert early == N // 2


def test_free_plan_is_budgeted_and_resumes(engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeApi(free=True)
    install(monkeypatch, fake)
    first = run(engine, tmp_path, bulk=False, max_requests=11)             # listing + 10 matches
    assert first["fixtures_requested_for_minutes"] == 10 and first["stopped_at_request_budget"] == 1
    assert first["fixtures_still_without_minutes"] == N - 10 and minutes(engine) == 10
    second = run(engine, tmp_path, bulk=False, max_requests=100)            # continues, never repeats
    assert second["already_with_minutes"] == 10 and second["fixtures_requested_for_minutes"] == N - 10
    assert minutes(engine) == N
    requested = [c["id"] for c in fake.calls if "id" in c]
    assert len(requested) == len(set(requested)) == N                     # every match fetched exactly once
    third = run(engine, tmp_path, bulk=False, max_requests=100)
    assert third["fixtures_requested_for_minutes"] == 0                    # nothing left: only the listing


def test_bulk_on_the_free_plan_fails_visibly(engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, FakeApi(free=True))
    with pytest.raises(ValueError, match="Ids parameter"):
        run(engine, tmp_path, bulk=True, max_requests=100)
    with engine.connect() as c:
        status, error = c.execute(text("select status, error from raw.ingestion_runs "
                                       "order by started_at desc limit 1")).one()
    assert status == "FAILED" and "Ids parameter" in error


def test_real_client_guards() -> None:
    client = ApiFootball("key", max_requests=1, requests_per_minute=10)
    with pytest.raises(ValueError):
        client.fixtures_by_ids([str(i) for i in range(21)])
    client.http.requests_used = 1
    with pytest.raises(FetchError, match="budget"):
        client.fixture_with_events("1")
