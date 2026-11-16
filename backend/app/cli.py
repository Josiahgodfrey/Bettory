"""BETTORY command line.

Examples (from backend/, venv active):
  python -m app.cli ingest openfootball --comp ENG1 --season 2025-26 --full
  python -m app.cli ingest football-data-org --comp ENG1 --season 2025-26
  python -m app.cli ingest football-data-co-uk --comp ENG1 --season 2025-26
  python -m app.cli ingest api-football-season --comp ENG1 --season 2024-25
  python -m app.cli ingest api-football-day --date 2026-11-14 --events 20 --odds 20
  python -m app.cli review list
  python -m app.cli review resolve <review_id> <team_id>
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from uuid import UUID

from app.core.settings import get_settings
from app.domain.registry import load_registry
from app.ingestion import pipeline
from app.storage.db import get_engine
from app.storage.raw_store import store_from_settings
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

    args = parser.parse_args(argv)
    settings, engine, registry = get_settings(), get_engine(), load_registry()

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
