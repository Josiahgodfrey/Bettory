"""ESPN history job end to end on a fresh database, with a fake ESPN client serving real (trimmed) payloads.

Setting: API-Football already stored a fixture Fulham v "Manchester Utd" and their earlier match (FT 1-1, no goal
minutes). The ESPN run must link Fulham by alias, link Manchester United through the fixture (its ESPN name differs),
fetch the earlier match's summary and make it COMPLETE (AGREED with API-Football's score).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest
from sqlalchemy import Engine, create_engine, text

from app.domain.enums import FixtureStatus, SourceCode
from app.domain.models import Score, SourceMatch
from app.domain.registry import load_registry
from app.ingestion import pipeline
from app.ingestion.base import Fetched, FetchError
from app.ingestion.pipeline import FixtureRef, Job
from app.normalisation.parsers import ParseResult
from app.storage.raw_store import LocalRawStore
from tests.integration.conftest import ADMIN_URL

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")
FIX = Path(__file__).resolve().parents[1] / "fixtures"
REG = load_registry()
SUMMARY = json.loads((FIX / "espn_summary_eng1_owngoal.json").read_text(encoding="utf-8"))
EVENT = str(SUMMARY["header"]["id"])
PAST_KO = datetime(2025, 8, 24, 15, 30, tzinfo=UTC)
NEXT_KO = datetime(2025, 9, 20, 14, 0, tzinfo=UTC)


def team(espn_id: str, name: str, side: str, score: str | None = None) -> dict[str, Any]:
    t: dict[str, Any] = {"homeAway": side, "id": espn_id, "team": {"id": espn_id, "displayName": name}}
    if score is not None:
        t["score"] = score
    return t


def event(event_id: str, ko: datetime, done: bool, home: dict[str, Any], away: dict[str, Any]) -> dict[str, Any]:
    status = ({"type": {"name": "STATUS_FULL_TIME", "state": "post", "completed": True}} if done else
              {"type": {"name": "STATUS_SCHEDULED", "state": "pre", "completed": False}})
    return {"id": event_id, "date": ko.strftime("%Y-%m-%dT%H:%MZ"), "season": {"year": 2025},
            "competitions": [{"status": status, "competitors": [home, away]}]}


class FakeEspn:
    """Serves the scoreboard of the fixture day, both teams' schedules (the earlier match) and its summary."""

    calls: ClassVar[list[str]] = []

    def __init__(self, *, max_requests: int | None = None, min_interval_s: float = 0) -> None:
        self.requests_used = 0
        self.max_requests = max_requests

    def _ok(self, what: str, body: dict[str, Any]) -> Fetched:
        self.requests_used += 1
        FakeEspn.calls.append(what)
        return Fetched(endpoint=what, params={}, http_status=200, body=json.dumps(body).encode(), ext="json")

    def scoreboard(self, league: str, day: date) -> Fetched:
        events = [event("next", NEXT_KO, False, team("370", "Fulham", "home"),
                        team("360", "Manchester United", "away"))] if day == NEXT_KO.date() else []
        return self._ok(f"scoreboard:{league}:{day}", {"leagues": [{"season": {"year": 2025}}], "events": events})

    def team_schedule(self, league: str, team_id: str, season: int | None = None) -> Fetched:
        if league != "eng.1" or season is not None:
            return self._ok(f"schedule:{league}:{team_id}:{season}", {"events": []})
        past = event(EVENT, PAST_KO, True, team("370", "Fulham", "home"), team("360", "Manchester United", "away"))
        return self._ok(f"schedule:{league}:{team_id}", {"events": [past]})

    def summary(self, league: str, event_id: str) -> Fetched:
        if event_id != EVENT:
            self.requests_used += 1
            raise FetchError("HTTP 404")
        return self._ok(f"summary:{league}:{event_id}", SUMMARY)


@pytest.fixture
def engine(fresh_db_url: str) -> Iterator[Engine]:
    eng = create_engine(fresh_db_url.replace("postgresql://", "postgresql+psycopg://"))
    yield eng
    eng.dispose()


