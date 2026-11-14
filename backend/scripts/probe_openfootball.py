"""Phase 0 probe: what does openfootball (football.json on GitHub) give BETTORY?

Usage (from backend/, venv active):  python -m scripts.probe_openfootball
No key needed. Uses the GitHub API once (repository file list, limit 60/hour unauthenticated)
and raw.githubusercontent.com for files. Samples saved to backend/phase0/openfootball/.
"""

from __future__ import annotations

import collections
import json
import sys
from typing import Any

import httpx

from app.core.settings import BACKEND_DIR

REPO = "openfootball/football.json"
RAW = f"https://raw.githubusercontent.com/{REPO}/master"
OUT_DIR = BACKEND_DIR / "phase0" / "openfootball"


def main() -> int:
    client = httpx.Client(timeout=30.0, headers={"User-Agent": "bettory-phase0-probe"})
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # O1 - inventory: seasons and leagues available
    tree = client.get(f"https://api.github.com/repos/{REPO}/git/trees/master", params={"recursive": "1"})
    if tree.status_code != 200:
        print(f"[FAIL] O1 inventory: HTTP {tree.status_code} {tree.text[:200]}")
        return 1
    files = [t["path"] for t in tree.json().get("tree", []) if t["path"].endswith(".json")]
    by_season: dict[str, list[str]] = collections.defaultdict(list)
    for f in files:
        if "/" in f:
            season, name = f.split("/", 1)
            by_season[season].append(name)
    seasons = sorted(by_season)
    (OUT_DIR / "00_inventory.json").write_text(json.dumps(by_season, indent=2), encoding="utf-8")
    print(f"[INFO] O1 inventory: {len(files)} json files, seasons {seasons[0]} .. {seasons[-1]} ({len(seasons)})")
    for s in seasons[-4:]:
        print(f"         {s}: {sorted(by_season[s])}")

    # O2 - sample files: latest complete season and current season, England top flight
    report: dict[str, Any] = {}
    for season in [s for s in seasons if "en.1.json" in by_season[s]][-3:]:
        url = f"{RAW}/{season}/en.1.json"
        resp = client.get(url)
        if resp.status_code != 200:
            print(f"[FAIL] O2 {season}/en.1.json: HTTP {resp.status_code}")
            continue
        data = resp.json()
        (OUT_DIR / f"{season}_en.1.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        matches = data.get("matches") or [
            m for r in data.get("rounds", []) for m in r.get("matches", [])
        ]
        keys = collections.Counter(k for m in matches for k in m)
        score_keys = collections.Counter(k for m in matches for k in (m.get("score") or {}))
        played = [m for m in matches if (m.get("score") or {}).get("ft")]
        with_goals = [m for m in played if m.get("goals1") is not None or m.get("goals2") is not None or m.get("goals")]
        goal_minutes = [
            g.get("minute") for m in with_goals for side in ("goals1", "goals2") for g in (m.get(side) or [])
        ]
        report[season] = {
            "matches": len(matches), "played": len(played),
            "match_keys": dict(keys), "score_keys": dict(score_keys),
            "played_with_goal_lists": len(with_goals), "goal_minutes_sample": goal_minutes[:10],
        }
        print(f"[INFO] O2 {season} en.1: {len(matches)} matches, {len(played)} played; "
              f"keys {sorted(keys)}; score {sorted(score_keys)}; "
              f"played with goal lists {len(with_goals)}; minutes sample {goal_minutes[:8]}")
        if played:
            print(f"         sample: {json.dumps(played[0])[:300]}")

    (OUT_DIR / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
