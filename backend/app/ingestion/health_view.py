"""The data-health picture (docs/DATA-HEALTH.md): one snapshot for the command line and the API.

    coverage      per competition-season: finished matches, usable 15' results (exact / derived), unknown,
                  conflicts, sources and their goal minutes, the current cross-check
    crosschecks   the current cross-check of each competition-season, and which block bets
    runs          the latest ingestion runs with their audit (OK / WARNING / ERROR) and findings
    open items    team names waiting for review, open data-quality issues
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection

from app.storage.repositories import health as health_repo


def _rate(agreed: int, compared: int) -> float | None:
    return agreed / compared if compared else None


def snapshot(conn: Connection, runs: int = 10) -> dict[str, Any]:
    checks = {c["competition_season_id"]: c for c in health_repo.latest_crosschecks(conn)}
    coverage = []
    for row in health_repo.coverage(conn):
        usable = row["complete"] + row["derived"]
        check = checks.get(row["competition_season_id"])
        coverage.append({
            "competition": row["competition"], "season": row["season"], "finished": row["finished"],
            "usable_15": usable, "usable_15_share": usable / row["finished"] if row["finished"] else None,
            "complete": row["complete"], "derived": row["derived"], "partial": row["partial"],
            "unknown_15": row["incomplete"], "conflicts": row["conflicts"], "with_ht": row["with_ht"],
            "sources": row["sources"],
            "crosscheck": None if check is None else check_summary(check),
        })
    return {
        "coverage": coverage,
        "blocking_crosschecks": [check_summary(c) for c in checks.values()
                                 if c["status"] == "FAIL" and c["accepted_at"] is None],
        "runs": [_run_summary(r) for r in health_repo.recent_runs(conn, runs)],
        "open": health_repo.open_items(conn),
    }


def check_summary(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(c["id"]), "competition": c["competition"], "season": c["season"], "status": c["status"],
        "accepted": c["accepted_at"] is not None, "accepted_note": c["accepted_note"],
        "blocks_bets": c["status"] == "FAIL" and c["accepted_at"] is None,
        "fifteen_verified": c["fifteen_verified"], "checked_at": c["checked_at"].isoformat(),
        "matches": c["matches"], "multi_source": c["multi_source"], "failed_checks": list(c["failed_checks"]),
        "agreement": {k: {"compared": c[f"{k}_compared"], "agreed": c[f"{k}_agreed"],
                          "rate": _rate(c[f"{k}_agreed"], c[f"{k}_compared"])}
                      for k in ("ft", "ht", "h15", "h15_vs_ht")},
        "min_agreement": c["min_agreement"], "min_compared": c["min_compared"],
    }


def _run_summary(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(r["id"]), "source": r["source"], "job": r["job_name"], "status": r["status"],
        "started_at": r["started_at"].isoformat(),
        "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
        "requests_used": r["requests_used"], "items_stored": r["items_stored"], "error": r["error"],
        "audit_status": r["audit_status"], "findings": r["audit"], "stats": r["stats"],
    }


# --- text -----------------------------------------------------------------------------------------------


def _pct(x: float | None) -> str:
    return "  -  " if x is None else f"{x:5.1%}"


def render_text(s: dict[str, Any], quota: tuple[int, int] | None = None) -> str:
    out = ["COVERAGE (finished matches)",
           f"  {'comp':<5} {'season':<8} {'finished':>8} {'usable 15':>10} {'share':>6} {'exact':>6} {'HT 0-0':>6} "
           f"{'unknown':>7} {'confl.':>6}  cross-check        sources (matches / with goal minutes)"]
    for c in s["coverage"]:
        x = c["crosscheck"]
        check = "never run" if x is None else (
            f"{x['status']}{' (accepted)' if x['accepted'] else ''}{', 15 verified' if x['fifteen_verified'] else ''}")
        sources = ", ".join(f"{k} {v['matches']}/{v['with_minutes']}" for k, v in sorted(c["sources"].items()))
        out.append(f"  {c['competition']:<5} {c['season']:<8} {c['finished']:>8} {c['usable_15']:>10} "
                   f"{_pct(c['usable_15_share']):>6} {c['complete']:>6} {c['derived']:>6} {c['unknown_15']:>7} "
                   f"{c['conflicts']:>6}  {check:<18} {sources}")

    out += ["", "CROSS-CHECKS BLOCKING BETS"]
    if not s["blocking_crosschecks"]:
        out.append("  none")
    for x in s["blocking_crosschecks"]:
        out.append(f"  {x['competition']} {x['season']}: {', '.join(x['failed_checks'])} below "
                   f"{x['min_agreement']:.0%}  -> review, then: python -m app.cli crosscheck-accept {x['id']} "
                   "--note \"...\"")

    out += ["", f"LATEST RUNS ({len(s['runs'])})"]
    for r in s["runs"]:
        out.append(f"  {r['started_at'][:16]}  {r['status']:<9} audit {r['audit_status'] or '-':<7} "
                   f"{r['requests_used']:>4} req  {r['job']}")
        for f in r["findings"]:
            out.append(f"      {f['level']:<7} {f['code']}: {f['message']}")

    o = s["open"]
    out += ["", "OPEN ITEMS", f"  team names to review: {o['open_reviews']}"]
    out += [f"  {i['severity']:<8} {i['issue_code']}: {i['n']}" for i in o["open_issues"]] or ["  no open data issues"]
    if quota is not None:
        used, limit = quota
        out += ["", f"API-FOOTBALL QUOTA TODAY: {used} / {limit} used, {limit - used} left"]
    return "\n".join(out)
