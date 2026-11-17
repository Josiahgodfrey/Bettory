"""Frontend read endpoints (docs/API.md step 1) on the synthetic league: sessions and their state over time, the
report as a preview before the cut-off and as the frozen record after it, every fixture of a session, the
calendar, one match, its full analysis (recomputed at the frozen as_of and compared), odds, settings."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import (
    Owner,
    clock_dep,
    engine_dep,
    params_dep,
    registry_dep,
    report_competitions_dep,
    require_owner,
)
from app.main import create_app
from app.paper.freeze import freeze_session
from tests.integration.conftest import ADMIN_URL
from tests.integration.test_backtest_runner import REGISTRY, league  # noqa: F401 - the synthetic league fixture
from tests.integration.test_paper_freeze import upcoming_session
from tests.support import PARAMS

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")


def same_time(text: str, when: datetime) -> bool:
    """API times are ISO 8601 UTC with a Z."""
    return text.endswith("Z") and datetime.fromisoformat(text) == when


class Clock:
    now: datetime


def api(engine: Any, clock: Clock) -> TestClient:
    app = create_app()
    app.dependency_overrides[engine_dep] = lambda: engine
    app.dependency_overrides[registry_dep] = lambda: REGISTRY
    app.dependency_overrides[params_dep] = lambda: PARAMS
    app.dependency_overrides[report_competitions_dep] = lambda: ("TST1",)
    app.dependency_overrides[clock_dep] = lambda: clock.now
    app.dependency_overrides[require_owner] = lambda: Owner(user_id="test", aal="aal2")
    return TestClient(app)


def test_session_report_from_preview_to_frozen(league: Any) -> None:  # noqa: F811
    engine, url, _ = league
    sid, _, as_of = upcoming_session(url)
    clock = Clock()
    clock.now = as_of - timedelta(hours=1)
    c = api(engine, clock)
    day = sid.day.isoformat()

    listing = c.get("/sessions", params={"from": day, "to": day}).json()
    assert [s["id"] for s in listing] == [f"{day}-NIGHT", f"{day}-NOON"]
    night, noon = listing
    assert night["report_state"] == "NO_FIXTURES" and night["fixtures"] == 0
    assert (noon["report_state"], noon["phase"], noon["fixtures"], noon["frozen"]) == ("PLANNED", "UPCOMING", 2, 0)
    assert same_time(noon["as_of_utc"], as_of)

    pre = c.get(f"/sessions/{sid}").json()
    assert pre["source"] == "PREVIEW" and sum(pre["decisions"].values()) == 2
    assert all(e["decision"] == "QUALIFIED" for e in pre["entries"])
    fast = c.get(f"/sessions/{sid}", params={"preview": "false"}).json()
    assert fast["source"] == "NONE" and fast["entries"] == []

    freeze_session(engine, PARAMS, REGISTRY, sid, now=as_of)
    clock.now = as_of + timedelta(minutes=1)
    assert c.get("/sessions", params={"from": day, "to": day}).json()[1]["report_state"] == "PUBLISHED"
    rep = c.get(f"/sessions/{sid}").json()
    assert rep["source"] == "FROZEN" and same_time(rep["as_of_utc"], as_of)
    assert rep["session"]["frozen"] == 2 and "minimum odds" in rep["how_to_bet"]
    for e in rep["entries"]:
        assert e["fixture"]["home_team"] and e["fixture"]["competition_code"] == "TST1"
        assert e["min_odds_under05"] > 1 and e["complete_sample"] is not None and e["checks"]
        assert e["lineup_status"] == "NOT_CHECKED" and e["chain_pos"] in (1, 2)
        assert {"layer", "code", "passed", "value", "threshold"} <= set(e["checks"][0])
    # the preview had computed the same numbers as the freeze (same as_of data, nothing changed in between)
    by_id = {e["match_id"]: e for e in rep["entries"]}
    assert all(abs(by_id[e["match_id"]]["p_under05"] - e["p_under05"]) < 1e-9 for e in pre["entries"])

    allf = c.get(f"/sessions/{sid}/fixtures").json()
    assert len(allf["entries"]) == 2 and allf["not_analysed"] == []


def test_match_calendar_analysis_and_odds(league: Any) -> None:  # noqa: F811
    engine, url, _ = league
    sid, ids, as_of = upcoming_session(url)
    freeze_session(engine, PARAMS, REGISTRY, sid, now=as_of)
    clock = Clock()
    clock.now = as_of + timedelta(minutes=5)
    c = api(engine, clock)

    m = c.get(f"/matches/{ids[0]}").json()
    assert m["session_id"] == str(sid) and m["status"] == "SCHEDULED" and m["result_15"] is None
    assert m["goals"] == [] and len(m["predictions"]) == 1

    a = c.get(f"/matches/{ids[0]}/analysis").json()
    assert a["mode"] == "FROZEN" and a["matches_frozen"] is True and same_time(a["as_of_utc"], as_of)
    assert a["checks"] and a["analysis"]["model_a"]["p00"] > 0 and a["verdict"]["decision"] == "QUALIFIED"
    assert a["frozen"]["checks"] == a["checks"]

    cal = c.get("/fixtures", params={"date": sid.day.isoformat()}).json()
    assert [f["match_id"] for f in cal["fixtures"]] == [str(i) for i in ids]
    assert all(f["decision"] == "QUALIFIED" and f["session_id"] == str(sid) for f in cal["fixtures"])

    assert c.get(f"/matches/{ids[0]}/odds").json()["odds"] == []
    unknown = "00000000-0000-0000-0000-000000000000"
    assert c.get(f"/matches/{unknown}").status_code == 404
    assert c.get(f"/matches/{unknown}/analysis").status_code == 404


def test_settings_me_bookmakers_and_bad_input(league: Any) -> None:  # noqa: F811
    engine, _, _ = league
    clock = Clock()
    clock.now = datetime.fromisoformat("2026-11-21T09:00:00+00:00")
    c = api(engine, clock)
    assert c.get("/me").json() == {"user_id": "test", "aal": "aal2", "local_bypass": False}
    s = c.get("/settings").json()
    assert s["timezone"] == PARAMS.sessions.timezone and s["params"]["params_version"] == PARAMS.params_version
    assert s["report_competitions"] == ["TST1"] and s["gate_b"]["paper_betting"]["min_bets"] >= 1
    assert c.get("/bookmakers").json() == []
    assert c.get("/sessions/2026-11-21-EVENING").status_code == 422
    assert c.get("/sessions", params={"from": "2026-11-01", "to": "2026-12-31"}).status_code == 422
    default = c.get("/sessions").json()
    assert [s["date"] for s in default][::2] == ["2026-11-20", "2026-11-21", "2026-11-22"]
