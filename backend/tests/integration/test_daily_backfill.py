"""Daily backfill: targets in order, finished targets cost nothing, the daily budget, the quota, the cross-process
lock, config validation and the schedule. Fake API (no network) with ONE quota counter shared by all runs."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from pydantic import SecretStr, ValidationError
from sqlalchemy import Engine, create_engine

from app.core.settings import Settings, get_settings
from app.domain.enums import SourceCode
from app.domain.registry import CompetitionDef, Registry
from app.ingestion import pipeline
from app.ingestion.base import Fetched
from app.jobs import backfill as backfill_module
from app.jobs.backfill import BackfillConfig, Mode, TargetState, plan, run_daily_backfill
from app.jobs.scheduler import IntervalJob, build_scheduler
from app.storage.raw_store import LocalRawStore
from app.storage.repositories import ops as ops_repo
from tests.integration.conftest import ADMIN_URL

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")
LEAGUES = {"TST1": "990", "TST2": "991"}
REG = Registry([CompetitionDef(code=c, name=c, country="Testland", type="LEAGUE", timezone="UTC",
                               season_format="SPLIT", fifteen_minute_market=True,
                               team_sources=(SourceCode.API_FOOTBALL,), providers={SourceCode.API_FOOTBALL: lid})
                for c, lid in LEAGUES.items()])
N = 10  # finished fixtures per league: even ones with an early goal (15' unknown), odd ones HT 0-0 (known)


def item(league: str, n: int, events: bool) -> dict[str, Any]:
    base = 100 * int(league)
    home, away = (base + n % 4, f"{league} Club {n % 4}"), (base + 4 + n % 3, f"{league} Club {4 + n % 3}")
    ko = datetime(2024, 8, 10, 15, tzinfo=UTC) + timedelta(days=n)
    minute = 12 if n % 2 == 0 else 70
    it: dict[str, Any] = {
        "fixture": {"id": base + n, "timezone": "UTC", "date": ko.isoformat(), "timestamp": int(ko.timestamp()),
                    "status": {"long": "Match Finished", "short": "FT", "elapsed": 90, "extra": None}},
        "league": {"id": int(league), "name": league, "country": "Testland", "season": 2024, "round": f"R{n}"},
        "teams": {"home": {"id": home[0], "name": home[1]}, "away": {"id": away[0], "name": away[1]}},
        "goals": {"home": 1, "away": 0},
        "score": {"halftime": {"home": 1 if minute <= 45 else 0, "away": 0}, "fulltime": {"home": 1, "away": 0}},
    }
    if events:
        it["events"] = [{"time": {"elapsed": minute, "extra": None}, "team": {"id": home[0], "name": home[1]},
                         "player": {"id": 1, "name": "S"}, "type": "Goal", "detail": "Normal Goal"}]
    return it


class Quota:
    def __init__(self, used: int = 0, limit: int = 100) -> None:
        self.used, self.limit, self.calls = used, limit, []
        self.plan: str | None = "Free"  # what /status reports for the key


class FakeHttp:
    max_requests: int | None = None


class FakeClient:
    """One per pipeline run, like the real client; the quota is shared (it is per day, not per run)."""

    def __init__(self, quota: Quota) -> None:
        self.q, self.requests_used, self.rate_limit_retries, self.http = quota, 0, 0, FakeHttp()
        self.subscription_plan: str | None = None

    def _f(self, params: dict[str, Any], items: list[dict[str, Any]]) -> Fetched:
        self.requests_used += 1
        self.q.used += 1
        self.q.calls.append(params)
        body = json.dumps({"errors": [], "results": len(items), "response": items}).encode()
        return Fetched(endpoint="/fixtures", params=params, http_status=200, body=body, ext="json")

    def quota(self) -> tuple[int, int]:
        self.subscription_plan = self.q.plan
        return self.q.used, self.q.limit

    def fixtures_by_ids(self, fixture_ids: list[str]) -> Fetched:
        return self._f({"ids": "-".join(fixture_ids)},
                       [item(str(divmod(int(i), 100)[0]), divmod(int(i), 100)[1], True) for i in fixture_ids])

    def fixtures_by_league_season(self, league_id: str, season: int) -> Fetched:
        return self._f({"league": league_id, "season": season}, [item(league_id, n, False) for n in range(N)])

    def fixture_with_events(self, fixture_id: str) -> Fetched:
        league, n = divmod(int(fixture_id), 100)
        return self._f({"id": fixture_id}, [item(str(league), n, True)])


@pytest.fixture
def engine(fresh_db_url: str) -> Iterator[Engine]:
    eng = create_engine(fresh_db_url.replace("postgresql://", "postgresql+psycopg://"))
    yield eng
    eng.dispose()


@pytest.fixture
def quota(monkeypatch: pytest.MonkeyPatch) -> Quota:
    q = Quota()
    monkeypatch.setattr(pipeline, "ApiFootball", lambda key, max_requests, requests_per_minute: FakeClient(q))
    return q


SETTINGS: Settings = get_settings().model_copy(update={
    "api_football_key": SecretStr("key"), "api_football_plan": "free", "api_football_max_requests_per_run": 90,
    "api_football_requests_per_minute": 10})
PRO: Settings = SETTINGS.model_copy(update={"api_football_plan": "pro"})


def config(daily: int, mode: Mode = Mode.HISTORY) -> BackfillConfig:
    return BackfillConfig.model_validate({
        "schedule": {"timezone": "UTC", "time": "00:30"}, "daily_max_requests": {"free": daily, "pro": daily},
        "targets": [{"competition": "TST1", "season": "2024-25", "mode": mode.value},
                    {"competition": "TST2", "season": "2024-25", "mode": mode.value}]})


def day(engine: Engine, tmp_path: Path, cfg: BackfillConfig, settings: Settings = SETTINGS) -> Any:
    return run_daily_backfill(engine, lambda: LocalRawStore(tmp_path), REG, settings, cfg)


@pytest.fixture
def separate_days(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests simulate several quota days within one real day: earlier runs are not 'today'."""
    monkeypatch.setattr(backfill_module.ops_repo, "requests_since", lambda *_a, **_k: 0)


