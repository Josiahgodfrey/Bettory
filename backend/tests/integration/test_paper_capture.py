"""Paper betting step 2: the capture (only the session's upcoming fixtures of the paper competitions, within the
request budget) and the sealing of the odds known at as_of with the frozen prediction. Fake API, no network."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest
from sqlalchemy import Engine, create_engine, text

from app.domain.enums import SourceCode
from app.domain.registry import CompetitionDef, Registry
from app.domain.sessions import SessionCalendar, SessionId, SessionKind
from app.ingestion import pipeline
from app.ingestion.base import Fetched
from app.paper.freeze import freeze_session, replay_session
from app.storage.raw_store import LocalRawStore
from app.storage.repositories import paper as paper_repo
from tests.integration.conftest import ADMIN_URL
from tests.support import PARAMS

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")
REG = Registry([CompetitionDef(code=c, name=c, country="Testland", type="LEAGUE", timezone="UTC",
                               season_format="SPLIT", fifteen_minute_market=True,
                               team_sources=(SourceCode.API_FOOTBALL,), providers={SourceCode.API_FOOTBALL: lid})
                for c, lid in (("TST1", "990"), ("TST2", "991"))])
DAY = date.today() + timedelta(days=30)           # in the future: the database refuses to freeze a played match
BASE = datetime.combine(DAY, datetime.min.time(), UTC)
# (fixture id, league, kick-off): two TST1 fixtures in the NOON session (15:00 and 19:00 EAT), one of the other
# league, one TST1 fixture after the window
FIXTURES = [(9901, "990", BASE + timedelta(hours=12)), (9902, "990", BASE + timedelta(hours=16)),
            (9903, "991", BASE + timedelta(hours=13)), (9904, "990", BASE + timedelta(hours=23))]
SID = SessionId(DAY, SessionKind.NOON)
CAL = SessionCalendar(PARAMS)
AS_OF = CAL.run_start(SID, FIXTURES[0][2])


def fixture_item(fid: int, league: str, ko: datetime) -> dict[str, Any]:
    home, away = 10 * fid, 10 * fid + 1
    return {"fixture": {"id": fid, "timezone": "UTC", "date": ko.isoformat(), "timestamp": int(ko.timestamp()),
                        "status": {"long": "Not Started", "short": "NS", "elapsed": None, "extra": None}},
            "league": {"id": int(league), "name": league, "country": "Testland", "season": DAY.year, "round": "R1"},
            "teams": {"home": {"id": home, "name": f"Club {home}"}, "away": {"id": away, "name": f"Club {away}"}},
            "goals": {"home": None, "away": None},
            "score": {"halftime": {"home": None, "away": None}, "fulltime": {"home": None, "away": None}}}


def odds_item(fid: int, league: str, book: str, update: datetime, no_goal: str, draw: str) -> dict[str, Any]:
    return {"league": {"id": int(league)}, "fixture": {"id": fid}, "update": update.isoformat(),
            "bookmakers": [{"id": 1, "name": book, "bets": [
                {"id": 1, "name": "Goal in 1-15 minutes", "values": [{"value": "Yes", "odd": "3.10"},
                                                                     {"value": "No", "odd": no_goal}]},
                {"id": 2, "name": "1x2 - 15 minutes", "values": [{"value": "Home", "odd": "7.50"},
                                                                 {"value": "Draw", "odd": draw},
                                                                 {"value": "Away", "odd": "9.00"}]},
                {"id": 3, "name": "Goals Over/Under", "values": [{"value": "Over 2.5", "odd": "1.90"},
                                                                 {"value": "Under 2.5", "odd": "1.95"}]}]}]}


class FakeHttp:
    max_requests: int | None = None


class FakeClient:
    calls: ClassVar[list[dict[str, Any]]] = []
    updates: ClassVar[dict[int, list[tuple[str, datetime, str, str]]]] = {}   # fixture -> (bookmaker, update, no, draw)

    def __init__(self, *_a: Any, **_k: Any) -> None:
        self.requests_used, self.rate_limit_retries, self.http, self.subscription_plan = 0, 0, FakeHttp(), "Free"

    def _f(self, endpoint: str, params: dict[str, Any], items: list[dict[str, Any]]) -> Fetched:
        self.requests_used += 1
        FakeClient.calls.append(params)
        body = json.dumps({"errors": [], "results": len(items), "response": items}).encode()
        return Fetched(endpoint=endpoint, params=params, http_status=200, body=body, ext="json")

    def quota(self) -> tuple[int, int]:
        return len(FakeClient.calls), 100

    def fixtures_by_date(self, day: date) -> Fetched:
        return self._f("/fixtures", {"date": day.isoformat()},
                       [fixture_item(f, lg, ko) for f, lg, ko in FIXTURES if ko.date() == day])

    def odds_for_fixture(self, fixture_id: str) -> Fetched:
        fid = int(fixture_id)
        league = next(lg for f, lg, _ in FIXTURES if f == fid)
        return self._f("/odds", {"fixture": fixture_id},
                       [odds_item(fid, league, b, u, n, d) for b, u, n, d in FakeClient.updates.get(fid, [])])


@pytest.fixture
def engine(fresh_db_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    FakeClient.calls, FakeClient.updates = [], {}
    monkeypatch.setattr(pipeline, "ApiFootball", FakeClient)
    eng = create_engine(fresh_db_url.replace("postgresql://", "postgresql+psycopg://"))
    yield eng
    eng.dispose()


def capture(engine: Engine, tmp_path: Path, max_requests: int) -> Any:
    window = CAL.times(SID)
    return pipeline.api_football_session_odds(engine, LocalRawStore(tmp_path), REG, "key", window.start_utc,
                                              window.end_utc, ["TST1"], max_requests, requests_per_minute=10)


def test_capture_takes_only_the_sessions_paper_fixtures(engine: Engine, tmp_path: Path) -> None:
    FakeClient.updates = {9901: [("BookA", AS_OF - timedelta(hours=2), "1.40", "1.55")],
                          9902: [("BookA", AS_OF - timedelta(hours=2), "1.38", "1.50")]}
    stats = capture(engine, tmp_path, 12)
    assert [c.get("fixture") for c in FakeClient.calls] == [None, "9901", "9902"]   # 1 date, 2 odds: not TST2,
    assert stats["session_fixtures"] == 2 and stats["odds_requests"] == 2           # not after the window
    with engine.connect() as c:
        markets = {r[0] for r in c.execute(text("select distinct market::text from core.odds_snapshots"))}
        upcoming = c.execute(text("select count(*) from core.matches where status::text = 'SCHEDULED'")).scalar()
    assert markets == {"FIRST15_GOAL", "FIRST15_RESULT", "FT_TOTAL"} and upcoming == 4


def test_capture_stops_at_its_budget(engine: Engine, tmp_path: Path) -> None:
    stats = capture(engine, tmp_path, 2)
    assert len(FakeClient.calls) == 2 and stats["stopped_at_request_budget"] == 1
    assert stats["fixtures_without_odds_request"] == 1


def test_freeze_seals_only_the_odds_known_at_as_of(engine: Engine, tmp_path: Path) -> None:
    FakeClient.updates = {9901: [("BookA", AS_OF - timedelta(hours=3), "1.40", "1.55"),
                                 ("BookB", AS_OF - timedelta(hours=1), "1.44", "1.52"),
                                 ("BookC", AS_OF + timedelta(minutes=5), "1.60", "1.70")]}   # seen too late
    capture(engine, tmp_path, 12)
    r = freeze_session(engine, PARAMS, REG, SID, now=AS_OF, competitions=["TST1"])
    assert r.status == "FROZEN" and r.frozen == 2
    with engine.connect() as c:
        rows = {str(x.match_id): x for x in paper_repo.frozen(c, str(SID))}
        chain = paper_repo.verify_chain(c)
        m9901 = c.execute(text("select distinct match_id from core.match_source_records "
                               "where provider_match_key = '9901'")).scalar_one()
    sealed = rows[str(m9901)]
    assert (sealed.best_under05_odds, sealed.best_under05_book) == (1.44, "BookB")      # not BookC's 1.60
    assert (sealed.best_draw_odds, sealed.best_draw_book) == (1.55, "BookA")
    assert {o["bookmaker"] for o in sealed.odds_seen} == {"BookA", "BookB"} and len(sealed.odds_seen) == 14
    other = next(x for k, x in rows.items() if k != str(m9901))
    assert other.odds_seen == [] and other.best_under05_odds is None                    # no odds captured
    assert chain.intact and chain.rows == 2
    assert replay_session(engine, PARAMS, REG, str(SID)).identical
