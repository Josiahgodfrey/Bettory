"""BETTORY command line.

Examples (from backend/, venv active):
  python -m app.cli ingest openfootball --comp ENG1 --season 2025-26 --full
  python -m app.cli ingest football-data-org --comp ENG1 --season 2025-26
  python -m app.cli ingest football-data-co-uk --comp ENG1 --season 2025-26
  python -m app.cli ingest api-football-season --comp ENG1 --season 2024-25
  python -m app.cli ingest api-football-day --date 2026-11-14 --events 20 --odds 20
  python -m app.cli review list
  python -m app.cli review resolve <review_id> <team_id>
  python -m app.cli crosscheck --comp ENG1 --season 2025-26 --show 10
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from uuid import UUID

from app.core.settings import BACKEND_DIR, get_settings
from app.domain.params import load_params
from app.domain.registry import load_registry
from app.ingestion import pipeline
from app.normalisation.crosscheck import cross_check
from app.storage.db import get_engine
from app.storage.raw_store import store_from_settings
from app.storage.repositories import matches as mrepo
from app.storage.repositories import reference as ref


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bettory")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ingest = sub.add_parser("ingest").add_subparsers(dest="source", required=True)
    for name in ("openfootball", "football-data-org", "football-data-co-uk", "api-football-season"):
        p = ingest.add_parser(name)
        p.add_argument("--comp", required=True)
        p.add_argument("--season", required=True)
        p.add_argument("--reparse", action="store_true")
        if name == "openfootball":
            p.add_argument("--full", action="store_true", help="use the -full file (goal minutes)")
    day = ingest.add_parser("api-football-day")
    day.add_argument("--date", required=True, type=date.fromisoformat)
    day.add_argument("--events", type=int, default=20, help="max requests for goal minutes")
    day.add_argument("--odds", type=int, default=20, help="max requests for odds")
    day.add_argument("--reparse", action="store_true")

    review = sub.add_parser("review").add_subparsers(dest="action", required=True)
    review.add_parser("list")
    res = review.add_parser("resolve")
    res.add_argument("review_id", type=UUID)
    res.add_argument("team_id", type=UUID)

    cc = sub.add_parser("crosscheck", help="agreement between sources (FT, HT, 15')")
    cc.add_argument("--comp", required=True)
    cc.add_argument("--season", required=True)
    cc.add_argument("--show", type=int, default=10, help="disagreements to list")

    args = parser.parse_args(argv)
    settings, engine, registry = get_settings(), get_engine(), load_registry()

    if args.cmd == "crosscheck":
        cutoff = load_params(BACKEND_DIR / settings.params_file).settlement.cutoff_label_minute
        with engine.connect() as conn:
            ids = mrepo.season_match_ids(conn, args.comp, args.season)
            report = cross_check(((str(i), mrepo.latest_records(conn, i)) for i in ids), cutoff)
        print(f"{args.comp} {args.season}: {report.matches} matches, {report.multi_source} with 2+ sources")
        rows = (("FT", report.ft), ("HT", report.ht), ("15'", report.h15), ("15' vs other HT", report.h15_vs_ht))
        for name, a in rows:
            rate = "n/a (fewer than 2 sources)" if a.rate is None else f"{a.rate:.1%}"
            print(f"  {name:<16} {a.agreed}/{a.compared} agree  {rate}")
        verdict = report.passes()
        print("  exit criterion (15' >= 98%):", "not measurable yet - needs 2 goal-minute sources" if verdict is None
              else ("PASS" if verdict else "FAIL"))
        for d in report.disagreements[:args.show]:
            print(f"  ! {d.kind} {d.match_key}: " + ", ".join(f"{s.value}={v}" for s, v in d.values))
        return 0

    if args.cmd == "review":
        with engine.begin() as conn:
            if args.action == "list":
                for row in ref.open_reviews(conn):
                    print(f"{row['id']}  {row['source']:<20} {row['provider_name']:<35} {row['context']}")
            else:
                ref.resolve_review(conn, args.review_id, args.team_id)
                print("resolved")
        return 0

    store = store_from_settings(settings)
    if args.source == "openfootball":
        stats = pipeline.openfootball(engine, store, registry, args.comp, args.season, args.full, args.reparse)
    elif args.source == "football-data-org":
        stats = pipeline.football_data_org(engine, store, registry, settings.football_data_org_key.get_secret_value(),
                                           args.comp, args.season, args.reparse)
    elif args.source == "football-data-co-uk":
        stats = pipeline.football_data_co_uk(engine, store, registry, args.comp, args.season, args.reparse)
    elif args.source == "api-football-season":
        stats = pipeline.api_football_season(engine, store, registry, settings.api_football_key.get_secret_value(),
                                             args.comp, args.season, args.reparse)
    else:
        stats = pipeline.api_football_day(engine, store, registry, settings.api_football_key.get_secret_value(),
                                          args.date, args.events, args.odds, args.reparse)
    print(pipeline.format_stats(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