def af_match(key: str, ko: datetime, finished: bool) -> SourceMatch:
    return SourceMatch(source=SourceCode.API_FOOTBALL, provider_match_key=key, competition_code="ENG1",
                       season_label="2025-26", kickoff_date=ko.date(), kickoff_utc=ko, home_name="Fulham",
                       away_name="Manchester Utd", home_key="af-fulham", away_key="af-mufc",
                       status=FixtureStatus.FINISHED if finished else FixtureStatus.SCHEDULED,
                       ft=Score(home=1, away=1) if finished else None, has_goal_minutes=False)


def seed(engine: Engine, tmp_path: Path) -> None:
    job = Job(engine, LocalRawStore(tmp_path), REG, SourceCode.API_FOOTBALL, "test:seed")
    parsed = ParseResult(matches=[af_match("af1", PAST_KO, True), af_match("af2", NEXT_KO, False)])
    job.process(Fetched(endpoint="seed", params={}, http_status=200, body=b"{}", ext="json"), lambda raw: parsed)
    job.finish(requests_used=0)


def q(engine: Engine, sql: str) -> list[tuple[Any, ...]]:
    with engine.connect() as c:
        return [tuple(r) for r in c.execute(text(sql))]


def run(engine: Engine, tmp_path: Path, **kw: Any) -> Any:
    with engine.connect() as c:
        h, a = c.execute(text("select home_team_id, away_team_id from core.matches where kickoff_utc = :k"),
                         {"k": NEXT_KO}).one()
    return pipeline.espn_history(engine, LocalRawStore(tmp_path), REG, [FixtureRef("ENG1", NEXT_KO, h, a)],
                                 history_matches=1, job_name="espn_history:test", **kw)


def test_history_links_teams_and_completes_the_earlier_match(engine: Engine, tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "Espn", FakeEspn)
    FakeEspn.calls = []
    seed(engine, tmp_path)
    assert q(engine, "select data_status::text from core.matches where kickoff_utc = '2025-08-24T15:30Z'") == \
        [("INCOMPLETE",)]

    st = run(engine, tmp_path, max_requests=100)

    # Fulham by its exact alias, Manchester United through the stored fixture (ESPN's name differs)
    assert st["espn_teams_linked_by_alias"] == 1 and st["espn_teams_linked_by_fixture"] == 1
    assert sorted(q(engine, "select provider_key, method::text from core.provider_entity_map where source_id = 5")) == \
        [("id:360", "FIXTURE_PAIRING"), ("id:370", "ALIAS")]
    # the earlier match: ESPN's goal minutes, agreeing with API-Football's score
    assert st["summaries"] == 1 and st["events_needed"] == 1
    assert q(engine, "select data_status::text, reconciliation_status::text, h15_home, h15_away from core.matches "
                     "where kickoff_utc = '2025-08-24T15:30Z'") == [("COMPLETE", "AGREED", 0, 0)]
    assert q(engine, "select minute, is_own_goal from core.match_goals order by sequence") == [(58, True), (73, False)]
    assert q(engine, "select count(*) from core.teams") == [(2,)]                     # no duplicate team
    assert q(engine, "select status::text, requests_used from raw.ingestion_runs "
                     "where job_name = 'espn_history:test'") == [("SUCCEEDED", st["schedules"] + st["scoreboards"] + 1)]

    # a second run fetches no summary again (already from ESPN)
    FakeEspn.calls = []
    st2 = run(engine, tmp_path, max_requests=100)
    assert st2.get("summaries", 0) == 0 and not [c for c in FakeEspn.calls if c.startswith("summary")]


def test_history_skips_matches_already_complete_and_respects_the_budget(engine: Engine, tmp_path: Path,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "Espn", FakeEspn)
    seed(engine, tmp_path)
    st = run(engine, tmp_path, max_requests=1)          # the first scoreboard uses the whole budget
    assert st["stopped_at_request_budget"] == 1 and st.get("summaries", 0) == 0
    assert q(engine, "select status::text from raw.ingestion_runs where job_name = 'espn_history:test'") == \
        [("SUCCEEDED",)]
