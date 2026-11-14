"""API-Football v3 adapter. Free plan (Phase 0): 100 requests/day, seasons 2022-2024, dates
yesterday..tomorrow, no bulk `ids`. Fetches only - parsing lives in normalisation/parsers."""

from __future__ import annotations

from datetime import date

from app.ingestion.base import Fetched, HttpSource

BASE_URL = "https://v3.football.api-sports.io"


class ApiFootball:
    def __init__(self, key: str, max_requests: int = 90) -> None:
        self.http = HttpSource(BASE_URL, headers={"x-apisports-key": key}, min_interval_s=1.0,
                               max_requests=max_requests)

    def fixtures_by_date(self, day: date) -> Fetched:
        return self.http.get("/fixtures", {"date": day.isoformat(), "timezone": "UTC"})

    def fixtures_by_league_season(self, league_id: str, season: int) -> Fetched:
        return self.http.get("/fixtures", {"league": league_id, "season": season, "timezone": "UTC"})

    def fixture_with_events(self, fixture_id: str) -> Fetched:
        """Single fixture incl. events (goal minutes): 1 request per match."""
        return self.http.get("/fixtures", {"id": fixture_id, "timezone": "UTC"})

    def odds_by_date(self, day: date, page: int = 1) -> Fetched:
        return self.http.get("/odds", {"date": day.isoformat(), "page": page, "timezone": "UTC"})

    @property
    def requests_used(self) -> int:
        return self.http.requests_used
