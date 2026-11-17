"""Who is logged in, the settings in force (read-only) and the bookmakers (docs/API.md).

    GET /me           the owner's identity and two-factor level
    GET /settings     params, sessions and time zone, competitions, recorder and Gate B settings, API plan
    GET /bookmakers   bookmakers known to the system
    GET /dashboard    the Today screen: sessions of yesterday .. tomorrow, the next session, alerts, recorder
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

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
from app.api.views import jsonable
from app.backtest.config import load_backtest_config
from app.backtest.tuning_plan import current_plan_path
from app.paper.config import load_paper_config
from app.storage.repositories import frontend as fe_repo

router = APIRouter(tags=["system"])


@router.get("/me")
def me(owner: OwnerDep) -> dict[str, Any]:
    return {"user_id": owner.user_id, "aal": owner.aal, "local_bypass": owner.local_bypass}


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
        "tuning_plan": current_plan_path().name,
        "competitions": [jsonable(c.model_dump()) for c in registry.competitions],
        "gate_b": {"paper_betting": jsonable(bt.paper_betting.model_dump()),
                   "calibration": jsonable(bt.calibration.model_dump()),
                   "min_qualified": bt.gate_b.min_qualified},
        "stake": bt.estimated_value.stake,
        "recorder": jsonable(load_paper_config().model_dump()),
        "api_football_plan": settings.api_football_plan,
    }


@router.get("/bookmakers")
def bookmakers(_: OwnerDep, engine: EngineDep) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [jsonable(b) for b in fe_repo.bookmakers(conn)]


@router.get("/dashboard")
def today(_: OwnerDep, engine: EngineDep, params: ParamsDep, competitions: ReportCompetitionsDep,
          now: ClockDep) -> dict[str, Any]:
    return dashboard(engine, params, competitions, now)
