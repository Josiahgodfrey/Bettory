"""Sessions and the session report: the Bets section (docs/API.md, ARCHITECTURE.md 12).

    GET /sessions?from=&to=              sessions of a date range (EAT dates; default yesterday .. tomorrow)
    GET /sessions/{id}                   the report: listed matches with minimum odds (frozen, or a preview)
    GET /sessions/{id}/fixtures          every fixture of the session with its decision and failed checks

`preview=false` skips computing a preview before the cut-off (fast: times and fixtures only).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import ClockDep, EngineDep, OwnerDep, ParamsDep, RegistryDep, ReportCompetitionsDep
from app.api.views import info_json, report_json, session_entries, session_infos
from app.domain.sessions import SessionCalendar, SessionId

router = APIRouter(prefix="/sessions", tags=["sessions"])


def parse_session(session_id: str) -> SessionId:
    try:
        return SessionId.parse(session_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "session id must look like 2026-11-21-NOON or 2026-11-21-NIGHT") from exc


@router.get("")
def sessions(_: OwnerDep, engine: EngineDep, params: ParamsDep, competitions: ReportCompetitionsDep, now: ClockDep,
             first: Annotated[date | None, Query(alias="from")] = None,
             last: Annotated[date | None, Query(alias="to")] = None) -> list[dict[str, Any]]:
    cal = SessionCalendar(params)
    today = now.astimezone(cal.tz).date()
    first = first or (last - timedelta(days=2) if last else today - timedelta(days=1))
    last = last or first + timedelta(days=2)
    try:
        with engine.connect() as conn:
            infos = session_infos(conn, params, competitions, first, last, now)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return [info_json(i, cal) for i in infos]


@router.get("/{session_id}")
def report(session_id: str, _: OwnerDep, engine: EngineDep, params: ParamsDep, registry: RegistryDep,
           competitions: ReportCompetitionsDep, now: ClockDep, preview: bool = True) -> dict[str, Any]:
    sid = parse_session(session_id)
    with engine.connect() as conn:
        se = session_entries(conn, params, registry, competitions, sid, now, preview=preview)
    return report_json(se, SessionCalendar(params), listed_only=True)


@router.get("/{session_id}/fixtures")
def fixtures(session_id: str, _: OwnerDep, engine: EngineDep, params: ParamsDep, registry: RegistryDep,
             competitions: ReportCompetitionsDep, now: ClockDep, preview: bool = True) -> dict[str, Any]:
    sid = parse_session(session_id)
    with engine.connect() as conn:
        se = session_entries(conn, params, registry, competitions, sid, now, preview=preview)
    return report_json(se, SessionCalendar(params), listed_only=False)
