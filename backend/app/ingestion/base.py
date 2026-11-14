"""Fetching basics: every adapter returns Fetched (raw bytes + request description). No parsing here."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx


class FetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class Fetched:
    endpoint: str
    params: dict[str, Any]
    http_status: int
    body: bytes
    ext: str  # "json" or "csv"
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class HttpSource:
    """HTTP client with a minimum interval between requests and bounded retries (429 / 5xx / network)."""

    def __init__(self, base_url: str, headers: dict[str, str] | None = None, min_interval_s: float = 0.0,
                 max_requests: int | None = None, retries: int = 3) -> None:
        self.client = httpx.Client(base_url=base_url, headers={"User-Agent": "bettory/0.1", **(headers or {})},
                                   timeout=60.0, follow_redirects=True)
        self.min_interval = min_interval_s
        self.max_requests = max_requests
        self.retries = retries
        self.requests_used = 0
        self._last = 0.0

    def get(self, path: str, params: dict[str, Any] | None = None, ext: str = "json") -> Fetched:
        if self.max_requests is not None and self.requests_used >= self.max_requests:
            raise FetchError(f"request budget of {self.max_requests} reached")
        params = params or {}
        for attempt in range(self.retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                resp = self.client.get(path, params=params)
            except httpx.TransportError as exc:
                if attempt == self.retries:
                    raise FetchError(f"{path}: {exc}") from exc
                time.sleep(2 * (attempt + 1))
                continue
            self.requests_used += 1
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == self.retries:
                    raise FetchError(f"{path}: HTTP {resp.status_code}")
                time.sleep(float(resp.headers.get("Retry-After", 10 * (attempt + 1))))
                continue
            if resp.status_code >= 400:
                raise FetchError(f"{path}: HTTP {resp.status_code} {resp.text[:200]}")
            return Fetched(endpoint=path, params=params, http_status=resp.status_code, body=resp.content, ext=ext)
        raise FetchError(f"{path}: retries exhausted")
