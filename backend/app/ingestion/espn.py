"""ESPN adapter: the public soccer feeds behind espn.com's score pages (no key, no daily quota). Fetches only -
parsing lives in normalisation/parsers/espn.py. docs/ESPN.md.

    scoreboard   /{league}/scoreboard?dates=YYYYMMDD     one league's matches of one day (1 request)
    schedule     /{league}/teams/{id}/schedule[?season=Y] one team's league matches of a season (1 request)
    summary      /{league}/summary?event={id}            one match incl. every goal with its minute (1 request)

Goal minutes are read from the match SUMMARY only: in the 2026-11-23 cross-check (881 matches) the scoreboard's
goal list was wrong once (Forest-Leeds 2025-11-09) where the summary was right. The feeds are undocumented, so
requests are paced (`min_interval_s`) and every response is stored as a raw payload like any other source.
"""

from __future__ import annotations

from datetime import date

from app.ingestion.base import Fetched, HttpSource

__all__ = ["Espn", "Fetched", "HttpSource"]

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"
DEFAULT_INTERVAL_S = 1.0


class Espn:
    def __init__(self, *, max_requests: int | None = None, min_interval_s: float = DEFAULT_INTERVAL_S) -> None:
        self.http = HttpSource(BASE_URL, min_interval_s=min_interval_s, max_requests=max_requests)

    def scoreboard(self, league: str, day: date) -> Fetched:
        return self.http.get(f"/{league}/scoreboard", {"dates": day.strftime("%Y%m%d")})

    def team_schedule(self, league: str, team_id: str, season: int | None = None) -> Fetched:
        return self.http.get(f"/{league}/teams/{team_id}/schedule", {"season": season} if season else {})

    def summary(self, league: str, event_id: str) -> Fetched:
        return self.http.get(f"/{league}/summary", {"event": event_id})

    @property
    def requests_used(self) -> int:
        return self.http.requests_used
