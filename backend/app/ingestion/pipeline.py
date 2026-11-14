"""Ingestion jobs: fetch -> raw file + raw.payloads -> parse -> load (one transaction per payload).

Idempotent: a payload whose SHA-256 is already stored is not parsed again (unless reparse=True).
Every job writes a raw.ingestion_runs row (RUNNING -> SUCCEEDED / FAILED).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from datetime import date
from uuid import UUID

from sqlalchemy import Engine

from app.domain.enums import FixtureStatus, RunStatus, SourceCode
from app.domain.registry import Registry, fdcouk_season_code, season_start_year
from app.ingestion.api_football import ApiFootball
from app.ingestion.base import Fetched
from app.ingestion.football_data_co_uk import FootballDataCoUk
from app.ingestion.football_data_org import FootballDataOrg
from app.ingestion.loader import Loader
from app.ingestion.openfootball import OpenFootball
from app.normalisation.parsers import ParseResult
from app.normalisation.parsers import api_football as p_af
from app.normalisation.parsers import football_data_co_uk as p_uk
from app.normalisation.parsers import football_data_org as p_fd
from app.normalisation.parsers import openfootball as p_of
from app.storage.raw_store import RawStore, object_path, sha256
from app.storage.repositories import raw as raw_repo

PARSER_VERSIONS = {
    SourceCode.API_FOOTBALL: "api_football/1",
    SourceCode.FOOTBALL_DATA_ORG: "football_data_org/1",
    SourceCode.OPENFOOTBALL: "openfootball/1",
    SourceCode.FOOTBALL_DATA_CO_UK: "football_data_co_uk/1",
}

Parse = Callable[[bytes], ParseResult]


class Job:
    """One ingestion run for one source."""

    def __init__(self, engine: Engine, store: RawStore, registry: Registry, source: SourceCode, name: str,
                 reparse: bool = False) -> None:
        self.engine, self.store, self.registry, self.source = engine, store, registry, source
        self.reparse = reparse
        self.stats: Counter[str] = Counter()
        with engine.begin() as conn:
            self.run_id: UUID = raw_repo.start_run(conn, source, name)

    def process(self, fetched: Fetched, parse: Parse) -> ParseResult | None:
        digest = sha256(fetched.body)
        path = object_path(self.source, fetched.fetched_at, digest, fetched.ext)
        self.store.put(path, fetched.body)
        with self.engine.begin() as conn:
            payload = raw_repo.insert_payload(
                conn, run_id=self.run_id, source=self.source, endpoint=fetched.endpoint, params=fetched.params,
                http_status=fetched.http_status, storage_path=path, digest=digest, byte_size=len(fetched.body),
                fetched_at=fetched.fetched_at)
            self.stats["payloads"] += 1
            if not payload.is_new and not self.reparse:
                self.stats["payloads_unchanged_skipped"] += 1
                return None
            parsed = parse(fetched.body)
            loaded = Loader(conn, self.registry).load(parsed, payload.id, PARSER_VERSIONS[self.source])
            self.stats.update(loaded.counts)
            return parsed

    def finish(self, requests_used: int, error: BaseException | None = None) -> Counter[str]:
        with self.engine.begin() as conn:
            raw_repo.finish_run(conn, self.run_id, RunStatus.FAILED if error else RunStatus.SUCCEEDED,
                                requests_used, self.stats["source_records"], repr(error) if error else None)
        return self.stats


def _run(job: Job, client_requests: Callable[[], int], body: Callable[[], None]) -> Counter[str]:
    try:
        body()
    except BaseException as exc:
        job.finish(client_requests(), exc)
        raise
    return job.finish(client_requests())


# --- jobs per source -----------------------------------------------------------------------


def openfootball(engine: Engine, store: RawStore, registry: Registry, comp_code: str, season: str,
                 full: bool, reparse: bool = False) -> Counter[str]:
    comp = registry.get(comp_code)
    key = comp.providers[SourceCode.OPENFOOTBALL]
    client = OpenFootball()
    job = Job(engine, store, registry, SourceCode.OPENFOOTBALL, f"openfootball:{comp_code}:{season}", reparse)
    return _run(job, lambda: client.requests_used, lambda: job.process(
        client.season_file(season, key, full), lambda raw: p_of.parse_season_file(raw, comp, season, full)))


def football_data_org(engine: Engine, store: RawStore, registry: Registry, api_key: str, comp_code: str,
                      season: str, reparse: bool = False) -> Counter[str]:
    code = registry.get(comp_code).providers[SourceCode.FOOTBALL_DATA_ORG]
    client = FootballDataOrg(api_key)
    job = Job(engine, store, registry, SourceCode.FOOTBALL_DATA_ORG, f"football_data_org:{comp_code}:{season}", reparse)
    return _run(job, lambda: client.requests_used, lambda: job.process(
        client.competition_matches(code, season_start_year(season)), lambda raw: p_fd.parse_matches(raw, registry)))


def football_data_co_uk(engine: Engine, store: RawStore, registry: Registry, comp_code: str, season: str,
                        reparse: bool = False) -> Counter[str]:
    div = registry.get(comp_code).providers[SourceCode.FOOTBALL_DATA_CO_UK]
    client = FootballDataCoUk()
    job = Job(engine, store, registry, SourceCode.FOOTBALL_DATA_CO_UK, f"football_data_co_uk:{comp_code}:{season}",
              reparse)
    return _run(job, lambda: client.requests_used, lambda: job.process(
        client.division_csv(fdcouk_season_code(season), div), lambda raw: p_uk.parse_csv(raw, registry, season)))


def api_football_season(engine: Engine, store: RawStore, registry: Registry, api_key: str, comp_code: str,
                        season: str, reparse: bool = False) -> Counter[str]:
    league = registry.get(comp_code).providers[SourceCode.API_FOOTBALL]
    client = ApiFootball(api_key)
    job = Job(engine, store, registry, SourceCode.API_FOOTBALL, f"api_football_season:{comp_code}:{season}", reparse)
    return _run(job, lambda: client.requests_used, lambda: job.process(
        client.fixtures_by_league_season(league, season_start_year(season)),
        lambda raw: p_af.parse_fixtures(raw, registry)))


def api_football_day(engine: Engine, store: RawStore, registry: Registry, api_key: str, day: date,
                     max_event_requests: int = 20, max_odds_requests: int = 20,
                     reparse: bool = False) -> Counter[str]:
    """Fixtures of the day (all registered competitions), then goal minutes for finished registered
    matches and odds (incl. 15' markets) for upcoming ones - each within its request budget."""
    client = ApiFootball(api_key)
    job = Job(engine, store, registry, SourceCode.API_FOOTBALL, f"api_football_day:{day.isoformat()}", reparse)

    def body() -> None:
        parsed = job.process(client.fixtures_by_date(day), lambda raw: p_af.parse_fixtures(raw, registry))
        if parsed is None:
            return
        finished = [m for m in parsed.matches if m.status is FixtureStatus.FINISHED]
        upcoming = [m for m in parsed.matches if m.status is FixtureStatus.SCHEDULED]
        job.stats["registered_finished"] += len(finished)
        job.stats["registered_upcoming"] += len(upcoming)
        for m in finished[:max_event_requests]:
            job.process(client.fixture_with_events(m.provider_match_key), lambda raw: p_af.parse_fixtures(raw, registry))
        for m in upcoming[:max_odds_requests]:
            job.process(client.http.get("/odds", {"fixture": m.provider_match_key}),
                        lambda raw: p_af.parse_odds(raw, registry))

    return _run(job, lambda: client.requests_used, body)


def format_stats(stats: Counter[str]) -> str:
    return json.dumps(dict(sorted(stats.items())), indent=2)
