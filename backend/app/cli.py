"""BETTORY command line.

Examples (from backend/, venv active):
  python -m app.cli ingest openfootball --comp ENG1 --season 2025-26 --full
  python -m app.cli ingest football-data-org --comp ENG1 --season 2025-26
  python -m app.cli ingest football-data-co-uk --comp ENG1 --season 2025-26
  python -m app.cli ingest api-football-season --comp ENG1 --season 2024-25
  python -m app.cli ingest api-football-backfill --comp ENG1 --season 2024-25 --max-requests 50
  python -m app.cli ingest api-football-day --date 2026-11-14 --events 20 --odds 20
  python -m app.cli review list
  python -m app.cli review resolve <review_id> <team_id>
  python -m app.cli crosscheck --comp ENG1 --season 2025-26 --show 10
  python -m app.cli backtest --from 2025-09-01 --to 2026-05-31 --comp ENG1 --comp GER1 --split 2026-02-01
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

from app.backtest.config import load_backtest_config
from app.backtest.report import render_markdown, rows_csv, summarise, summary_json
from app.backtest.runner import SessionRun, run_backtest
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
    bf = ingest.add_parser("api-football-backfill", help="season fixtures + goal minutes (resumable)")
    bf.add_argument("--comp", required=True)
    bf.add_argument("--season", required=True)
    bf.add_argument("--max-requests", type=int, default=None,
                    help="request cap for this run (default and maximum: API_FOOTBALL_MAX_REQUESTS_PER_RUN)")
    bf.add_argument("--reparse", action="store_true")
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

    bt = sub.add_parser("backtest", help="replay sessions point in time and report calibration / Gate B")
    bt.add_argument("--from", dest="first", required=True, type=date.fromisoformat, help="first session date")
    bt.add_argument("--to", dest="last", required=True, type=date.fromisoformat, help="last session date")
    bt.add_argument("--comp", action="append", help="competition code (repeatable; default: all registered)")
    bt.add_argument("--split", type=date.fromisoformat, default=None,
                    help="validation period starts on this session date (Gate B: unseen period)")
    bt.add_argument("--out", type=Path, default=None, help="output folder (default: backtests/<params>_<dates>)")

    args = parser.parse_args(argv)
    settings, engine, registry = get_settings(), get_engine(), load_registry()

    if args.cmd == "backtest":
        params, cfg = load_params(BACKEND_DIR / settings.params_file), load_backtest_config()

        def progress(s: SessionRun) -> None:
            print(f"  {s.id}  as_of {s.as_of:%Y-%m-%d %H:%M}Z  fixtures {s.fixtures:>3}  qualified {s.qualified:>3}",
                  file=sys.stderr)

        run = run_backtest(engine, params, registry, cfg, args.first, args.last, args.comp, progress)
        summary = summarise(run, cfg, args.split)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out = args.out or BACKEND_DIR / "backtests" / f"v{run.params_version}_{args.first}_{args.last}_{stamp}"
        out.mkdir(parents=True, exist_ok=True)
        text_report = render_markdown(summary, cfg)
        (out / "report.md").write_text(text_report, encoding="utf-8")
        (out / "rows.csv").write_text(rows_csv(run.rows), encoding="utf-8")
        (out / "summary.json").write_text(json.dumps(summary_json(summary), indent=2), encoding="utf-8")
        print(text_report)
        print(f"written to {out}")
        return 0

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
    elif args.source == "api-football-backfill":
        cap = settings.api_football_max_requests_per_run
        stats = pipeline.api_football_backfill(engine, store, registry, settings.api_football_key.get_secret_value(),
                                               args.comp, args.season, settings.api_football_plan == "pro",
                                               min(args.max_requests or cap, cap), args.reparse)
    else:
        stats = pipeline.api_football_day(engine, store, registry, settings.api_football_key.get_secret_value(),
                                          args.date, args.events, args.odds, args.reparse)
    print(pipeline.format_stats(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
