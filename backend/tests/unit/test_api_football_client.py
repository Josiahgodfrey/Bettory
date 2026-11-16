"""API-Football client: pacing to the plan's per-minute limit and retry of its rate-limit answer (no network)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.ingestion import api_football
from app.ingestion.api_football import ApiFootball, rate_limited
from app.ingestion.base import FetchError

LIMITED = json.dumps({"errors": {"rateLimit": "Too many requests. Your rate limit is 10 requests per minute."},
                      "response": []}).encode()
OK = json.dumps({"errors": [], "results": 1, "response": [{"fixture": {"id": 1}}]}).encode()


def client(bodies: list[bytes], max_requests: int = 10) -> tuple[ApiFootball, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=bodies[min(len(seen), len(bodies)) - 1])

    c = ApiFootball("key", max_requests=max_requests, requests_per_minute=10)
    c.http.client = httpx.Client(base_url=api_football.BASE_URL, transport=httpx.MockTransport(handler))
    c.http.min_interval = 0.0
    return c, seen


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr(api_football.time, "sleep", slept.append)
    return slept


def test_pacing_follows_the_plan() -> None:
    assert ApiFootball("k", max_requests=1, requests_per_minute=10).http.min_interval == 6.0
    assert ApiFootball("k", max_requests=1, requests_per_minute=300).http.min_interval == pytest.approx(0.2)
    with pytest.raises(ValueError):
        ApiFootball("k", max_requests=1, requests_per_minute=0)


def test_rate_limit_answer_is_retried_after_the_window(no_sleep: list[float]) -> None:
    c, seen = client([LIMITED, OK])
    fetched = c.fixture_with_events("1")
    assert fetched.body == OK and len(seen) == 2 and c.requests_used == 2
    assert no_sleep == [api_football.RATE_LIMIT_WINDOW_S]


def test_persistent_rate_limit_is_returned_for_the_parser_to_fail_visibly() -> None:
    c, seen = client([LIMITED])
    assert rate_limited(c.odds_for_fixture("1").body) and len(seen) == api_football.RATE_LIMIT_RETRIES + 1


def test_retries_respect_the_request_budget() -> None:
    c, _ = client([LIMITED], max_requests=2)
    with pytest.raises(FetchError, match="budget"):
        c.fixture_with_events("1")


def test_rate_limited_detection() -> None:
    assert rate_limited(LIMITED) and not rate_limited(OK)
    assert not rate_limited(b"not json") and not rate_limited(b"[]")
