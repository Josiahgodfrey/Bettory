"""Paper betting and recorder endpoints, and the Today dashboard (docs/API.md step 2), on the synthetic league."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import recorder
from app.api.deps import raw_store_factory_dep, settings_dep
from app.core.settings import Settings
from app.paper.freeze import freeze_session
from app.storage.raw_store import LocalRawStore
from tests.integration.conftest import ADMIN_URL
from tests.integration.test_api_frontend import Clock, api
from tests.integration.test_backtest_runner import REGISTRY, league  # noqa: F401 - the synthetic league fixture
from tests.integration.test_paper_freeze import upcoming_session
from tests.support import PARAMS

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")


def client(engine: Any, clock: Clock, tmp_path: Path) -> TestClient:
    c = api(engine, clock)
    app: Any = c.app
    app.dependency_overrides[settings_dep] = lambda: Settings(api_football_key=SecretStr(""))
    app.dependency_overrides[raw_store_factory_dep] = lambda: (lambda: LocalRawStore(tmp_path))
    return c


def test_paper_endpoints_on_a_frozen_session(league: Any, tmp_path: Path) -> None:  # noqa: F811
    engine, url, _ = league
    sid, ids, as_of = upcoming_session(url)
    clock = Clock()
    clock.now = as_of + timedelta(minutes=1)
    c = client(engine, clock, tmp_path)
    assert c.get("/paper/chain").json() == {"rows": 0, "intact": True, "head_pos": None, "head_hash": None,
                                            "problems": []}
    assert c.post(f"/paper/replay/{sid}").status_code == 404

    freeze_session(engine, PARAMS, REGISTRY, sid, now=as_of)
    ch = c.get("/paper/chain").json()
    assert ch["rows"] == 2 and ch["intact"] and ch["head_pos"] == 2 and len(ch["head_hash"]) == 64

    preds = c.get("/paper/predictions", params={"session": str(sid)}).json()
    assert [p["chain_pos"] for p in preds] == [2, 1]                          # newest first
    assert {p["match_id"] for p in preds} == {str(i) for i in ids} and preds[0]["fixture"]["home_team"]
    assert all(p["session_id"] == str(sid) and len(p["row_hash"]) == 64 for p in preds)
    assert c.get("/paper/predictions", params={"session": "bad"}).status_code == 422

    rp = c.post(f"/paper/replay/{sid}").json()
    assert rp["identical"] and rp["replays"][0]["compared"] == 2

    rep = c.get("/paper/report").json()
    assert rep["chain"]["intact"] and rep["frozen"] == 2 and "gate" in rep


def test_recorder_status_and_cycle(league: Any, tmp_path: Path) -> None:  # noqa: F811
    engine, _, _ = league
    clock = Clock()
    clock.now = datetime.now(UTC)
    c = client(engine, clock, tmp_path)
    st = c.get("/paper/status").json()
    assert st["frozen"] == 0 and st["requests_today"] == 0 and st["requests_daily_bound"] >= 1
    assert st["last_capture"] is None and st["cycle_running"] is False and isinstance(st["due_now"], list)
    dry = c.post("/paper/cycle", params={"dry_run": "true"})
    assert dry.status_code == 202 and dry.json()["dry_run"] is True
    real = c.post("/paper/cycle")                                              # the background task runs first
    assert real.status_code == 202 and real.json()["started"] is True
    after = c.get("/paper/status").json()["manual_cycle"]
    assert after["running"] is False and after["error"] is None and after["finished_at"]
    recorder.MANUAL.running = True
    try:
        assert c.post("/paper/cycle").status_code == 409
    finally:
        recorder.MANUAL.running = False


def test_dashboard_alerts_follow_the_recorder(league: Any, tmp_path: Path) -> None:  # noqa: F811
    engine, url, _ = league
    sid, _, as_of = upcoming_session(url)
    clock = Clock()
    clock.now = as_of - timedelta(days=1)                                     # the session is tomorrow
    c = client(engine, clock, tmp_path)
    d = c.get("/dashboard").json()
    assert d["alerts"] == []
    assert d["next_session"]["id"] == str(sid) and d["next_session"]["report_state"] == "PLANNED"

    clock.now = as_of + timedelta(minutes=30)                                 # cut-off passed, nothing frozen
    d = c.get("/dashboard").json()
    assert [a["session"] for a in d["alerts"] if a["code"] == "FREEZE_LATE"] == [str(sid)]

    freeze_session(engine, PARAMS, REGISTRY, sid, now=as_of)
    d = c.get("/dashboard").json()
    assert not [a for a in d["alerts"] if a["code"] == "FREEZE_LATE"]
    assert d["next_session"]["report_state"] == "PUBLISHED" and d["paper"]["frozen"] == 2
    assert d["paper"]["chain_intact"] is True
