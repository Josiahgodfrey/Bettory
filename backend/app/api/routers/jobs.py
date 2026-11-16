"""Ingestion jobs: start a fetch -> parse -> store run for one source, and follow its status.

Each POST validates the request, creates the run row and returns 202 with the run id at once;
the job itself runs in the background. Poll GET /jobs/runs/{run_id} for the outcome and statistics.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status

from app.api.deps import EngineDep, OwnerDep, RawStoreFactoryDep, RegistryDep, SettingsDep
from app.api.schemas import (
    ApiFootballDayIngest,
    CompetitionOut,
    JobAccepted,
    OpenFootballIngest,
    RunOut,
    SeasonIngest,
)
from app.domain.enums import RunStatus, SourceCode
from app.domain.registry import CompetitionDef, Registry, season_label, season_start_year
from app.ingestion import pipeline
from app.jobs import ingest_runner
from app.storage.repositories import raw as raw_repo

router = APIRouter(prefix="/jobs", tags=["jobs"])


# --- helpers ---------------------------------------------------------------------------------


def _competition(registry: Registry, code: str, source: SourceCode, season: str) -> CompetitionDef:
    try:
        comp = registry.get(code)
    except KeyError:
        known = ", ".join(c.code for c in registry.competitions)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"unknown competition {code}; known: {known}") from None
    if source not in comp.providers:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{code} has no {source.value} mapping")
    expected = season_label(season_start_year(season), comp.season_format)
    if season != expected:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"{code} uses {comp.season_format.value} seasons: write {expected!r}, not {season!r}")
    return comp


def _key(value: str, name: str) -> str:
    if not value:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"{name} is not configured")
    return value


def _start(engine: EngineDep, background: BackgroundTasks, source: SourceCode, job_name: str,
           work: Callable[[UUID], object]) -> JobAccepted:
    try:
        run_id = ingest_runner.submit(engine, source, job_name, work, background.add_task)
    except ingest_runner.SourceBusyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return JobAccepted(run_id=run_id, source=source, job_name=job_name, status_url=f"/jobs/runs/{run_id}")


# --- reference ---------------------------------------------------------------------------------


@router.get("/competitions", response_model=list[CompetitionOut])
def competitions(_: OwnerDep, registry: RegistryDep) -> list[CompetitionOut]:
    """Competitions in the registry, with the sources each one can be fetched from."""
    return [CompetitionOut(code=c.code, name=c.name, country=c.country, season_format=c.season_format.value,
                           sources=sorted(c.providers, key=lambda s: s.value),
                           example_season=season_label(2025, c.season_format))
            for c in registry.competitions]


# --- start jobs --------------------------------------------------------------------------------



@router.post("/ingest/openfootball", status_code=status.HTTP_202_ACCEPTED, response_model=JobAccepted)
def ingest_openfootball(body: OpenFootballIngest, _: OwnerDep, background: BackgroundTasks, engine: EngineDep,
                        registry: RegistryDep, store: RawStoreFactoryDep) -> JobAccepted:
    """openfootball season file. `full=true` gives goal minutes: the best free source for the 15' score."""
    _competition(registry, body.competition, SourceCode.OPENFOOTBALL, body.season)
    return _start(engine, background, SourceCode.OPENFOOTBALL,
                  f"api:openfootball:{body.competition}:{body.season}{':full' if body.full else ''}",
                  lambda run_id: pipeline.openfootball(engine, store(), registry, body.competition, body.season,
                                                       body.full, body.reparse, run_id))


@router.post("/ingest/football-data-org", status_code=status.HTTP_202_ACCEPTED, response_model=JobAccepted)
def ingest_football_data_org(body: SeasonIngest, _: OwnerDep, background: BackgroundTasks, engine: EngineDep,
                             registry: RegistryDep, store: RawStoreFactoryDep, settings: SettingsDep) -> JobAccepted:
    """football-data.org season: kickoff, FT and HT (no goal minutes). Free tier: current + 3 seasons."""
    _competition(registry, body.competition, SourceCode.FOOTBALL_DATA_ORG, body.season)
    key = _key(settings.football_data_org_key.get_secret_value(), "FOOTBALL_DATA_ORG_KEY")
    return _start(engine, background, SourceCode.FOOTBALL_DATA_ORG,
                  f"api:football_data_org:{body.competition}:{body.season}",
                  lambda run_id: pipeline.football_data_org(engine, store(), registry, key, body.competition,
                                                            body.season, body.reparse, run_id))