def listed(q: Quota) -> list[str]:
    return [c["league"] for c in q.calls if "league" in c]


def test_targets_in_order_within_the_daily_budget(engine: Engine, tmp_path: Path, quota: Quota,
                                                  separate_days: None) -> None:
    first = day(engine, tmp_path, config(daily=8))
    # TST1: listing + its 5 unknown fixtures (history mode) -> finished; TST2: the 2 requests left
    assert first.requests_used == 8 and first.stopped == "request budget reached"
    assert [r["outcome"] for r in first.runs] == ["ok", "ok"] and first.runs[0]["fixtures_still_without_minutes"] == 0
    assert [s.state for s in plan(engine, config(daily=8))] == [TargetState.FINISHED, TargetState.IN_PROGRESS]

    quota.used, quota.calls = 0, []                                          # the next day: a fresh quota
    second = day(engine, tmp_path, config(daily=8))
    assert listed(quota) == ["991"]                                          # finished TST1 costs nothing
    assert second.requests_used == 1 + 4 and [r["competition"] for r in second.runs] == ["TST2"]
    assert all(s.state is TargetState.FINISHED for s in plan(engine, config(daily=8)))

    quota.used, quota.calls = 0, []
    third = day(engine, tmp_path, config(daily=8))
    assert quota.calls == [] and third.runs == [] and third.stopped == "all targets finished or attempted"


