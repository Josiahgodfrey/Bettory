"""API-Football v3 adapter. Fetches only - parsing lives in normalisation/parsers.

Free plan (Phase 0): 100 requests/day and 10 per minute, league seasons 2022-2024, dates yesterday..tomorrow,
no bulk `ids` (1 request per match for goal minutes). Paid plans: all seasons, `ids` with up to 20 fixtures
(incl. events) per request.

The per-minute limit is answered with HTTP 200 and {"errors": {"rateLimit": ...}}, not 429: requests are paced
to `requests_per_minute`, and a rate-limit answer is retried after the one-minute window instead of failing."""

from __future__ import annotations

import json
import logging
import time
from datetime import date
from typing import Any

from app.ingestion.base import Fetched, HttpSource

log = logging.getLogger(__name__)

BASE_URL = "https://v3.football.api-sports.io"
BULK_IDS_MAX = 20  # API-Football limit for the `ids` parameter
RATE_LIMIT_WINDOW_S = 61.0  # the per-minute window, plus a second of margin
RATE_LIMIT_RETRIES = 3


def rate_limited(body: bytes) -> bool:
    try:
        errors = json.loads(body).get("errors")
    except (ValueError, AttributeError):
        return False
    return isinstance(errors, dict) and "rateLimit" in errors


class ApiFootball:
    def __init__(self, key: str, *, max_requests: int, requests_per_minute: int) -> None:
        if requests_per_minute < 1:
            raise ValueError("requests_per_minute must be >= 1")
        self.http = HttpSource(BASE_URL, headers={"x-apisports-key": key}, min_interval_s=60.0 / requests_per_minute,
                               max_requests=max_requests)

    def _get(self, path: str, params: dict[str, Any]) -> Fetched:
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            fetched = self.http.get(path, params)
            if not rate_limited(fetched.body) or attempt == RATE_LIMIT_RETRIES:
                return fetched
            log.warning("API-Football per-minute limit hit; waiting %.0f s (retry %d)", RATE_LIMIT_WINDOW_S,
                        attempt + 1)
            time.sleep(RATE_LIMIT_WINDOW_S)
        raise AssertionError("unreachable")

    def fixtures_by_date(self, day: date) -> Fetched:
        return self._get("/fixtures", {"date": day.isoformat(), "timezone": "UTC"})

    def fixtures_by_league_season(self, league_id: str, season: int) -> Fetched:
        return self._get("/fixtures", {"league": league_id, "season": season, "timezone": "UTC"})

    def fixture_with_events(self, fixture_id: str) -> Fetched:
        """Single fixture incl. events (goal minutes): 1 request per match."""
        return self._get("/fixtures", {"id": fixture_id, "timezone": "UTC"})

    def fixtures_by_ids(self, fixture_ids: list[str]) -> Fetched:
        """Up to 20 fixtures incl. events in ONE request (paid plans only; the free plan answers with an error)."""
        if not 1 <= len(fixture_ids) <= BULK_IDS_MAX:
            raise ValueError(f"ids takes 1-{BULK_IDS_MAX} fixtures, got {len(fixture_ids)}")
        return self._get("/fixtures", {"ids": "-".join(fixture_ids), "timezone": "UTC"})

    def odds_by_date(self, day: date, page: int = 1) -> Fetched:
        return self._get("/odds", {"date": day.isoformat(), "page": page, "timezone": "UTC"})

    def odds_for_fixture(self, fixture_id: str) -> Fetched:
        return self._get("/odds", {"fixture": fixture_id})

    @property
    def requests_used(self) -> int:
        return self.http.requests_used
