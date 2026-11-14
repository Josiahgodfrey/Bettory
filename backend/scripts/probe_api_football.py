"""Phase 0 probe: is API-Football (free plan) usable as BETTORY's main data source?

Usage (from backend/, venv active):
    python -m scripts.probe_api_football            # Premier League (id 39)
    python -m scripts.probe_api_football --league 140

Needs API_FOOTBALL_KEY in .env (key from https://dashboard.api-football.com, not RapidAPI).
Uses about 10-12 requests of the daily quota. Raw responses are saved to
backend/phase0/api_football/ for the field-mapping step. The key is never printed or saved.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.core.settings import BACKEND_DIR, get_settings

BASE_URL = "https://v3.football.api-sports.io"
OUT_DIR = BACKEND_DIR / "phase0" / "api_football"
BULK_IDS_MAX = 20


class Probe:
    def __init__(self, key: str) -> None:
        self.client = httpx.Client(
            base_url=BASE_URL, headers={"x-apisports-key": key}, timeout=30.0
        )
        self.requests_used = 0
        self.results: list[dict[str, Any]] = []

    def get(self, name: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.get(path, params=params or {})
        self.requests_used += 1
        body: dict[str, Any] = response.json()
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / f"{name}.json").write_text(json.dumps(body, indent=2), encoding="utf-8")
        return body

    def record(self, test: str, passed: bool | None, critical: bool, detail: str) -> None:
        self.results.append(
            {"test": test, "passed": passed, "critical": critical, "detail": detail}
        )
        mark = {True: "PASS", False: "FAIL", None: "INFO"}[passed]
        print(f"[{mark}] {test}{' (critical)' if critical else ''}: {detail}")


def api_errors(body: dict[str, Any]) -> str:
    errors = body.get("errors")
    if not errors:
        return ""
    return json.dumps(errors) if isinstance(errors, (dict, list)) else str(errors)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", type=int, default=39)
    args = parser.parse_args()

    key = get_settings().api_football_key.get_secret_value()
    if not key:
        print("API_FOOTBALL_KEY is missing in .env")
        return 2
    p = Probe(key)

    # T0 - account status (does not count against the quota)
    status = p.get("00_status", "/status")
    sub = status.get("response", {}) or {}
    plan = (sub.get("subscription") or {}).get("plan")
    req = sub.get("requests") or {}
    p.requests_used -= 1
    p.record("T0 account", None, False, f"plan={plan}, used today={req.get('current')}/{req.get('limit_day')}")

    # T8 - league coverage per season
    league = p.get("01_league", "/leagues", {"id": args.league})
    seasons = (league.get("response") or [{}])[0].get("seasons", []) if league.get("response") else []
    if not seasons:
        p.record("T8 coverage", False, False, f"no seasons returned {api_errors(league)}")
        return finish(p)
    current = next((s for s in seasons if s.get("current")), seasons[-1])
    for s in sorted(seasons, key=lambda s: s["year"])[-4:]:
        cov = (s.get("coverage") or {})
        fx = cov.get("fixtures") or {}
        p.record(
            f"T8 coverage {s['year']}", None, False,
            f"events={fx.get('events')} lineups={fx.get('lineups')} odds={cov.get('odds')} current={s.get('current')}",
        )

    # T1 / T2 - season access
    years = [current["year"], current["year"] - 1, current["year"] - 2]
    season_fixtures: dict[int, list[dict[str, Any]]] = {}
    for i, year in enumerate(years):
        body = p.get(f"02_fixtures_{year}", "/fixtures", {"league": args.league, "season": year})
        err = api_errors(body)
        fixtures = body.get("response") or []
        season_fixtures[year] = fixtures
        test = "T1 current season" if i == 0 else f"T2 past season {year}"
        p.record(test, bool(fixtures) and not err, True, f"{len(fixtures)} fixtures {err}".strip())

    # pick finished fixtures from the newest accessible season
    finished = []
    for year in years:
        finished = [f for f in season_fixtures[year] if f["fixture"]["status"]["short"] in ("FT", "AET", "PEN")]
        if finished:
            break

    # T4 + T3 - bulk ids with events and goal minutes
    if finished:
        ids = [str(f["fixture"]["id"]) for f in finished[:BULK_IDS_MAX]]
        bulk = p.get("03_fixtures_bulk_ids", "/fixtures", {"ids": "-".join(ids)})
        got = bulk.get("response") or []
        err = api_errors(bulk)
        p.record("T4 bulk 20 ids", len(got) == len(ids) and not err, True, f"{len(got)}/{len(ids)} returned {err}".strip())
        with_events = [f for f in got if f.get("events")]
        goals = [
            e for f in got for e in (f.get("events") or [])
            if e.get("type") == "Goal" and e.get("detail") != "Missed Penalty"
        ]
        minutes_ok = all(isinstance((g.get("time") or {}).get("elapsed"), int) for g in goals)
        p.record(
            "T3 goal events with minutes", bool(with_events) and bool(goals) and minutes_ok, True,
            f"{len(with_events)}/{len(got)} fixtures have events, {len(goals)} goals, all with minute={minutes_ok}",
        )
        early = sorted({g['time']['elapsed'] for g in goals if g['time']['elapsed'] <= 16})
        p.record("T3 minute labels <= 16", None, False, f"minutes seen: {early} (extra field present: {any('extra' in g['time'] for g in goals)})")
        # T6 - lineups
        lu = p.get("05_lineups", "/fixtures/lineups", {"fixture": ids[0]})
        p.record("T6 lineups", bool(lu.get("response")), False, f"{len(lu.get('response') or [])} teams {api_errors(lu)}".strip())
    else:
        p.record("T3/T4", False, True, "no finished fixtures accessible to test")

    # T5 - fixtures by date (today, EAT)
    today = date.today().isoformat()
    by_date = p.get("04_fixtures_today", "/fixtures", {"date": today, "timezone": "Africa/Nairobi"})
    err = api_errors(by_date)
    p.record("T5 fixtures by date", not err, True, f"{len(by_date.get('response') or [])} fixtures on {today} {err}".strip())

    # T7 - odds markets
    bets = p.get("06_odds_bets", "/odds/bets")
    names = [b.get("name") or "" for b in bets.get("response") or []]
    interesting = [n for n in names if any(k in n.lower() for k in ("first half", "1st half", "minute", "15"))]
    p.record("T7 odds market list", bool(names), False, f"{len(names)} markets; relevant: {interesting[:15]} {api_errors(bets)}".strip())
    odds_today = p.get("07_odds_today", "/odds", {"date": today, "timezone": "Africa/Nairobi"})
    p.record("T7 odds for today", None, False, f"{odds_today.get('results', 0)} results, pages={(odds_today.get('paging') or {}).get('total')} {api_errors(odds_today)}".strip())

    return finish(p)


def finish(p: Probe) -> int:
    critical_failed = [r["test"] for r in p.results if r["critical"] and r["passed"] is False]
    verdict = "CONFIRMED (free plan)" if not critical_failed else "NOT CONFIRMED - see failed critical tests"
    summary = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "requests_used": p.requests_used,
        "verdict": verdict,
        "critical_failed": critical_failed,
        "results": p.results,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nRequests used: {p.requests_used}")
    print(f"VERDICT: {verdict}")
    if critical_failed:
        print(f"Failed critical tests: {', '.join(critical_failed)}")
    return 0 if not critical_failed else 1


if __name__ == "__main__":
    sys.exit(main())
