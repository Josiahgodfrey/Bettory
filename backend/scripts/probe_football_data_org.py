"""Phase 0 probe: what does football-data.org (free tier) give BETTORY?

Usage (from backend/, venv active):
    python -m scripts.probe_football_data_org            # Premier League (PL)
    python -m scripts.probe_football_data_org --comp SA

Needs FOOTBALL_DATA_ORG_KEY in .env. Uses fewer than 10 requests (free tier: 10/minute).
Raw responses are saved to backend/phase0/football_data_org/. The key is never printed or saved.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from app.core.settings import BACKEND_DIR, get_settings

BASE_URL = "https://api.football-data.org/v4"
OUT_DIR = BACKEND_DIR / "phase0" / "football_data_org"


class Probe:
    def __init__(self, key: str) -> None:
        self.client = httpx.Client(base_url=BASE_URL, headers={"X-Auth-Token": key}, timeout=30.0)
        self.requests_used = 0
        self.results: list[dict[str, Any]] = []
        self.last_headers: dict[str, str] = {}

    def get(self, name: str, path: str, params: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        response = self.client.get(path, params=params or {})
        self.requests_used += 1
        self.last_headers = {
            k: v for k, v in response.headers.items() if k.lower().startswith("x-")
        }
        try:
            body: dict[str, Any] = response.json()
        except ValueError:
            body = {"raw": response.text[:500]}
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / f"{name}.json").write_text(json.dumps(body, indent=2), encoding="utf-8")
        return response.status_code, body

    def record(self, test: str, passed: bool | None, critical: bool, detail: str) -> None:
        self.results.append({"test": test, "passed": passed, "critical": critical, "detail": detail})
        mark = {True: "PASS", False: "FAIL", None: "INFO"}[passed]
        print(f"[{mark}] {test}{' (critical)' if critical else ''}: {detail}")


def message(code: int, body: dict[str, Any]) -> str:
    return "" if code == 200 else f"HTTP {code}: {body.get('message') or body.get('raw') or body}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--comp", default="PL")
    args = parser.parse_args()

    key = get_settings().football_data_org_key.get_secret_value()
    if not key:
        print("FOOTBALL_DATA_ORG_KEY is missing in .env")
        return 2
    p = Probe(key)

    # F1 - competitions available on this plan
    code, comps = p.get("01_competitions", "/competitions")
    items = comps.get("competitions") or []
    p.record("F1 competitions", code == 200 and bool(items), True,
             f"{len(items)} available: {[c.get('code') for c in items]} {message(code, comps)}".strip())

    # F2 - competition detail and current season
    code, comp = p.get("02_competition", f"/competitions/{args.comp}")
    cur = comp.get("currentSeason") or {}
    start_year = int(str(cur.get("startDate", "0000"))[:4] or 0)
    p.record("F2 current season", code == 200, False,
             f"{comp.get('name')} current season {cur.get('startDate')} -> {cur.get('endDate')}, "
             f"matchday {cur.get('currentMatchday')} {message(code, comp)}".strip())

    # F3 - season access: current and three previous
    finished: list[dict[str, Any]] = []
    for i, year in enumerate([start_year, start_year - 1, start_year - 2, start_year - 3]):
        code, body = p.get(f"03_matches_{year}", f"/competitions/{args.comp}/matches", {"season": year})
        matches = body.get("matches") or []
        fin = [m for m in matches if m.get("status") == "FINISHED"]
        if not finished and fin:
            finished = fin
        sample = fin[0] if fin else {}
        p.record(f"F3 season {year}", code == 200 and bool(matches), i < 2,
                 f"{len(matches)} matches, {len(fin)} finished; list has goals key: {'goals' in sample}; "
                 f"halfTime: {(sample.get('score') or {}).get('halfTime')} {message(code, body)}".strip())

    # F4 - single match detail: goals with minutes?
    if finished:
        with_goals = [m for m in finished if ((m.get("score") or {}).get("fullTime") or {}).get("home") or
                      ((m.get("score") or {}).get("fullTime") or {}).get("away")]
        m = (with_goals or finished)[-1]
        code, detail = p.get("04_match_detail", f"/matches/{m['id']}")
        goals = detail.get("goals")
        minutes = [(g.get("minute"), g.get("injuryTime")) for g in (goals or [])]
        p.record("F4 match goals with minutes", code == 200 and bool(goals), True,
                 f"{detail.get('homeTeam', {}).get('name')} vs {detail.get('awayTeam', {}).get('name')} "
                 f"FT {(detail.get('score') or {}).get('fullTime')}; goals key present: {goals is not None}; "
                 f"minutes: {minutes} {message(code, detail)}".strip())
        extra = [k for k in ("bookings", "substitutions", "lineup", "homeTeam", "odds", "referees") if k in detail]
        p.record("F4 other detail fields", None, False, f"present: {extra}")
    else:
        p.record("F4 match goals with minutes", False, True, "no finished matches accessible")

    # F5 - matches by date range (yesterday..today)
    today = date.today()
    code, by_date = p.get("05_matches_by_date", "/matches",
                          {"dateFrom": (today - timedelta(days=1)).isoformat(), "dateTo": today.isoformat()})
    p.record("F5 matches by date", code == 200, False,
             f"{len(by_date.get('matches') or [])} matches yesterday-today {message(code, by_date)}".strip())

    p.record("F6 rate-limit headers", None, False, json.dumps(p.last_headers))
    return finish(p)


def finish(p: Probe) -> int:
    critical_failed = [r["test"] for r in p.results if r["critical"] and r["passed"] is False]
    verdict = "USABLE as backup" if not critical_failed else "LIMITED - see failed critical tests"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "summary.json").write_text(json.dumps({
        "run_at": datetime.now(UTC).isoformat(),
        "requests_used": p.requests_used, "verdict": verdict,
        "critical_failed": critical_failed, "results": p.results,
    }, indent=2), encoding="utf-8")
    print(f"\nRequests used: {p.requests_used}\nVERDICT: {verdict}")
    return 0 if not critical_failed else 1


if __name__ == "__main__":
    sys.exit(main())