@router.post("/ingest/football-data-co-uk", status_code=status.HTTP_202_ACCEPTED, response_model=JobAccepted)
def ingest_football_data_co_uk(body: SeasonIngest, _: OwnerDep, background: BackgroundTasks, engine: EngineDep,
                               registry: RegistryDep, store: RawStoreFactoryDep) -> JobAccepted:
    """football-data.co.uk season CSV: FT, HT and Pinnacle closing odds."""
    _competition(registry, body.competition, SourceCode.FOOTBALL_DATA_CO_UK, body.season)
    return _start(engine, background, SourceCode.FOOTBALL_DATA_CO_UK,
                  f"api:football_data_co_uk:{body.competition}:{body.season}",
                  lambda run_id: pipeline.football_data_co_uk(engine, store(), registry, body.competition,
                                                              body.season, body.reparse, run_id))


@router.post("/ingest/api-football-season", status_code=status.HTTP_202_ACCEPTED, response_model=JobAccepted)
def ingest_api_football_season(body: SeasonIngest, _: OwnerDep, background: BackgroundTasks, engine: EngineDep,
                               registry: RegistryDep, store: RawStoreFactoryDep,
                               settings: SettingsDep) -> JobAccepted:
    """API-Football league season (1 request). Free plan: seasons 2022-2024 only."""
    _competition(registry, body.competition, SourceCode.API_FOOTBALL, body.season)
    key = _key(settings.api_football_key.get_secret_value(), "API_FOOTBALL_KEY")
    return _start(engine, background, SourceCode.API_FOOTBALL,
                  f"api:api_football_season:{body.competition}:{body.season}",
                  lambda run_id: pipeline.api_football_season(engine, store(), registry, key, body.competition,
                                                              body.season, body.reparse, run_id))


@router.post("/ingest/api-football-day", status_code=status.HTTP_202_ACCEPTED, response_model=JobAccepted)
def ingest_api_football_day(body: ApiFootballDayIngest, _: OwnerDep, background: BackgroundTasks,
                            engine: EngineDep, registry: RegistryDep, store: RawStoreFactoryDep,
                            settings: SettingsDep) -> JobAccepted:
    """Fixtures of one day for all registered competitions, then goal minutes for finished matches and
    odds (incl. the 15' markets) for upcoming ones, each within its request budget.
    Free plan: only yesterday, today and tomorrow."""
    key = _key(settings.api_football_key.get_secret_value(), "API_FOOTBALL_KEY")
    return _start(engine, background, SourceCode.API_FOOTBALL, f"api:api_football_day:{body.date.isoformat()}",
                  lambda run_id: pipeline.api_football_day(engine, store(), registry, key, body.date,
                                                           body.max_event_requests, body.max_odds_requests,
                                                           body.reparse, run_id))


# --- run status --------------------------------------------------------------------------------


@router.get("/runs", response_model=list[RunOut])
def list_runs(
    _: OwnerDep,
    engine: EngineDep,
    source: SourceCode | None = None,
    run_status: Annotated[RunStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[RunOut]:
    """Latest ingestion runs (API, command line and scheduler), newest first."""
    with engine.connect() as conn:
        return [RunOut(**r) for r in raw_repo.list_runs(conn, source, run_status, limit)]


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: UUID, _: OwnerDep, engine: EngineDep) -> RunOut:
    """One run: status, requests used, error and statistics (matches created, teams sent to review, ...)."""
    with engine.connect() as conn:
        row = raw_repo.get_run(conn, run_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return RunOut(**row)
