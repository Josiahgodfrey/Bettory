"""openfootball adapter: public football.json files on GitHub (no key)."""

from __future__ import annotations

from app.ingestion.base import Fetched, HttpSource

BASE_URL = "https://raw.githubusercontent.com/openfootball/football.json/master"


class OpenFootball:
    def __init__(self) -> None:
        self.http = HttpSource(BASE_URL, min_interval_s=0.5)

    def season_file(self, season_label: str, key: str, full: bool) -> Fetched:
        """e.g. ('2025-26', 'en.1', True) -> /2025-26/en.1-full.json"""
        return self.http.get(f"/{season_label}/{key}{'-full' if full else ''}.json")

    @property
    def requests_used(self) -> int:
        return self.http.requests_used
