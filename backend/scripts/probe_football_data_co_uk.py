"""Phase 0 probe: what does football-data.co.uk (free CSV files) give BETTORY?

Usage (from backend/, venv active):  python -m scripts.probe_football_data_co_uk
No key needed. Downloads a few CSV files + notes.txt into backend/phase0/football_data_co_uk/,
checks columns (HT scores, Pinnacle closing odds) and cross-checks scores against openfootball.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from typing import Any

import httpx

from app.core.settings import BACKEND_DIR

BASE = "https://www.football-data.co.uk"
OUT_DIR = BACKEND_DIR / "phase0" / "football_data_co_uk"
OPENFOOTBALL_FULL = BACKEND_DIR / "phase0" / "openfootball" / "2025-26_en.1-full.json"

FILES = {
    "E0_2526": "/mmz4281/2526/E0.csv",   # England Premier League 2025-26 (complete)
    "E0_2627": "/mmz4281/2627/E0.csv",   # current season
    "D1_2526": "/mmz4281/2526/D1.csv",   # Germany Bundesliga 2025-26
    "ARG_extra": "/new/ARG.csv",          # "extra" league format
    "fixtures": "/fixtures.csv",          # upcoming fixtures
}
WANTED = {
    "HT scores": ["HTHG", "HTAG"],
    "FT scores": ["FTHG", "FTAG"],
    "Kick-off time": ["Time"],
    "Pinnacle 1X2 (pre-close)": ["PSH", "PSD", "PSA"],
    "Pinnacle 1X2 closing": ["PSCH", "PSCD", "PSCA"],
    "Pinnacle O/U 2.5": ["P>2.5", "P<2.5"],
    "Pinnacle O/U 2.5 closing": ["PC>2.5", "PC<2.5"],
    "Market average closing O/U 2.5": ["AvgC>2.5", "AvgC<2.5"],
}


def read_csv(text: str) -> list[dict[str, str]]:
    return [r for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))) if any(r.values())]


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(timeout=60.0, follow_redirects=True,
                          headers={"User-Agent": "bettory-phase0-probe"})
    notes = client.get(f"{BASE}/notes.txt")
    if notes.status_code == 200:
        (OUT_DIR / "notes.txt").write_text(notes.text, encoding="utf-8")
    print(f"[INFO] notes.txt: HTTP {notes.status_code}, {len(notes.text)} chars")

    tables: dict[str, list[dict[str, str]]] = {}
    for name, path in FILES.items():
        resp = client.get(f"{BASE}{path}")
        if resp.status_code != 200:
            print(f"[FAIL] {name}: HTTP {resp.status_code}")
            continue
        text = resp.content.decode("utf-8-sig", errors="replace")
        (OUT_DIR / f"{name}.csv").write_text(text, encoding="utf-8")
        rows = read_csv(text)
        tables[name] = rows
        cols = list(rows[0].keys()) if rows else []
        print(f"[INFO] {name}: {len(rows)} rows, {len(cols)} columns")
        for label, need in WANTED.items():
            have = [c for c in need if c in cols]
            filled = sum(1 for r in rows if all((r.get(c) or "").strip() for c in have)) if have else 0
            mark = "PASS" if len(have) == len(need) else ("PART" if have else "----")
            print(f"        [{mark}] {label}: {have or 'missing'} filled {filled}/{len(rows)}")
        if name == "ARG_extra" and cols:
            print(f"        extra-league columns: {cols}")
        if rows:
            print(f"        first row date/time: {rows[0].get('Date')} {rows[0].get('Time')}")

    cross_check(tables.get("E0_2526", []))
    return 0


def cross_check(rows: list[dict[str, str]]) -> None:
    """Compare HT/FT scores with openfootball's verified en.1-full file."""
    if not rows or not OPENFOOTBALL_FULL.exists():
        print("[INFO] cross-check skipped (missing data)")
        return
    of = json.loads(OPENFOOTBALL_FULL.read_text(encoding="utf-8"))["matches"]
    by_date: dict[str, list[dict[str, Any]]] = {}
    for m in of:
        by_date.setdefault(m["date"], []).append(m)
    matched = agree = 0
    disagreements = []
    names: dict[str, str] = {}
    for r in rows:
        d, mth, y = r["Date"].split("/")
        iso = f"{('20' + y) if len(y) == 2 else y}-{mth}-{d}"
        fd = [int(r["FTHG"]), int(r["FTAG"])]
        hd = [int(r["HTHG"]), int(r["HTAG"])]
        cands = [m for m in by_date.get(iso, []) if m["score"].get("ft") == fd]
        if len(cands) == 1:
            m = cands[0]
            matched += 1
            names.setdefault(r["HomeTeam"], m["team1"])
            if m["score"].get("ht") == hd:
                agree += 1
            else:
                disagreements.append((iso, r["HomeTeam"], r["AwayTeam"], hd, m["score"].get("ht")))
    print(f"[INFO] cross-check vs openfootball (E0 2025-26): matched {matched}/{len(rows)} by date+FT; "
          f"HT agrees {agree}/{matched}")
    for x in disagreements[:5]:
        print(f"        HT disagreement: {x}")
    print(f"        name examples (football-data.co.uk -> openfootball): {list(names.items())[:6]}")


if __name__ == "__main__":
    sys.exit(main())
