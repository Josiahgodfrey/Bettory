"""football-data.org v4 adapter. Free tier: 10 requests/minute, 13 competitions, no goal minutes."""

from __future__ import annotations

from app.ingestion.base import Fetched, HttpSource

BASE_URL = "https://api.football-data.org/v4"


class FootballDataOrg:
    def __init__(self, key: str) -> None:
        self.http = HttpSource(BASE_URL, headers={"X-Auth-Token": key}, min_interval_s=6.5)

    def competition_matches(self, code: str, season_start_year: int) -> Fetched:
        return self.http.get(f"/competitions/{code}/matches", {"season": season_start_year})

    @property
    def requests_used(self) -> int:
        return self.http.requests_used
