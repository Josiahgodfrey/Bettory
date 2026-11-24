"""Who is logged in, the settings in force (read-only) and the bookmakers (docs/API.md).

    GET /me           the owner's identity and two-factor level
    GET /settings     params, sessions and time zone, competitions, recorder and Gate B settings, API plan
    GET /bookmakers   bookmakers known to the system
    GET /dashboard    the Today screen: sessions of yesterday .. tomorrow, the next session, alerts, recorder
    GET  /notifications       the Telegram outbox (latest first) and whether Telegram is configured
    POST /notifications/test  send a test message now
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status

from app.api.deps import (
    ClockDep,
    EngineDep,
    OwnerDep,
    ParamsDep,
    RegistryDep,
    ReportCompetitionsDep,
    SettingsDep,
)
from app.api.recorder import dashboard
from app.api.responses import BookmakerOut, DashboardOut, MeOut, NotificationsOut, TestNotificationOut
from app.api.views import jsonable
from app.backtest.config import load_backtest_config
from app.domain.betting_config import load_betting_config
from app.domain.sessions import SessionCalendar
from app.jobs.backfill import load_backfill_config
from app.notifications import messages, outbox
from app.paper.config import load_paper_config
from app.storage.repositories import frontend as fe_repo
from app.storage.repositories import notifications as notif_repo

router = APIRouter(tags=["system"])


@router.get("/me", response_model=MeOut)
def me(owner: OwnerDep, engine: EngineDep, params: ParamsDep, settings: SettingsDep, now: ClockDep,
       tasks: BackgroundTasks) -> dict[str, Any]:
    """The frontend calls this after login: the first call of a login session queues a Telegram login alert."""
    if owner.session_id and not owner.local_bypass:
        tz = SessionCalendar(params).tz
        if outbox.notify(engine, f"login:{owner.session_id}", "LOGIN", messages.login(now, tz)):
            tasks.add_task(outbox.deliver, engine, outbox.sender(settings))
    return {"user_id": owner.user_id, "aal": owner.aal, "local_bypass": owner.local_bypass}


@router.get("/notifications", response_model=NotificationsOut)
def notifications(_: OwnerDep, engine: EngineDep, settings: SettingsDep,
                  limit: Annotated[int, Query(ge=1, le=200)] = 50) -> dict[str, Any]:
    with engine.connect() as conn:
        return {"configured": outbox.configured(settings), "counts": notif_repo.counts(conn),
                "items": jsonable(notif_repo.recent(conn, limit))}


@router.post("/notifications/test", response_model=TestNotificationOut)
def test_notification(_: OwnerDep, engine: EngineDep, settings: SettingsDep, now: ClockDep) -> dict[str, Any]:
    """Queue a test message and send what is queued now."""
    if not outbox.configured(settings):
        raise HTTPException(status.HTTP_409_CONFLICT, "Telegram is not configured: set TELEGRAM_BOT_TOKEN and "
                                                      "TELEGRAM_CHAT_ID in the backend's .env")
    outbox.notify(engine, f"test:{now.isoformat()}", "TEST", "✅ BETTORY test message: notifications work.")
    d = outbox.deliver(engine, outbox.sender(settings))
    return {"sent": d.sent, "failed": d.failed}


@router.get("/settings")
def settings_view(_: OwnerDep, settings: SettingsDep, params: ParamsDep, registry: RegistryDep,
                  competitions: ReportCompetitionsDep) -> dict[str, Any]:
    """Read-only: what the system runs with. Changing a value means a new params file or config commit."""
    bt = load_backtest_config()
    return {
        "environment": settings.environment,
        "timezone": params.sessions.timezone,
        "params_file": settings.params_file,
        "params": jsonable(params.model_dump()),
        "report_competitions": list(competitions),
        "competitions": [jsonable(c.model_dump()) for c in registry.competitions],
        "gate_b": {"paper_betting": jsonable(bt.paper_betting.model_dump()),
                   "calibration": jsonable(bt.calibration.model_dump()),
                   "min_qualified": bt.gate_b.min_qualified},
        "stake": bt.estimated_value.stake,
        "betting": jsonable(load_betting_config().model_dump()),  # real bets: currency, max_stake
        "recorder": jsonable(load_paper_config().model_dump()),
        "espn_daily": jsonable(load_backfill_config().espn.model_dump()),  # nightly ESPN fill (docs/ESPN.md)
        "api_football_plan": settings.api_football_plan,
    }


@router.get("/bookmakers", response_model=list[BookmakerOut])
def bookmakers(_: OwnerDep, engine: EngineDep) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [jsonable(b) for b in fe_repo.bookmakers(conn)]


@router.get("/dashboard", response_model=DashboardOut)
def today(_: OwnerDep, engine: EngineDep, params: ParamsDep, competitions: ReportCompetitionsDep,
          now: ClockDep) -> dict[str, Any]:
    return dashboard(engine, params, competitions, now)
