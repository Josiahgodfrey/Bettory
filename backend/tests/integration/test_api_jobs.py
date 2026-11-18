"""Job endpoints end to end: request -> background run -> database, with stored real payloads (no network)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text

from app.api.deps import Owner, engine_dep, raw_store_factory_dep, require_owner
from app.domain.enums import SourceCode
from app.ingestion import runner as ingest_runner
from app.ingestion.base import Fetched, FetchError
from app.ingestion.openfootball import OpenFootball
from app.main import create_app
from app.storage.raw_store import LocalRawStore
from tests.integration.conftest import ADMIN_URL

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")
FIX = Path(__file__).resolve().parents[1] / "fixtures"
FULL_FILE = "openfootball_en.1-full_2025-26.json"


@pytest.fixture
def engine(fresh_db_url: str) -> Iterator[Engine]:
    eng = create_engine(fresh_db_url.replace("postgresql://", "postgresql+psycopg://"))
    yield eng
    eng.dispose()


@pytest.fixture
def client(engine: Engine, tmp_path: Path) -> TestClient:
    app = create_app()
    app.dependency_overrides[engine_dep] = lambda: engine
    app.dependency_overrides[raw_store_factory_dep] = lambda: (lambda: LocalRawStore(tmp_path))
    app.dependency_overrides[require_owner] = lambda: Owner(user_id="test", aal="aal2")
    return TestClient(app)


@pytest.fixture
def offline_openfootball(monkeypatch: pytest.MonkeyPatch) -> None:
    def season_file(self: OpenFootball, season_label: str, key: str, full: bool) -> Fetched:
        assert (season_label, key, full) == ("2025-26", "en.1", True)
        return Fetched(endpoint=FULL_FILE, params={}, http_status=200, body=(FIX / FULL_FILE).read_bytes(), ext="json")
    monkeypatch.setattr(OpenFootball, "season_file", season_file)


BODY = {"competition": "ENG1", "season": "2025-26", "full": True}


def test_ingest_openfootball_runs_and_reports(client: TestClient, engine: Engine, offline_openfootball: None) -> None:
    resp = client.post("/jobs/ingest/openfootball", json=BODY)
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]

    run = client.get(f"/jobs/runs/{run_id}").json()     # TestClient runs the background task first
    assert run["status"] == "SUCCEEDED" and run["source"] == "OPENFOOTBALL"
    assert run["stats"]["matches_created"] == 6 and run["stats"]["data_status:COMPLETE"] == 6
    with engine.connect() as c:
        assert c.execute(text("select count(*) from core.matches")).scalar_one() == 6

    again = client.post("/jobs/ingest/openfootball", json=BODY).json()["run_id"]
    assert client.get(f"/jobs/runs/{again}").json()["stats"]["payloads_unchanged_skipped"] == 1
    assert [r["id"] for r in client.get("/jobs/runs", params={"source": "OPENFOOTBALL"}).json()] == [again, run_id]


def test_failed_fetch_is_recorded(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: Any) -> Fetched:
        raise FetchError("en.1-full.json: HTTP 503")
    monkeypatch.setattr(OpenFootball, "season_file", boom)
    run_id = client.post("/jobs/ingest/openfootball", json=BODY).json()["run_id"]
    run = client.get(f"/jobs/runs/{run_id}").json()
    assert run["status"] == "FAILED" and "HTTP 503" in run["error"]
    assert not ingest_runner.is_busy(SourceCode.OPENFOOTBALL)            # lock released


def test_crash_before_pipeline_marks_run_failed(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_store() -> LocalRawStore:
        raise RuntimeError("storage unavailable")
    client.app.dependency_overrides[raw_store_factory_dep] = lambda: broken_store  # type: ignore[attr-defined]
    run_id = client.post("/jobs/ingest/openfootball", json=BODY).json()["run_id"]
    run = client.get(f"/jobs/runs/{run_id}").json()
    assert run["status"] == "FAILED" and "storage unavailable" in run["error"]


def test_one_job_per_source(client: TestClient) -> None:
    lock = ingest_runner._locks[SourceCode.OPENFOOTBALL]
    lock.acquire()
    try:
        assert client.post("/jobs/ingest/openfootball", json=BODY).status_code == 409
    finally:
        lock.release()
    assert client.get("/jobs/runs").json() == []                         # no run row was created


@pytest.mark.parametrize(("path", "body"), [
    ("/jobs/ingest/openfootball", {"competition": "XXX9", "season": "2025-26"}),   # unknown competition
    ("/jobs/ingest/openfootball", {"competition": "ENG1", "season": "2025"}),      # wrong season format
    ("/jobs/ingest/openfootball", {"competition": "ENG1", "season": "2025-27"}),   # impossible season
    ("/jobs/ingest/openfootball", {"competition": "eng1", "season": "2025-26"}),   # bad code
    ("/jobs/ingest/openfootball", {**BODY, "extra": 1}),                           # unknown field
    ("/jobs/ingest/api-football-day", {"date": "2026-11-14", "max_event_requests": 60,
                                       "max_odds_requests": 60}),                  # over request budget
])
def test_invalid_requests_rejected(client: TestClient, path: str, body: dict[str, Any]) -> None:
    assert client.post(path, json=body).status_code == 422
    assert client.get("/jobs/runs").json() == []


def test_unknown_run_is_404(client: TestClient) -> None:
    assert client.get("/jobs/runs/00000000-0000-0000-0000-000000000000").status_code == 404


def test_review_flow(client: TestClient, engine: Engine, offline_openfootball: None) -> None:
    client.post("/jobs/ingest/openfootball", json=BODY)                   # creates the teams
    with engine.begin() as c:
        c.execute(text("insert into core.entity_review_queue (source_id, entity_type, provider_key, provider_name) "
                       "values (4, 'TEAM', 'name:England|Spurs', 'Spurs')"))
    (review,) = client.get("/reviews").json()
    (team,) = [t for t in client.get("/teams", params={"q": "tottenham"}).json()]
    assert client.post(f"/reviews/{review['id']}/resolve", json={"team_id": team["id"]}).status_code == 200
    assert client.get("/reviews").json() == []
    assert client.get("/teams", params={"q": "spurs"}).json() == [team]                 # alias added
    assert client.post(f"/reviews/{review['id']}/resolve", json={"team_id": team["id"]}).status_code == 404


def test_review_new_team_flow(client: TestClient, engine: Engine, offline_openfootball: None) -> None:
    client.post("/jobs/ingest/openfootball", json=BODY)                   # creates the teams
    (spurs,) = client.get("/teams", params={"q": "tottenham"}).json()
    rows = [("name:England|Coventry City FC", "Coventry City FC"), ("name:England|Tottenham Women", "Tottenham Women"),
            ("name:England|" + spurs["name"], spurs["name"])]
    with engine.begin() as c:
        for key, name in rows:
            c.execute(text("insert into core.entity_review_queue (source_id, entity_type, provider_key, provider_name, "
                           "context) values (4, 'TEAM', :k, :n, cast(:ctx as jsonb))"),
                      {"k": key, "n": name, "ctx": '{"country": "England"}'})
    reviews = {r["provider_name"]: r["id"] for r in client.get("/reviews").json()}

    new = client.post(f"/reviews/{reviews['Coventry City FC']}/new-team", json={"name": "Coventry City"})
    assert new.status_code == 200
    (cov,) = client.get("/teams", params={"q": "coventry"}).json()
    assert cov["name"] == "Coventry City" and cov["id"] == new.json()["team_id"] and cov["country"] == "England"

    similar = client.post(f"/reviews/{reviews['Tottenham Women']}/new-team", json={})
    assert similar.status_code == 409 and spurs["id"] in similar.json()["detail"]        # a distinctive word shared
    assert client.post(f"/reviews/{reviews['Tottenham Women']}/new-team",
                       json={"allow_similar": True}).status_code == 200                   # confirmed: another club

    taken = client.post(f"/reviews/{reviews[spurs['name']]}/new-team", json={"allow_similar": True})
    assert taken.status_code == 409 and "already a name of team" in taken.json()["detail"]
    assert [r["provider_name"] for r in client.get("/reviews").json()] == [spurs["name"]]  # still open
    assert client.post(f"/reviews/{reviews['Coventry City FC']}/new-team", json={}).status_code == 404  # resolved


def test_team_search_finds_a_team_by_its_name_without_aliases(client: TestClient, engine: Engine) -> None:
    """GET /teams matches the team's own name too, not only its aliases (found by the e2e tests 2026-11-19)."""
    with engine.begin() as c:
        c.execute(text("insert into core.teams (name, country) values ('Team Without Alias', 'Testland')"))
    assert [t["name"] for t in client.get("/teams", params={"q": "without ali"}).json()] == ["Team Without Alias"]
    assert client.get("/teams", params={"q": "no such club"}).json() == []
