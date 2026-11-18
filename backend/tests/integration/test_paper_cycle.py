"""Paper betting step 5: the recorder cycle decides from the database alone what is due - capture inside the window
before as_of (once), freeze after as_of (once), results of frozen fixtures still without a 15' result - and a dry run
does nothing. Fake API, no network; the clock is passed in (run start times are moved to it)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text

from app.core.settings import Settings
from app.ingestion.base import Fetched
from app.paper import cycle as cycle_mod
from app.paper.config import PaperConfig, load_paper_config
from app.paper.cycle import CycleInputs, run_cycle
from app.paper.freeze import Provenance
from app.paper.settle import SettleResult
from app.storage.raw_store import LocalRawStore
from app.storage.repositories import ops as ops_repo
from app.storage.repositories import paper as paper_repo
from tests.integration.conftest import ADMIN_URL
from tests.integration.test_paper_capture import (
    AS_OF,
    FIXTURES,
    REG,
    SID,
    FakeClient,
    engine,  # noqa: F401 - the database + fake API fixture
    fixture_item,
)
from tests.integration.test_paper_capture import capture as manual_capture
from tests.support import PARAMS

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")
FULL = load_paper_config()
# the tests of capture / freeze / results / settle load their fixtures themselves: planning off (tested separately)
CFG = FULL.model_copy(update={"plan": FULL.plan.model_copy(update={"enabled": False})})


def inputs(engine: Engine, tmp_path: Path, cfg: PaperConfig = CFG, key: str = "key") -> CycleInputs:  # noqa: F811
    settings = Settings(api_football_key=SecretStr(key), api_football_plan="free")
    return CycleInputs(engine=engine, store=lambda: LocalRawStore(tmp_path), registry=REG, settings=settings,
                       params=PARAMS, cfg=cfg, competitions=["TST1"], provenance=Provenance(), stake=1.0)


def runs_at(engine: Engine, when: datetime) -> None:  # noqa: F811
    """Runs are stamped by the real clock; move the ones just made to the simulated one."""
    with engine.begin() as c:
        c.execute(text("update raw.ingestion_runs set started_at = :t, finished_at = :t + (finished_at - started_at) "
                       "where started_at between now() - interval '10 minutes' and now() + interval '1 minute'"),
                  {"t": when})


def finished_with_events(fid: int, league: str, ko: datetime) -> dict[str, Any]:
    item = fixture_item(fid, league, ko)
    item["fixture"]["status"] = {"long": "Match Finished", "short": "FT", "elapsed": 90, "extra": None}
    item["goals"] = {"home": 1, "away": 0}
    item["score"] = {"halftime": {"home": 0, "away": 0}, "fulltime": {"home": 1, "away": 0}}
    item["events"] = [{"time": {"elapsed": 61, "extra": None}, "team": {"id": item["teams"]["home"]["id"]},
                       "player": {"name": "A"}, "type": "Goal", "detail": "Normal Goal"}]
    return item


def test_cycle_captures_then_freezes_once(engine: Engine, tmp_path: Path) -> None:  # noqa: F811
    x = inputs(engine, tmp_path)
    window = AS_OF - CFG.capture.lead

    early = run_cycle(x, now=window - timedelta(minutes=1))
    assert early.lines == [] and FakeClient.calls == []                   # fixtures unknown, or not due yet

    # the schedule is known (e.g. loaded from another source) but nothing is captured yet
    manual_capture(engine, tmp_path, 2)                                        # not a recorder run
    runs_at(engine, window - timedelta(hours=2))                               # long before the window
    FakeClient.calls.clear()
    FakeClient.updates = {9901: [("BookA", window - timedelta(hours=1), "1.40", "1.55")]}

    now = AS_OF - timedelta(minutes=10)
    dry = run_cycle(x, now=now, dry_run=True)
    assert len(dry.lines) == 1 and "would capture" in dry.lines[0] and FakeClient.calls == []

    done = run_cycle(x, now=now)
    assert done.errors == 0 and "capture CAPTURED" in done.lines[0]
    assert [c.get("fixture") for c in FakeClient.calls] == [None, "9901", "9902"]
    runs_at(engine, now)
    assert ops_repo.run_statuses_since(engine, f"paper_capture:{SID}", now)
    FakeClient.calls.clear()
    again = run_cycle(x, now=now + timedelta(minutes=5))
    assert again.lines == [] and FakeClient.calls == []                   # captured once per window

    frozen = run_cycle(x, now=AS_OF + timedelta(minutes=1))
    assert frozen.errors == 0 and "freeze FROZEN: new 2" in frozen.lines[0] and "no capture" not in frozen.lines[0]
    with engine.connect() as c:
        assert len(paper_repo.frozen(c, str(SID))) == 2
    assert run_cycle(x, now=AS_OF + timedelta(minutes=6)).lines == []      # nothing left to freeze


def test_freeze_without_capture_is_reported(engine: Engine, tmp_path: Path) -> None:  # noqa: F811
    manual_capture(engine, tmp_path, 2)
    runs_at(engine, AS_OF - timedelta(days=1))
    r = run_cycle(inputs(engine, tmp_path), now=AS_OF + timedelta(minutes=2))
    assert len(r.lines) == 1 and "no odds sealed" in r.lines[0]


def test_results_of_frozen_fixtures_are_fetched(engine: Engine, tmp_path: Path,  # noqa: F811
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    x = inputs(engine, tmp_path)
    manual_capture(engine, tmp_path, 2)
    runs_at(engine, AS_OF - timedelta(days=1))
    assert "FROZEN" in run_cycle(x, now=AS_OF + timedelta(minutes=1)).lines[0]

    events_calls: list[str] = []

    def with_events(self: FakeClient, fixture_id: str) -> Fetched:
        events_calls.append(fixture_id)
        fid = int(fixture_id)
        f, lg, ko = next(t for t in FIXTURES if t[0] == fid)
        body = json.dumps({"errors": [], "results": 1, "response": [finished_with_events(f, lg, ko)]}).encode()
        self.requests_used += 1
        return Fetched(endpoint="/fixtures", params={"id": fixture_id}, http_status=200, body=body, ext="json")

    monkeypatch.setattr(FakeClient, "fixture_with_events", with_events, raising=False)
    # the database refuses to settle a match whose kick-off is still in the future (real clock): not tested here
    monkeypatch.setattr(cycle_mod, "settle_due", lambda *_a: SettleResult(0, 0, 0, 0, ()))
    late = FIXTURES[1][2] + timedelta(hours=3)                                 # both frozen fixtures played
    dry = run_cycle(x, now=late, dry_run=True)
    assert dry.lines == ["would fetch the results of 2 fixtures now (up to 8 requests)"] and not events_calls
    r = run_cycle(x, now=late)
    assert r.errors == 0 and r.lines == ["results: 2 of 2 fixtures requested"]
    assert events_calls == ["9901", "9902"]
    with engine.connect() as c:
        known = c.execute(text("select count(*) from core.matches where goal_before_15 is false")).scalar()
        assert known == 2
        assert paper_repo.results_wanted(c, late - timedelta(days=2), late) == []
    runs_at(engine, late)
    again = run_cycle(x, now=late + timedelta(hours=1))                       # (the NIGHT session's capture
    assert not [ln for ln in again.lines if "results" in ln] and events_calls == ["9901", "9902"]  # is due)
