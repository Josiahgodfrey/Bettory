"""Data health (docs/DATA-HEALTH.md): the metrics behind every analysis, for the owner.

    GET  /data-health                          everything at once: coverage, blocking checks, runs, open items
    GET  /data-health/coverage                 per competition-season: usable 15' results, conflicts, sources
    GET  /data-health/crosschecks              the current cross-check of every competition-season
    GET  /data-health/crosschecks/{id}         one check, with every stored disagreement
    POST /data-health/crosschecks              run a cross-check now  {competition, season}
    POST /data-health/crosschecks/{id}/accept  accept a FAILED check after review  {note}
    GET  /data-health/runs                     latest ingestion runs with their audit findings
    GET  /data-health/quota                    API-Football requests used today (not counted)
    GET  /data-health/backtests                stored backtest runs with their Gate B status
    GET  /data-health/backtests/{name}         one backtest summary (calibration, models, ROI, Gate B)
"""

from __future__ import annotations

import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import EngineDep, OwnerDep, SettingsDep
from app.api.schemas import AcceptCrossCheck, CrossCheckRequest
from app.core.settings import BACKEND_DIR
from app.domain.params import load_params
from app.domain.quality_config import load_quality_config
from app.ingestion.api_football import ApiFootball
from app.ingestion.base import FetchError
from app.ingestion.health_view import check_summary, snapshot
from app.ingestion.quality import crosscheck_season
from app.storage.repositories import health as health_repo

router = APIRouter(prefix="/data-health", tags=["data health"])
BACKTESTS_DIR = BACKEND_DIR / "backtests"


@router.get("")
def overview(_: OwnerDep, engine: EngineDep,
             runs: Annotated[int, Query(ge=1, le=200)] = 10) -> dict[str, Any]:
    """Coverage, cross-checks that block bets, the latest runs with their audits, and open items."""
    with engine.connect() as conn:
        return snapshot(conn, runs)


@router.get("/coverage")
def coverage(_: OwnerDep, engine: EngineDep) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        result: list[dict[str, Any]] = snapshot(conn, 1)["coverage"]
        return result


@router.get("/crosschecks")
def crosschecks(_: OwnerDep, engine: EngineDep) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [check_summary(c) for c in health_repo.latest_crosschecks(conn)]


@router.get("/crosschecks/{crosscheck_id}")
def crosscheck(crosscheck_id: UUID, _: OwnerDep, engine: EngineDep) -> dict[str, Any]:
    with engine.connect() as conn:
        c = health_repo.crosscheck(conn, crosscheck_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no cross-check with this id")
    return {**check_summary(c), "disagreements": c["disagreements"]}


@router.post("/crosschecks", status_code=status.HTTP_201_CREATED)
def run_crosscheck(body: CrossCheckRequest, _: OwnerDep, engine: EngineDep,
                   settings: SettingsDep) -> dict[str, Any]:
    """Cross-check one competition-season now and store the result (it also runs after every ingestion)."""
    cutoff = load_params(BACKEND_DIR / settings.params_file).settlement.cutoff_label_minute
    with engine.begin() as conn:
        season_id = health_repo.season_id(conn, body.competition, body.season)
        if season_id is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such competition-season in the database")
        stored = crosscheck_season(conn, season_id, cutoff, load_quality_config().crosscheck)
        c = health_repo.crosscheck(conn, stored.id)
    if c is None:  # pragma: no cover - just written in the same transaction
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "cross-check not stored")
    return {**check_summary(c), "disagreements": c["disagreements"]}


@router.post("/crosschecks/{crosscheck_id}/accept")
def accept(crosscheck_id: UUID, body: AcceptCrossCheck, _: OwnerDep, engine: EngineDep) -> dict[str, str]:
    """Accept a FAILED check after reviewing its disagreements: the season's matches can qualify again.
    Only the season's latest check counts, so a new FAIL after new data blocks again."""
    try:
        with engine.begin() as conn:
            health_repo.accept_crosscheck(conn, crosscheck_id, body.note)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return {"status": "ACCEPTED"}


@router.get("/runs")
def runs(_: OwnerDep, engine: EngineDep, limit: Annotated[int, Query(ge=1, le=200)] = 20) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        result: list[dict[str, Any]] = snapshot(conn, limit)["runs"]
        return result


@router.get("/quota")
def quota(_: OwnerDep, settings: SettingsDep) -> dict[str, int]:
    """API-Football requests used today (the /status call is not counted by API-Football)."""
    key = settings.api_football_key.get_secret_value()
    if not key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "API_FOOTBALL_KEY is not configured")
    try:
        used, limit = ApiFootball(key, max_requests=1,
                                  requests_per_minute=settings.api_football_requests_per_minute).quota()
    except FetchError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None
    return {"used": used, "limit": limit, "remaining": max(limit - used, 0)}


def _backtest_summaries() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if BACKTESTS_DIR.is_dir():
        for d in sorted(BACKTESTS_DIR.iterdir()):
            f = d / "summary.json"
            if d.is_dir() and f.is_file():
                out[d.name] = json.loads(f.read_text(encoding="utf-8"))
    return out


@router.get("/backtests")
def backtests(_: OwnerDep) -> list[dict[str, Any]]:
    return [{"name": name, "params_version": s.get("params_version"), "first": s.get("first"),
             "last": s.get("last"), "competitions": s.get("competitions"), "fixtures": s.get("fixtures"),
             "decisions": s.get("decisions"), "gate_b": s.get("gate_b"), "gate_b_passed": s.get("gate_b_passed")}
            for name, s in _backtest_summaries().items()]


@router.get("/backtests/{name}")
def backtest(name: str, _: OwnerDep) -> dict[str, Any]:
    summaries = _backtest_summaries()  # only names that exist on disk: no path is built from the request
    if name not in summaries:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no backtest with this name")
    return summaries[name]
