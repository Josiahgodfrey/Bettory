"""Data-health endpoints end to end: an ingestion run (real stored payload, no network) -> audit, automatic
cross-check, coverage -> /data-health; accept rules; backtest summaries; owner-only access."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text

from app.api.deps import Owner, engine_dep, raw_store_factory_dep, require_owner
from app.api.routers import data_health
from app.ingestion.base import Fetched
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
def client(engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    def season_file(self: OpenFootball, season_label: str, key: str, full: bool) -> Fetched:
        return Fetched(endpoint=FULL_FILE, params={}, http_status=200, body=(FIX / FULL_FILE).read_bytes(), ext="json")
    monkeypatch.setattr(OpenFootball, "season_file", season_file)
    monkeypatch.setattr(data_health, "BACKTESTS_DIR", tmp_path / "backtests")
    app = create_app()
    app.dependency_overrides[engine_dep] = lambda: engine
    app.dependency_overrides[raw_store_factory_dep] = lambda: (lambda: LocalRawStore(tmp_path / "raw"))
    app.dependency_overrides[require_owner] = lambda: Owner(user_id="test", aal="aal2")
    return TestClient(app)


def test_ingestion_is_audited_cross_checked_and_visible(client: TestClient, engine: Engine) -> None:
    run_id = client.post("/jobs/ingest/openfootball",
                         json={"competition": "ENG1", "season": "2025-26", "full": True}).json()["run_id"]
    health = client.get("/data-health").json()

    (cov,) = health["coverage"]
    assert (cov["competition"], cov["season"]) == ("ENG1", "2025-26")
    assert cov["finished"] == cov["usable_15"] + cov["unknown_15"] + cov["partial"] > 0
    assert cov["sources"]["OPENFOOTBALL"]["with_minutes"] > 0
    assert cov["crosscheck"]["status"] == "NOT_MEASURABLE"                   # one source: nothing to compare
    assert health["blocking_crosschecks"] == []

    (run,) = [r for r in health["runs"] if r["id"] == run_id]
    assert run["audit_status"] == "OK" and run["stats"]["crosscheck:NOT_MEASURABLE"] == 1

    (check,) = client.get("/data-health/crosschecks").json()
    detail = client.get(f"/data-health/crosschecks/{check['id']}").json()
    assert detail["disagreements"] == [] and detail["matches"] == cov["finished"]
    assert client.post(f"/data-health/crosschecks/{check['id']}/accept", json={"note": "fine"}).status_code == 409


def test_failed_check_blocks_until_accepted(client: TestClient, engine: Engine) -> None:
    client.post("/jobs/ingest/openfootball", json={"competition": "ENG1", "season": "2025-26", "full": True})
    with engine.begin() as c:                                                 # a later check that failed
        failing = c.execute(text("""
            insert into core.crosscheck_results (competition_season_id, status, fifteen_verified, matches,
              multi_source, ft_compared, ft_agreed, ht_compared, ht_agreed, h15_compared, h15_agreed,
              h15_vs_ht_compared, h15_vs_ht_agreed, min_agreement, min_compared, failed_checks)
            select competition_season_id, 'FAIL', false, 380, 380, 380, 300, 0, 0, 0, 0, 0, 0, 0.98, 20, '{FT}'
            from core.crosscheck_results limit 1 returning id""")).scalar_one()
    (blocking,) = client.get("/data-health").json()["blocking_crosschecks"]
    assert blocking["id"] == str(failing) and blocking["blocks_bets"] and blocking["failed_checks"] == ["FT"]
    assert client.post(f"/data-health/crosschecks/{failing}/accept", json={"note": "  "}).status_code == 422
    assert client.post(f"/data-health/crosschecks/{failing}/accept",
                       json={"note": "reviewed 80 FT differences: provider X late corrections"}).status_code == 200
    assert client.get("/data-health").json()["blocking_crosschecks"] == []
    assert client.post(f"/data-health/crosschecks/{failing}/accept", json={"note": "again"}).status_code == 409


def test_run_a_crosscheck_on_demand(client: TestClient) -> None:
    client.post("/jobs/ingest/openfootball", json={"competition": "ENG1", "season": "2025-26", "full": True})
    made = client.post("/data-health/crosschecks", json={"competition": "ENG1", "season": "2025-26"})
    assert made.status_code == 201 and made.json()["status"] == "NOT_MEASURABLE"
    assert client.post("/data-health/crosschecks", json={"competition": "ENG1", "season": "1999-00"}).status_code == 404


def test_backtest_summaries(client: TestClient, tmp_path: Path) -> None:
    assert client.get("/data-health/backtests").json() == []
    run = tmp_path / "backtests" / "v1.5_test"
    run.mkdir(parents=True)
    (run / "summary.json").write_text(json.dumps({"params_version": "1.5", "fixtures": 3, "gate_b_passed": False}))
    (listed,) = client.get("/data-health/backtests").json()
    assert listed["name"] == "v1.5_test" and listed["fixtures"] == 3
    assert client.get("/data-health/backtests/v1.5_test").json()["gate_b_passed"] is False
    assert client.get("/data-health/backtests/..%2Fsecrets").status_code == 404


def test_owner_only(engine: Engine) -> None:
    app = create_app()
    app.dependency_overrides[engine_dep] = lambda: engine
    anonymous = TestClient(app)
    for path in ("/data-health", "/data-health/coverage", "/data-health/runs", "/data-health/backtests"):
        assert anonymous.get(path).status_code in (401, 503)     # 503: auth not configured -> fails closed
