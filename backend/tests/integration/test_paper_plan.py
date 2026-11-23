"""Session planning (ARCHITECTURE.md 12.4, app/paper/plan.py): each session's fixtures are loaded once from its plan
time - the Noon session at 07:00 EAT the same day, the Night session at 19:00 EAT the evening before (two UTC
dates) - a session in progress is planned late rather than never, an empty session gets one "no fixtures" notice,
and the recorder status shows each session's timeline. Fake API, no network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from app.api.recorder import session_schedule
from app.domain.sessions import SessionId, SessionKind
from app.paper.cycle import run_cycle
from app.paper.plan import plan_time, session_dates
from tests.integration.conftest import ADMIN_URL
from tests.integration.test_paper_capture import CAL, DAY, FIXTURES, SID, FakeClient, engine  # noqa: F401
from tests.integration.test_paper_cycle import FULL, SHIPPED, inputs, runs_at

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")

NOON = SID  # Noon session of DAY: two TST1 fixtures at 15:00 and 19:00 EAT
NIGHT = SessionId(DAY, SessionKind.NIGHT)  # 01:00-12:59 EAT of DAY: no fixture
NOON_BEFORE = SessionId(DAY - timedelta(days=1), SessionKind.NOON)  # the day before: no fixture


def dates_called() -> list[str]:
    return [c["date"] for c in FakeClient.calls if "date" in c]


def notices(engine: Engine) -> list[str]:  # noqa: F811
    with engine.connect() as c:
        return [r[0] for r in c.execute(text("select event_key from ops.notifications order by created_at"))]


def test_plan_times_and_dates() -> None:
    assert plan_time(CAL, FULL, NOON).astimezone(CAL.tz) == datetime.combine(DAY, FULL.plan.noon_time, CAL.tz)
    assert plan_time(CAL, FULL, NIGHT).astimezone(CAL.tz) == datetime.combine(DAY - timedelta(days=1),
                                                                             FULL.plan.night_time, CAL.tz)
    assert session_dates(CAL, NOON) == [DAY]                           # 10:00-21:59 UTC
    assert session_dates(CAL, NIGHT) == [DAY - timedelta(days=1), DAY]  # 22:00 UTC the day before - 09:59 UTC


def test_sessions_are_planned_once_at_their_time(engine: Engine, tmp_path: Path) -> None:  # noqa: F811
    x = inputs(engine, tmp_path, cfg=FULL)
    night_at, noon_at = plan_time(CAL, FULL, NIGHT), plan_time(CAL, FULL, NOON)

    # just before the Night session's plan time: only the session in progress (Noon of the day before) is due -
    # planned late rather than never; it has no fixture -> one "no fixtures" notice
    t0 = night_at - timedelta(minutes=1)
    r0 = run_cycle(x, now=t0)
    runs_at(engine, t0)
    assert dates_called() == [str(DAY - timedelta(days=1))]
    assert [ln for ln in r0.lines if "plan" in ln] == [f"{NOON_BEFORE} plan: 0 fixtures listed (1 date(s))"]

    # 19:00 EAT: the Night session of the next date - two UTC dates in one plan, nothing for the planned session
    t1 = night_at + timedelta(minutes=1)
    assert run_cycle(x, now=t1, dry_run=True).lines == [
        f"{NIGHT} would plan now: fixtures of {DAY - timedelta(days=1)}, {DAY} (2 requests)"]
    run_cycle(x, now=t1)
    runs_at(engine, t1)
    assert dates_called()[1:] == [str(DAY - timedelta(days=1)), str(DAY)]
    with engine.connect() as c:
        known = c.execute(text("select count(*) from core.matches where status::text = 'SCHEDULED'")).scalar()
    assert known == len(FIXTURES)                                   # the listing loaded every fixture of DAY

    # later cycles: nothing is planned twice
    run_cycle(x, now=t1 + timedelta(minutes=5))
    assert len(dates_called()) == 3

    # 07:00 EAT on DAY: the Noon session - it has fixtures, so no notice; its timeline is known
    t2 = noon_at + timedelta(minutes=1)
    run_cycle(x, now=t2)
    runs_at(engine, t2)
    assert dates_called()[3:] == [str(DAY)]
    assert notices(engine) == [f"nofixtures:{NOON_BEFORE}", f"nofixtures:{NIGHT}"]

    sched = {s["id"]: s for s in session_schedule(x, t2)}
    noon = sched[str(NOON)]
    first = min(ko for _, lg, ko in FIXTURES if lg == "990" and CAL.session_of(ko) == NOON)
    assert noon["plan_status"] == "SUCCEEDED" and noon["fixtures"] == 2 and noon["frozen"] == 0
    assert datetime.fromisoformat(noon["first_kickoff_utc"]) == first
    assert datetime.fromisoformat(noon["as_of_utc"]) == CAL.run_start(NOON, first)
    assert datetime.fromisoformat(noon["capture_from_utc"]) == CAL.run_start(NOON, first) - FULL.capture.lead
    assert sched[str(NIGHT)]["fixtures"] == 0 and sched[str(NIGHT)]["as_of_utc"] is None


def test_plan_fails_visibly_without_a_key(engine: Engine, tmp_path: Path) -> None:  # noqa: F811
    x = inputs(engine, tmp_path, cfg=FULL, key="")
    t = plan_time(CAL, FULL, NOON) + timedelta(minutes=1)
    r = run_cycle(x, now=t)
    assert f"! {NOON} plan due but API_FOOTBALL_KEY is not configured" in r.lines and FakeClient.calls == []
    assert all(k.startswith("fail:") for k in notices(engine))  # failures only, never "no fixtures"


def test_planning_can_be_switched_off(engine: Engine, tmp_path: Path) -> None:  # noqa: F811
    off = FULL.model_copy(update={"plan": FULL.plan.model_copy(update={"enabled": False})})
    x = inputs(engine, tmp_path, cfg=off)
    run_cycle(x, now=plan_time(CAL, FULL, NOON) + timedelta(minutes=1))
    assert FakeClient.calls == [] and notices(engine) == []


def test_now_is_timezone_aware() -> None:
    assert plan_time(CAL, FULL, NOON).tzinfo is UTC


def test_history_runs_once_per_session_after_its_plan(engine: Engine, tmp_path: Path,  # noqa: F811
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """The ESPN history step (docs/ESPN.md): after the session's plan, once; its run is not a "paper_" run, so its
    requests never count against the recorder's API-Football bound."""
    from dataclasses import replace

    from app.domain.enums import SourceCode
    from app.domain.registry import Registry
    from app.ingestion import pipeline
    from tests.integration.test_espn_history import FakeEspn

    assert SHIPPED.history.enabled                                   # the shipped config switches it on
    monkeypatch.setattr(pipeline, "Espn", FakeEspn)
    plain = inputs(engine, tmp_path, cfg=FULL.model_copy(update={"history": SHIPPED.history}))
    noon_at = plan_time(CAL, FULL, NOON)

    # the test league has no ESPN code: the step has nothing to fetch and starts no run
    run_cycle(plain, now=noon_at + timedelta(minutes=1))
    runs_at(engine, noon_at + timedelta(minutes=1))
    with engine.connect() as c:
        assert c.execute(text("select count(*) from raw.ingestion_runs "
                              "where job_name like 'espn_history:%'")).scalar() == 0

    # with an ESPN code for the league
    leagues = [c.model_copy(update={"providers": {**c.providers, SourceCode.ESPN: f"tst.{i}"}})
               for i, c in enumerate(plain.registry.competitions)]
    x = replace(plain, registry=Registry(leagues))

    run_cycle(x, now=noon_at - timedelta(minutes=30))
    with engine.connect() as c:
        before = c.execute(text("select count(*) from raw.ingestion_runs "
                                "where job_name like 'espn_history:%'")).scalar()
    assert before == 0                                               # nothing before the plan time

    t = noon_at + timedelta(minutes=1)
    r = run_cycle(x, now=t)
    runs_at(engine, t)
    run_cycle(x, now=t + timedelta(minutes=5))
    with engine.connect() as c:
        rows = c.execute(text("select job_name, status::text from raw.ingestion_runs "
                              "where job_name like 'espn_history:%'")).all()
    assert [tuple(row) for row in rows] == [(f"espn_history:{NOON}", "SUCCEEDED")]
    assert any(ln.startswith(f"{NOON} history (ESPN):") for ln in r.lines)
    noon = {s["id"]: s for s in session_schedule(x, t + timedelta(minutes=6))}[str(NOON)]   # the Recorder screen
    assert (noon["history_enabled"], noon["history_status"], noon["history_summaries"]) == (True, "SUCCEEDED", 0)
    assert noon["history_stopped_at_budget"] is False
