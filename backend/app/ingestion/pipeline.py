"""Ingestion jobs: fetch -> raw file + raw.payloads -> parse -> load (one transaction per payload).

Idempotent: a payload whose SHA-256 is already stored is not parsed again (unless reparse=True).
Every job writes a raw.ingestion_runs row (RUNNING -> SUCCEEDED / FAILED). When it ends - successfully or not -
every competition-season it wrote to is cross-checked and stored, and the run is audited (OK / WARNING /
ERROR with findings, docs/DATA-HEALTH.md). API-Football jobs first check the day's quota (/status, not counted)
and plan the run inside it.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import Engine

from app.core.settings import BACKEND_DIR, get_settings
from app.domain.enums import FixtureStatus, RunStatus, SnapshotType, SourceCode
from app.domain.params import load_params
from app.domain.quality_config import load_quality_config
from app.domain.registry import Registry, fdcouk_season_code, season_start_year
from app.ingestion.api_football import BULK_IDS_MAX, ApiFootball
from app.ingestion.audit import audit_run
from app.ingestion.base import Fetched
from app.ingestion.football_data_co_uk import FootballDataCoUk
from app.ingestion.football_data_org import FootballDataOrg
from app.ingestion.loader import Loader
from app.ingestion.openfootball import OpenFootball
from app.ingestion.quality import crosscheck_season
from app.normalisation.parsers import ParseResult
from app.normalisation.parsers import api_football as p_af
from app.normalisation.parsers import football_data_co_uk as p_uk
from app.normalisation.parsers import football_data_org as p_fd
from app.normalisation.parsers import openfootball as p_of
from app.storage.raw_store import RawStore, object_path, sha256
from app.storage.repositories import health as health_repo
from app.storage.repositories import matches as matches_repo
from app.storage.repositories import ops as ops_repo
from app.storage.repositories import raw as raw_repo

PARSER_VERSIONS = {
    SourceCode.API_FOOTBALL: "api_football/1",
    SourceCode.FOOTBALL_DATA_ORG: "football_data_org/1",
    SourceCode.OPENFOOTBALL: "openfootball/1",
    SourceCode.FOOTBALL_DATA_CO_UK: "football_data_co_uk/1",
}

Parse = Callable[[bytes], ParseResult]
log = logging.getLogger(__name__)


class QuotaError(RuntimeError):
    """No API request left for this run today (after the configured reserve)."""


class PlanError(RuntimeError):
    """The run needs a paid API-Football plan but the key is on the free plan (checked before any counted request)."""


class ConcurrentRunError(RuntimeError):
    """Another process is running a job of the same source (they would share one quota)."""


def _exclusive(engine: Engine, source: SourceCode, body: Callable[[], None]) -> Callable[[], None]:
    """Run `body` holding the source's database lock: one job per source across ALL processes (API, worker,
    command line). The in-process lock of ingestion/runner.py only covers the API process."""
    def run() -> None:
        with ops_repo.exclusive(engine, f"ingest:{source.value}") as got:
            if not got:
                raise ConcurrentRunError(f"another {source.value} job is running in another process")
            body()
    return run


class Job:
    """One ingestion run for one source."""

    def __init__(self, engine: Engine, store: RawStore, registry: Registry, source: SourceCode, name: str,
                 reparse: bool = False, run_id: UUID | None = None) -> None:
        """`run_id`: an already created raw.ingestion_runs row (the API creates it so it can answer at once)."""
        self.engine, self.store, self.registry, self.source = engine, store, registry, source
        self.reparse = reparse
        self.stats: Counter[str] = Counter()
        # the 15' cutoff comes from the active params file (single source of parameter values)
        self.cutoff_minute = load_params(BACKEND_DIR / get_settings().params_file).settlement.cutoff_label_minute
        self.quality = load_quality_config()
        self.seasons_touched: set[UUID] = set()
        if run_id is None:
            with engine.begin() as conn:
                run_id = raw_repo.start_run(conn, source, name)
        self.run_id: UUID = run_id

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
            loader = Loader(conn, self.registry, self.cutoff_minute)
            loaded = loader.load(parsed, payload.id, PARSER_VERSIONS[self.source])
            self.stats.update(loaded.counts)
            self.seasons_touched |= loaded.seasons
            return parsed

    def _crosscheck_touched(self) -> None:
        """Cross-check every competition-season this run wrote to; each check in its own transaction."""
        for season in sorted(self.seasons_touched):
            try:
                with self.engine.begin() as conn:
                    stored = crosscheck_season(conn, season, self.cutoff_minute, self.quality.crosscheck, self.run_id)
                self.stats[f"crosscheck:{stored.verdict.status.value}"] += 1
            except Exception:  # a failed check is reported by the audit; it must not hide the run outcome
                log.exception("cross-check of competition-season %s failed", season)
                self.stats["crosscheck_errors"] += 1

    def finish(self, requests_used: int, error: BaseException | None = None) -> Counter[str]:
        self._crosscheck_touched()
        message = repr(error) if error else None
        audit_status, findings = audit_run(self.stats, message, self.quality)
        with self.engine.begin() as conn:
            raw_repo.finish_run(conn, self.run_id, RunStatus.FAILED if error else RunStatus.SUCCEEDED,
                                requests_used, self.stats["source_records"], message, dict(self.stats))
            health_repo.set_run_audit(conn, self.run_id, audit_status.value, [f.as_dict() for f in findings])
        return self.stats


def _run(job: Job, client_requests: Callable[[], int], body: Callable[[], object],
         after: Callable[[], None] | None = None) -> Counter[str]:
    """`after`: adds closing statistics (quota, retries); it runs even when the body failed."""
    error: BaseException | None = None
    try:
        body()
    except BaseException as exc:
        error = exc
    if after is not None:
        try:
            after()
        except Exception:
            log.exception("closing statistics of run %s failed", job.run_id)
    job.finish(client_requests(), error)
    if error is not None:
        raise error
    return job.stats


def _plan_quota(client: ApiFootball, job: Job, wanted: int, *, minimum: int) -> None:
    """Fit this run's request budget into what is left today, minus the reserve. Fail visibly, before any request,
    if less than `minimum` is left: the fewest requests with which the run achieves anything (a backfill needs
    the season listing AND at least one fixture - the listing alone would be a wasted request)."""
    used, limit = client.quota()
    reserve = job.quality.api_quota.reserve_requests
    job.stats["quota_limit"], job.stats["quota_used_before"] = limit, used
    budget = min(wanted, limit - used - reserve)
    if budget < minimum:
        raise QuotaError(f"API-Football quota: {used}/{limit} requests used today, reserve {reserve}: "
                         f"{max(budget, 0)} left, this run needs at least {minimum}")
    client.http.max_requests = budget
    job.stats["request_budget"] = budget


def _close_api_football(client: ApiFootball, job: Job) -> Callable[[], None]:
    def after() -> None:
        job.stats["rate_limit_retries"] = client.rate_limit_retries
        if "quota_limit" in job.stats:
            used, limit = client.quota()
            job.stats["quota_used_after"], job.stats["quota_remaining_after"] = used, max(limit - used, 0)
    return after


# --- jobs per source -----------------------------------------------------------------------


def openfootball(engine: Engine, store: RawStore, registry: Registry, comp_code: str, season: str,
                 full: bool, reparse: bool = False, run_id: UUID | None = None) -> Counter[str]:
    comp = registry.get(comp_code)
    key = comp.providers[SourceCode.OPENFOOTBALL]
    client = OpenFootball()
    job = Job(engine, store, registry, SourceCode.OPENFOOTBALL, f"openfootball:{comp_code}:{season}",
              reparse, run_id)
    return _run(job, lambda: client.requests_used, lambda: job.process(
        client.season_file(season, key, full), lambda raw: p_of.parse_season_file(raw, comp, season, full)))


def football_data_org(engine: Engine, store: RawStore, registry: Registry, api_key: str, comp_code: str,
                      season: str, reparse: bool = False, run_id: UUID | None = None) -> Counter[str]:
    code = registry.get(comp_code).providers[SourceCode.FOOTBALL_DATA_ORG]
    client = FootballDataOrg(api_key)
    job = Job(engine, store, registry, SourceCode.FOOTBALL_DATA_ORG, f"football_data_org:{comp_code}:{season}",
              reparse, run_id)
    return _run(job, lambda: client.requests_used, lambda: job.process(
        client.competition_matches(code, season_start_year(season)), lambda raw: p_fd.parse_matches(raw, registry)))


def football_data_co_uk(engine: Engine, store: RawStore, registry: Registry, comp_code: str, season: str,
                        reparse: bool = False, run_id: UUID | None = None) -> Counter[str]:
    div = registry.get(comp_code).providers[SourceCode.FOOTBALL_DATA_CO_UK]
    client = FootballDataCoUk()
    job = Job(engine, store, registry, SourceCode.FOOTBALL_DATA_CO_UK, f"football_data_co_uk:{comp_code}:{season}",
              reparse, run_id)
    return _run(job, lambda: client.requests_used, lambda: job.process(
        client.division_csv(fdcouk_season_code(season), div), lambda raw: p_uk.parse_csv(raw, registry, season)))


def api_football_season(engine: Engine, store: RawStore, registry: Registry, api_key: str, comp_code: str,
                        season: str, reparse: bool = False, run_id: UUID | None = None, *,
                        requests_per_minute: int) -> Counter[str]:
    league = registry.get(comp_code).providers[SourceCode.API_FOOTBALL]
    client = ApiFootball(api_key, max_requests=1, requests_per_minute=requests_per_minute)
    job = Job(engine, store, registry, SourceCode.API_FOOTBALL, f"api_football_season:{comp_code}:{season}",
              reparse, run_id)

    def body() -> None:
        _plan_quota(client, job, 1, minimum=1)
        job.process(client.fixtures_by_league_season(league, season_start_year(season)),
                    lambda raw: p_af.parse_fixtures(raw, registry))

    return _run(job, lambda: client.requests_used, _exclusive(engine, SourceCode.API_FOOTBALL, body),
                _close_api_football(client, job))


def api_football_day(engine: Engine, store: RawStore, registry: Registry, api_key: str, day: date,
                     max_event_requests: int = 20, max_odds_requests: int = 20,
                     reparse: bool = False, run_id: UUID | None = None, *,
                     requests_per_minute: int) -> Counter[str]:
    """Fixtures of the day (all registered competitions), then goal minutes for finished registered
    matches and odds (incl. 15' markets) for upcoming ones - each within its request budget."""
    client = ApiFootball(api_key, max_requests=1 + max_event_requests + max_odds_requests,
                         requests_per_minute=requests_per_minute)
    job = Job(engine, store, registry, SourceCode.API_FOOTBALL, f"api_football_day:{day.isoformat()}",
              reparse, run_id)

    def body() -> None:
        _plan_quota(client, job, 1 + max_event_requests + max_odds_requests, minimum=1)
        parsed = job.process(client.fixtures_by_date(day), lambda raw: p_af.parse_fixtures(raw, registry))
        if parsed is None:
            return
        finished = [m for m in parsed.matches if m.status is FixtureStatus.FINISHED]
        upcoming = [m for m in parsed.matches if m.status is FixtureStatus.SCHEDULED]
        job.stats["registered_finished"] += len(finished)
        job.stats["registered_upcoming"] += len(upcoming)
        budget = client.http.max_requests or 0
        for m in finished[:max_event_requests]:
            if client.requests_used >= budget:
                job.stats["stopped_at_request_budget"] = 1
                break
            job.process(client.fixture_with_events(m.provider_match_key),
                        lambda raw: p_af.parse_fixtures(raw, registry))
        for m in upcoming[:max_odds_requests]:
            if client.requests_used >= budget:
                job.stats["stopped_at_request_budget"] = 1
                break
            job.process(client.odds_for_fixture(m.provider_match_key),
                        lambda raw: p_af.parse_odds(raw, registry))

    return _run(job, lambda: client.requests_used, _exclusive(engine, SourceCode.API_FOOTBALL, body),
                _close_api_football(client, job))


def api_football_session_odds(engine: Engine, store: RawStore, registry: Registry, api_key: str,
                              start_utc: datetime, end_utc: datetime, competitions: Sequence[str],
                              max_requests: int, run_id: UUID | None = None, *,
                              requests_per_minute: int, job_name: str | None = None) -> Counter[str]:
    """Paper betting capture (docs/PAPER-BETTING.md §5): the fixtures of the UTC dates the window covers (all
    leagues in one request per date; this also gives the API-Football ids of fixtures loaded from other sources,
    and the results of matches already played), then the odds - 15' markets and main markets - of every
    SCHEDULED fixture of `competitions` kicking off in [start_utc, end_utc), within `max_requests`."""
    if start_utc.utcoffset() is None or end_utc.utcoffset() is None or end_utc <= start_utc:
        raise ValueError("timezone-aware window with end after start required")
    days = sorted({start_utc.astimezone(UTC).date(), (end_utc - timedelta(microseconds=1)).astimezone(UTC).date()})
    wanted = set(competitions)
    client = ApiFootball(api_key, max_requests=max_requests, requests_per_minute=requests_per_minute)
    job = Job(engine, store, registry, SourceCode.API_FOOTBALL,
              job_name or f"api_football_session_odds:{start_utc:%Y-%m-%dT%H:%M}", False, run_id)

    def body() -> None:
        _plan_quota(client, job, max_requests, minimum=len(days) + 1)
        budget = client.http.max_requests or 0
        upcoming: list[str] = []
        for day in days:
            listing = client.fixtures_by_date(day)
            parsed = p_af.parse_fixtures(listing.body, registry)  # parsed here too: needed even if the payload is old
            job.process(listing, lambda raw: parsed)  # noqa: B023 - used at once, inside this iteration
            upcoming += [m.provider_match_key for m in parsed.matches
                         if m.status is FixtureStatus.SCHEDULED and m.competition_code in wanted
                         and m.kickoff_utc is not None and start_utc <= m.kickoff_utc < end_utc]
        job.stats["session_fixtures"] = len(upcoming)
        for key in upcoming:
            if client.requests_used >= budget:
                job.stats["stopped_at_request_budget"] = 1
                job.stats["fixtures_without_odds_request"] += 1
                continue
            job.process(client.odds_for_fixture(key), lambda raw: p_af.parse_odds(raw, registry))
            job.stats["odds_requests"] += 1

    return _run(job, lambda: client.requests_used, _exclusive(engine, SourceCode.API_FOOTBALL, body),
                _close_api_football(client, job))


def api_football_fixture_dates(engine: Engine, store: RawStore, registry: Registry, api_key: str,
                               days: Sequence[date], run_id: UUID | None = None, *, requests_per_minute: int,
                               job_name: str) -> Counter[str]:
    """Session planning (ARCHITECTURE.md 12.4): the fixtures of the given UTC dates, all leagues in one request per
    date - new fixtures, kick-off changes and postponements - and nothing else (no odds, no goal minutes)."""
    wanted = sorted(set(days))
    client = ApiFootball(api_key, max_requests=len(wanted), requests_per_minute=requests_per_minute)
    job = Job(engine, store, registry, SourceCode.API_FOOTBALL, job_name, False, run_id)

    def body() -> None:
        _plan_quota(client, job, len(wanted), minimum=len(wanted))  # a half-listed session is no plan
        for day in wanted:
            listing = client.fixtures_by_date(day)
            parsed = p_af.parse_fixtures(listing.body, registry)
            job.process(listing, lambda raw: parsed)  # noqa: B023 - used at once, inside this iteration
            job.stats["listed_fixtures"] += len(parsed.matches)

    return _run(job, lambda: client.requests_used, _exclusive(engine, SourceCode.API_FOOTBALL, body),
                _close_api_football(client, job))


def api_football_results(engine: Engine, store: RawStore, registry: Registry, api_key: str,
                         fixture_keys: Sequence[str], bulk: bool, max_requests: int, run_id: UUID | None = None, *,
                         requests_per_minute: int, job_name: str) -> Counter[str]:
    """Paper betting results (docs/PAPER-BETTING.md 8): the fixtures incl. events (goal minutes) of the given
    API-Football fixture ids - the frozen fixtures still waiting for their 15' result - 20 per request with `bulk`
    (paid plans), otherwise 1 per request, within `max_requests`."""
    client = ApiFootball(api_key, max_requests=max_requests, requests_per_minute=requests_per_minute)
    job = Job(engine, store, registry, SourceCode.API_FOOTBALL, job_name, False, run_id)
    keys = list(dict.fromkeys(fixture_keys))

    def body() -> None:
        _plan_quota(client, job, max_requests, minimum=1)
        actual = getattr(client, "subscription_plan", None)
        if bulk and actual is not None and actual.strip().lower() == "free":
            raise PlanError("API_FOOTBALL_PLAN=pro but the key is on the Free plan (/status)")
        budget = client.http.max_requests or 0
        size = BULK_IDS_MAX if bulk else 1
        requested = 0
        for i in range(0, len(keys), size):
            if client.requests_used >= budget:
                job.stats["stopped_at_request_budget"] = 1
                break
            chunk = keys[i:i + size]
            fetched = client.fixtures_by_ids(chunk) if bulk else client.fixture_with_events(chunk[0])
            job.process(fetched, lambda raw: p_af.parse_fixtures(raw, registry))
            requested += len(chunk)
        job.stats["fixtures_requested"] += requested
        job.stats["fixtures_not_requested"] += len(keys) - requested

    return _run(job, lambda: client.requests_used, _exclusive(engine, SourceCode.API_FOOTBALL, body),
                _close_api_football(client, job))


def api_football_recheck(engine: Engine, store: RawStore, registry: Registry, api_key: str,
                         fixture_keys: Sequence[str], max_requests: int, run_id: UUID | None = None, *,
                         requests_per_minute: int, job_name: str) -> Counter[str]:
    """Paper betting re-check (docs/PAPER-BETTING.md 10): for each fixture its announced lineups and its current
    odds (15' and main markets), 2 requests per fixture, within `max_requests`."""
    client = ApiFootball(api_key, max_requests=max_requests, requests_per_minute=requests_per_minute)
    job = Job(engine, store, registry, SourceCode.API_FOOTBALL, job_name, False, run_id)
    keys = list(dict.fromkeys(fixture_keys))

    def body() -> None:
        _plan_quota(client, job, max_requests, minimum=2)
        budget = client.http.max_requests or 0
        for key in keys:
            if client.requests_used + 2 > budget:
                job.stats["stopped_at_request_budget"] = 1
                job.stats["fixtures_not_requested"] += 1
                continue
            lineups = client.lineups(key)
            parsed = p_af.parse_lineups(lineups.body, key)
            job.process(lineups, lambda raw: parsed)  # noqa: B023 - used at once, inside this iteration
            job.stats["lineups_announced"] += len(parsed.lineups)
            job.process(client.odds_for_fixture(key), lambda raw: p_af.parse_odds(raw, registry))
            job.stats["fixtures_requested"] += 1

    return _run(job, lambda: client.requests_used, _exclusive(engine, SourceCode.API_FOOTBALL, body),
                _close_api_football(client, job))


def api_football_closing(engine: Engine, store: RawStore, registry: Registry, api_key: str,
                         fixture_keys: Sequence[str], max_requests: int, run_id: UUID | None = None, *,
                         requests_per_minute: int, job_name: str) -> Counter[str]:
    """Closing odds (docs/PAPER-BETTING.md 11): the odds of each fixture just before kick-off, stored as CLOSING
    snapshots - the closing line against which CLV is measured. 1 request per fixture, within `max_requests`."""
    client = ApiFootball(api_key, max_requests=max_requests, requests_per_minute=requests_per_minute)
    job = Job(engine, store, registry, SourceCode.API_FOOTBALL, job_name, False, run_id)
    keys = list(dict.fromkeys(fixture_keys))

    def body() -> None:
        _plan_quota(client, job, max_requests, minimum=1)
        budget = client.http.max_requests or 0
        for key in keys:
            if client.requests_used >= budget:
                job.stats["stopped_at_request_budget"] = 1
                job.stats["fixtures_not_requested"] += 1
                continue
            job.process(client.odds_for_fixture(key),
                        lambda raw: p_af.parse_odds(raw, registry, SnapshotType.CLOSING))
            job.stats["fixtures_requested"] += 1

    return _run(job, lambda: client.requests_used, _exclusive(engine, SourceCode.API_FOOTBALL, body),
                _close_api_football(client, job))


def api_football_backfill(engine: Engine, store: RawStore, registry: Registry, api_key: str, comp_code: str,
                          season: str, bulk: bool, max_requests: int, reparse: bool = False,
                          run_id: UUID | None = None, *, requests_per_minute: int,
                          skip_known_15: bool) -> Counter[str]:
    """Season fixtures (1 request), then goal minutes for every finished fixture that has none from
    API-Football yet: 20 fixtures per request with `bulk` (paid plans), otherwise 1 per request.

    Order: fixtures whose 15' result is still UNKNOWN first; fixtures whose 15' result is already known (e.g.
    half-time 0-0, or minutes from another source) last - for them the request only adds a second source for
    the cross-check. `skip_known_15`: do not fetch those at all (when no second goal-minute source exists for
    the season, they would add nothing).
    Resumable: fixtures that already have API-Football goal minutes are skipped, so repeated runs (e.g. one
    per day on the free plan) continue where the last one stopped. Stops cleanly at the request budget:
    `max_requests`, reduced to what the quota of the day still allows."""
    league = registry.get(comp_code).providers[SourceCode.API_FOOTBALL]
    client = ApiFootball(api_key, max_requests=max_requests, requests_per_minute=requests_per_minute)
    job = Job(engine, store, registry, SourceCode.API_FOOTBALL, f"api_football_backfill:{comp_code}:{season}",
              reparse, run_id)

    def body() -> None:
        _plan_quota(client, job, max_requests, minimum=2)
        actual = getattr(client, "subscription_plan", None)
        if bulk and actual is not None and actual.strip().lower() == "free":
            raise PlanError("API_FOOTBALL_PLAN=pro but the key is on the Free plan (/status): set "
                            "API_FOOTBALL_PLAN=free, or finish the upgrade on the API-Football dashboard")
        budget = client.http.max_requests or 0
        listing = client.fixtures_by_league_season(league, season_start_year(season))
        parsed = p_af.parse_fixtures(listing.body, registry)  # parsed here too: needed even if the payload is old
        job.process(listing, lambda raw: parsed)
        finished = [m.provider_match_key for m in parsed.matches if m.status is FixtureStatus.FINISHED]
        with engine.connect() as conn:
            done = matches_repo.provider_keys_with_minutes(conn, SourceCode.API_FOOTBALL, finished)
            known = matches_repo.provider_keys_with_known_15(conn, SourceCode.API_FOOTBALL, finished)
        missing = [k for k in finished if k not in done]
        todo = [k for k in missing if k not in known] + ([] if skip_known_15 else [k for k in missing if k in known])
        job.stats["finished_fixtures"] += len(finished)
        job.stats["already_with_minutes"] += len(done)
        job.stats["todo_unknown_15"] += sum(k not in known for k in missing)
        job.stats["todo_known_15_crosscheck_only"] += 0 if skip_known_15 else sum(k in known for k in missing)
        job.stats["skipped_known_15"] += sum(k in known for k in missing) if skip_known_15 else 0
        size = BULK_IDS_MAX if bulk else 1
        requested = 0
        for i in range(0, len(todo), size):
            if client.requests_used >= budget:
                job.stats["stopped_at_request_budget"] = 1
                break
            chunk = todo[i:i + size]
            fetched = client.fixtures_by_ids(chunk) if bulk else client.fixture_with_events(chunk[0])
            job.process(fetched, lambda raw: p_af.parse_fixtures(raw, registry))
            requested += len(chunk)
        job.stats["fixtures_requested_for_minutes"] += requested
        job.stats["fixtures_still_without_minutes"] += len(todo) - requested

    return _run(job, lambda: client.requests_used, _exclusive(engine, SourceCode.API_FOOTBALL, body),
                _close_api_football(client, job))


def format_stats(stats: Counter[str]) -> str:
    return json.dumps(dict(sorted(stats.items())), indent=2)