def test_crosscheck_mode_also_fetches_known_results(engine: Engine, tmp_path: Path, quota: Quota,
                                                     separate_days: None) -> None:
    day(engine, tmp_path, config(daily=100))                                 # history: unknown ones only
    assert all(s.state is TargetState.FINISHED for s in plan(engine, config(daily=100)))
    assert [s.state for s in plan(engine, config(daily=100, mode=Mode.CROSSCHECK))] == [TargetState.IN_PROGRESS] * 2
    quota.used = 0
    result = day(engine, tmp_path, config(daily=100, mode=Mode.CROSSCHECK))
    assert [r["fixtures_requested_for_minutes"] for r in result.runs] == [N // 2, N // 2]   # the known ones now
    assert all(s.state is TargetState.FINISHED for s in plan(engine, config(daily=100, mode=Mode.CROSSCHECK)))


def test_quota_used_up_stops_the_day_without_requests(engine: Engine, tmp_path: Path, quota: Quota) -> None:
    quota.used = 96                                                          # 4 left, reserve 5
    result = day(engine, tmp_path, config(daily=90))
    assert result.stopped == "quota used" and quota.calls == [] and result.runs[0]["outcome"].startswith("quota")


def test_another_process_running_stops_the_day(engine: Engine, tmp_path: Path, quota: Quota) -> None:
    with ops_repo.exclusive(engine, "ingest:API_FOOTBALL") as got:          # e.g. a backfill started from the API
        assert got
        result = day(engine, tmp_path, config(daily=90))
    assert result.stopped == "another API-Football job is running" and quota.calls == []
    with ops_repo.exclusive(engine, "ingest:API_FOOTBALL") as again:        # released afterwards
        assert again


def test_config_is_validated() -> None:
    base = config(daily=10).model_dump(mode="json")
    with pytest.raises(ValidationError, match="twice"):
        BackfillConfig.model_validate({**base, "targets": base["targets"] + base["targets"][:1]})
    with pytest.raises(ValidationError):
        BackfillConfig.model_validate({**base, "schedule": {"timezone": "UTC", "time": "25:00"}})
    with pytest.raises(ValidationError):
        BackfillConfig.model_validate({**base, "schedule": {"timezone": "Mars/Base", "time": "00:30"}})
    with pytest.raises(ValidationError):
        BackfillConfig.model_validate({**base, "unknown_key": 1})
    unknown = BackfillConfig.model_validate({**base, "targets": [{"competition": "XXX9", "season": "2024-25",
                                                                  "mode": "history"}]})
    with pytest.raises(ValueError, match="XXX9 is not in"):
        unknown.check_registry(REG)


def test_real_config_and_schedule(engine: Engine) -> None:
    from app.domain.registry import load_registry
    from app.jobs.backfill import load_backfill_config

    cfg = load_backfill_config()
    cfg.check_registry(load_registry())                                       # the shipped file is valid
    sched = build_scheduler(engine, lambda: LocalRawStore(Path(".")), load_registry(), SETTINGS, cfg,
                            BackgroundScheduler(timezone="UTC"))
    (job,) = sched.get_jobs()
    assert job.id == "daily_backfill" and job.coalesce and job.max_instances == 1
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert (fields["hour"], fields["minute"]) == tuple(str(x) for x in cfg.schedule.hour_minute)
    with_paper = build_scheduler(engine, lambda: LocalRawStore(Path(".")), load_registry(), SETTINGS, cfg,
                                 BackgroundScheduler(timezone="UTC"),
                                 interval_jobs=[IntervalJob("paper_cycle", "paper betting cycle", lambda: None, 5)])
    paper = with_paper.get_job("paper_cycle")
    assert paper is not None and paper.coalesce and paper.max_instances == 1 and paper.misfire_grace_time == 300


def test_current_season_mode_needs_pro_and_never_finishes(engine: Engine, tmp_path: Path, quota: Quota,
                                                           separate_days: None) -> None:
    cfg = config(daily=100, mode=Mode.CURRENT)
    free = day(engine, tmp_path, cfg)                                       # free plan: skipped, nothing spent
    assert quota.calls == [] and all(r["outcome"].startswith("skipped") and r["requests"] == 0 for r in free.runs)

    quota.plan = "Pro"
    first = day(engine, tmp_path, cfg, PRO)
    assert [r["outcome"] for r in first.runs] == ["ok", "ok"]
    assert any("ids" in c for c in quota.calls)                             # 20 fixtures per request on Pro
    assert all(r["fixtures_still_without_minutes"] == 0 for r in first.runs)
    assert [s.state for s in plan(engine, cfg)] == [TargetState.IN_PROGRESS] * 2   # never finished

    quota.calls = []
    second = day(engine, tmp_path, cfg, PRO)                                # the next night: listings again,
    assert listed(quota) == ["990", "991"] and second.requests_used == 2   # nothing new to fetch


def test_pro_setting_with_a_free_key_spends_nothing(engine: Engine, tmp_path: Path, quota: Quota) -> None:
    result = day(engine, tmp_path, config(daily=100), PRO)                  # /status still says Free
    assert result.stopped == "plan mismatch" and quota.calls == []
    assert result.runs[0]["outcome"].startswith("plan: API_FOOTBALL_PLAN=pro")


def test_the_daily_bound_counts_earlier_runs_of_the_same_day(engine: Engine, tmp_path: Path, quota: Quota) -> None:
    first = day(engine, tmp_path, config(daily=8))                         # e.g. a manual run
    assert first.requests_used == 8
    second = day(engine, tmp_path, config(daily=8))                        # the night job, same quota day
    assert second.earlier_today == 8 and second.requests_used == 0 and second.stopped == "daily budget used"
    assert quota.calls[8:] == []
