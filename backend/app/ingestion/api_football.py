"""API-Football v3 adapter. Fetches only - parsing lives in normalisation/parsers.

Free plan (Phase 0): 100 requests/day, league seasons 2022-2024, dates yesterday..tomorrow, no bulk `ids`
(1 request per match for goal minutes). Paid plans: all seasons, `ids` with up to 20 fixtures (incl. events)
per request."""

from __future__ import annotations

from datetime import date

from app.ingestion.base import Fetched, HttpSource

BASE_URL = "https://v3.football.api-sports.io"
BULK_IDS_MAX = 20  # API-Football limit for the `ids` parameter


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

    def fixtures_by_ids(self, fixture_ids: list[str]) -> Fetched:
        """Up to 20 fixtures incl. events in ONE request (paid plans only; the free plan answers with an error)."""
        if not 1 <= len(fixture_ids) <= BULK_IDS_MAX:
            raise ValueError(f"ids takes 1-{BULK_IDS_MAX} fixtures, got {len(fixture_ids)}")
        return self.http.get("/fixtures", {"ids": "-".join(fixture_ids), "timezone": "UTC"})

    def odds_by_date(self, day: date, page: int = 1) -> Fetched:
        return self.http.get("/odds", {"date": day.isoformat(), "page": page, "timezone": "UTC"})

    @property
    def requests_used(self) -> int:
        return self.http.requests_used
