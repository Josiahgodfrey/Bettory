"""The paper-betting recorder and the Today screen as the frontend sees them (docs/API.md step 2).

    cycle_inputs     what one recorder cycle runs with (shared with the command line and the worker)
    recorder_status  last capture / freeze / results / settlement, today's requests, what is due now (dry run)
    paper_summary    the paper report (the same summary as `paper-report`)
    dashboard        the Today screen in one call: sessions of today and tomorrow, the next one, alerts
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine

from app.api.views import info_json, jsonable, session_infos
from app.backtest.config import load_backtest_config
from app.backtest.paper_report import PaperSummary, paper_json, summarise_paper
from app.backtest.tuning_plan import current_plan_path, load_plan, sha256_of
from app.core.provenance import code_version
from app.core.settings import Settings
from app.domain.params import Params
from app.domain.registry import Registry
from app.domain.sessions import SessionCalendar
from app.ingestion.health_view import snapshot
from app.notifications import outbox
from app.paper.capture import RECORDER_JOB_PREFIX
from app.paper.config import PaperConfig, load_paper_config
from app.paper.cycle import RESULTS_JOB, CycleInputs, CycleResult, run_cycle
from app.paper.freeze import Provenance
from app.storage.raw_store import RawStore, store_from_settings
from app.storage.repositories import notifications as notif_repo
from app.storage.repositories import ops as ops_repo
from app.storage.repositories import paper as paper_repo

ALERT_WINDOW = timedelta(hours=24)
NEXT_SESSION_DAYS = 14  # how far ahead the dashboard looks for the next session with fixtures
FREEZE_LATE = timedelta(minutes=15)  # AWAITING_FREEZE longer than this: the recorder is probably not running


def cycle_inputs(engine: Engine, registry: Registry, params: Params, settings: Settings,
                 cfg: PaperConfig | None = None, store: Callable[[], RawStore] | None = None) -> CycleInputs:
    """Built for every cycle: a new tuning plan or commit is picked up without restarting the worker."""
    plan_path = current_plan_path()
    return CycleInputs(engine=engine, store=store or (lambda: store_from_settings(settings)), registry=registry,
                       settings=settings, params=params, cfg=cfg or load_paper_config(),
                       competitions=load_plan(plan_path).competitions,
                       provenance=Provenance(plan_path.name, sha256_of(plan_path), code_version()),
                       stake=load_backtest_config().estimated_value.stake)


@dataclass
class ManualCycle:
    """The latest cycle started from the API (in this process)."""

    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: CycleResult | None = None
    error: str | None = None
    running: bool = False


MANUAL = ManualCycle()


def run_manual_cycle(x: CycleInputs) -> None:
    MANUAL.running, MANUAL.started_at, MANUAL.finished_at, MANUAL.error = True, datetime.now(UTC), None, None
    try:
        MANUAL.result = run_cycle(x, now=datetime.now(UTC))
    except Exception as exc:  # shown by GET /paper/status
        MANUAL.result, MANUAL.error = None, f"{type(exc).__name__}: {exc}"
    finally:
        MANUAL.running, MANUAL.finished_at = False, datetime.now(UTC)


def _run(r: dict[str, Any] | None) -> dict[str, Any] | None:
    return jsonable(r) if r else None


def recorder_status(x: CycleInputs, now: datetime) -> dict[str, Any]:
    plan = x.settings.api_football_plan
    day = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    with x.engine.connect() as conn:
        marks = paper_repo.recorder_marks(conn)
        counts = notif_repo.counts(conn)
    due = run_cycle(x, now=now, dry_run=True)
    m = MANUAL
    return {
        "now_utc": jsonable(now),
        "competitions": list(x.competitions),
        "api_football_plan": plan,
        "requests_today": ops_repo.requests_since(x.engine, RECORDER_JOB_PREFIX, day),
        "requests_daily_bound": x.cfg.daily_max_requests.for_plan(plan),
        "last_capture": _run(ops_repo.latest_run_like(x.engine, "paper_capture:")),
        "last_results": _run(ops_repo.latest_run_like(x.engine, RESULTS_JOB)),
        "last_freeze_at": jsonable(marks["last_freeze_at"]),
        "last_settlement_at": jsonable(marks["last_settlement_at"]),
        "frozen": marks["frozen"],
        "kicked_off_unsettled": marks["kicked_off_unsettled"],
        "due_now": [] if due.busy else due.lines,
        "cycle_running": due.busy or m.running,
        "manual_cycle": None if m.started_at is None else {
            "started_at": jsonable(m.started_at), "finished_at": jsonable(m.finished_at), "running": m.running,
            "error": m.error, "lines": m.result.lines if m.result else [],
            "errors": m.result.errors if m.result else 0},
        "schedule_every_minutes": x.cfg.schedule.every_minutes,
        "telegram": {"configured": outbox.configured(x.settings), "pending": counts.get("PENDING", 0),
                     "failed": counts.get("FAILED", 0)},
    }


def paper_summary(engine: Engine, params: Params, params_version: str | None) -> PaperSummary:
    with engine.connect() as conn:
        rows = paper_repo.report_rows(conn, params_version)
        chain = paper_repo.verify_chain(conn)
    return summarise_paper(rows, chain, load_backtest_config(), params, params_version)


def dashboard(engine: Engine, params: Params, competitions: tuple[str, ...], now: datetime) -> dict[str, Any]:
    cal = SessionCalendar(params)
    today = now.astimezone(cal.tz).date()
    with engine.connect() as conn:
        infos = session_infos(conn, params, competitions, today - timedelta(days=1), today + timedelta(days=1), now)
        chain = paper_repo.verify_chain(conn)
        marks = paper_repo.recorder_marks(conn)
        ahead = session_infos(conn, params, competitions, today, today + timedelta(days=NEXT_SESSION_DAYS - 1), now)
    sessions = [info_json(i, cal) for i in infos]
    upcoming = [info_json(i, cal) for i in ahead if i.report_state != "NO_FIXTURES" and i.phase != "ENDED"]
    alerts: list[dict[str, Any]] = []
    if not chain.intact:
        alerts.append({"level": "BLOCKING", "code": "CHAIN_BROKEN",
                       "message": f"the frozen record's hash chain is broken at {len(chain.problems)} row(s)"})
    for s, i in zip(sessions, infos, strict=True):
        if i.report_state == "MISSED":
            alerts.append({"level": "WARN", "code": "SESSION_MISSED", "session": s["id"],
                           "message": f"{s['id']} ended without a frozen report: was the recorder running?"})
        elif i.report_state == "AWAITING_FREEZE" and i.as_of is not None and now - i.as_of > FREEZE_LATE:
            alerts.append({"level": "WARN", "code": "FREEZE_LATE", "session": s["id"],
                           "message": f"{s['id']} passed its cut-off {int((now - i.as_of).total_seconds() // 60)} "
                                      "min ago and is not frozen: is the recorder running?"})
    for r in ops_repo.failed_runs_since(engine, now - ALERT_WINDOW):
        alerts.append({"level": "WARN", "code": "RUN_FAILED", "run_id": str(r["id"]),
                       "message": f"{r['job_name']} failed: {r['error'] or 'see the run'}"})
    with engine.connect() as conn:
        blocking = snapshot(conn, 1)["blocking_crosschecks"]
    for b in blocking:
        alerts.append({"level": "BLOCKING", "code": "DATA_CHECK_FAILED",
                       "message": f"{b['competition']} {b['season']}: a failed cross-check blocks bets"})
    return {"now_utc": jsonable(now), "timezone": params.sessions.timezone, "today": today.isoformat(),
            "next_session": upcoming[0] if upcoming else None, "sessions": sessions, "alerts": alerts,
            "paper": {"frozen": marks["frozen"], "last_freeze_at": jsonable(marks["last_freeze_at"]),
                      "kicked_off_unsettled": marks["kicked_off_unsettled"], "chain_intact": chain.intact}}


def report_json(s: PaperSummary) -> dict[str, Any]:
    out: dict[str, Any] = jsonable(paper_json(s))
    return out

