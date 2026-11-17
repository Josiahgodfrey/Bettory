"""One match and the calendar (docs/API.md).

    GET /fixtures?date=YYYY-MM-DD        every fixture of one EAT date (all competitions, or ?competition=ENG1
                                         repeated), with its session and, once frozen, its decision
    GET /matches/{id}                    fixture, scores, 15' result, goals, sources, frozen predictions
    GET /matches/{id}/analysis?session=  every model and check, at the session's frozen as_of (or now: preview)
    GET /matches/{id}/odds               the newest price per bookmaker, market, line and selection
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import ClockDep, EngineDep, OwnerDep, ParamsDep, RegistryDep
from app.api.views import day_window, fixture_json, jsonable, match_analysis
from app.domain.sessions import SessionCalendar
from app.storage.repositories import frontend as fe_repo
from app.storage.repositories import paper as paper_repo

router = APIRouter(tags=["matches"])


def _not_found(match_id: UUID) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"unknown match {match_id}")


@router.get("/fixtures")
def calendar(_: OwnerDep, engine: EngineDep, params: ParamsDep, now: ClockDep,
             day: Annotated[date | None, Query(alias="date")] = None,
             competition: Annotated[list[str] | None, Query()] = None) -> dict[str, Any]:
    cal = SessionCalendar(params)
    day = day or now.astimezone(cal.tz).date()
    start, end = day_window(cal, day)
    decisions: dict[UUID, str] = {}
    with engine.connect() as conn:
        rows = fe_repo.fixtures_between(conn, start, end, competition)
        for sid in sorted({str(cal.session_of(r["kickoff_utc"])) for r in rows}):
            decisions.update({p.match_id: p.decision
                              for p in paper_repo.frozen(conn, sid, params.params_version)})
    out = []
    for r in rows:
        f = fixture_json(r, cal)
        f["decision"] = decisions.get(r["match_id"])
        out.append(f)
    return {"date": day.isoformat(), "timezone": params.sessions.timezone, "fixtures": out}


@router.get("/matches/{match_id}")
def match(match_id: UUID, _: OwnerDep, engine: EngineDep, params: ParamsDep) -> dict[str, Any]:
    cal = SessionCalendar(params)
    with engine.connect() as conn:
        f = fe_repo.match(conn, match_id)
        if f is None:
            raise _not_found(match_id)
        return {**fixture_json(f, cal), "goals": jsonable(fe_repo.goals(conn, match_id)),
                "sources": jsonable(fe_repo.match_sources(conn, match_id)),
                "predictions": jsonable(fe_repo.predictions_for_match(conn, match_id))}


@router.get("/matches/{match_id}/analysis")
def analysis(match_id: UUID, _: OwnerDep, engine: EngineDep, params: ParamsDep, registry: RegistryDep,
             now: ClockDep, session: str | None = None) -> dict[str, Any]:
    with engine.connect() as conn:
        try:
            return match_analysis(conn, params, registry, match_id, session, now)
        except LookupError as exc:
            raise _not_found(match_id) from exc


@router.get("/matches/{match_id}/odds")
def odds(match_id: UUID, _: OwnerDep, engine: EngineDep) -> dict[str, Any]:
    with engine.connect() as conn:
        if fe_repo.match(conn, match_id) is None:
            raise _not_found(match_id)
        return {"match_id": str(match_id), "odds": jsonable(fe_repo.latest_odds(conn, match_id))}
