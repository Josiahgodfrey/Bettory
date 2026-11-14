"""football-data.co.uk adapter: CSV files (no key). Certificate verification stays ON
(Phase 0: TLS interception on the development network blocks this source locally)."""

from __future__ import annotations

from app.ingestion.base import Fetched, HttpSource

BASE_URL = "https://www.football-data.co.uk"


class FootballDataCoUk:
    def __init__(self) -> None:
        self.http = HttpSource(BASE_URL, min_interval_s=1.0)

    def division_csv(self, season_code: str, division: str) -> Fetched:
        """e.g. ('2526', 'E0') -> /mmz4281/2526/E0.csv"""
        return self.http.get(f"/mmz4281/{season_code}/{division}.csv", ext="csv")

    @property
    def requests_used(self) -> int:
        return self.http.requests_used
