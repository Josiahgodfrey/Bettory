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
  python -m app.cli crosscheck-accept <crosscheck_id> --note "reviewed: ..."
  python -m app.cli health --runs 10 --quota
  python -m app.cli backfill-daily              # where every target stands (no request)
  python -m app.cli backfill-daily --run        # run today's backfill now
  python -m app.cli worker                      # start the scheduler (foreground)
  python -m app.cli tuning-plan                 # the pre-registered tuning plan (config/tuning_v1.yaml)
  python -m app.cli tune --tuning 2025-08-01 2026-01-31 --holdout 2026-02-01 2026-05-31   # dry run
  python -m app.cli tune --real                 # the real run: locks the plan, may write a new params file
  python -m app.cli backtest --from 2025-09-01 --to 2026-05-31 --comp ENG1 --comp GER1 --split 2026-02-01
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from app.backtest.config import load_backtest_config
from app.backtest.report import render_markdown, rows_csv, summarise, summary_json
from app.backtest.runner import SessionRun, run_backtest
from app.backtest.tuning import TuningNotReady, period_of, run_tuning
from app.backtest.tuning_plan import check_unchanged, current_plan_path, lineage, load_plan, sha256_of, variations
from app.backtest.tuning_report import render_tuning, tuning_json, variations_csv
from app.core.settings import BACKEND_DIR, get_settings
from app.domain.params import Params, load_params, params_path
from app.domain.quality_config import load_quality_config
from app.domain.registry import load_registry
from app.domain.sessions import SessionCalendar, SessionId
from app.ingestion import pipeline
from app.ingestion.api_football import ApiFootball
from app.ingestion.health_view import render_text, snapshot
from app.ingestion.quality import crosscheck_season
from app.jobs.backfill import load_backfill_config, plan, run_daily_backfill
from app.jobs.scheduler import build_scheduler
from app.paper.freeze import Provenance, due_sessions, freeze_session, replay_session
from app.storage.db import get_engine
from app.storage.raw_store import store_from_settings
from app.storage.repositories import health as health_repo
from app.storage.repositories import paper as paper_repo
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
    bf.add_argument("--skip-known", action="store_true",
                    help="do not fetch fixtures whose 15' result is already known (no second goal-minute source)")
    day = ingest.add_parser("api-football-day")
    day.add_argument("--date", required=True, type=date.fromisoformat)
    day.add_argument("--events", type=int, default=20, help="max requests for goal minutes")
    day.add_argument("--odds", type=int, default=20, help="max requests for odds")
    day.add_argument("--reparse", action="store_true")

    review = sub.add_parser("review").add_subparsers(dest="action", required=True)
    review.add_parser("list")
    res = review.add_parser("resolve")
    res.add_argument("review_id", type=UUID)
    res.add_argument("team_id", type=UUID, nargs="?", help="the existing team this name refers to")
    res.add_argument("--new-team", action="store_true",
                     help="the name is a club new to the database (e.g. promoted): create it")
    res.add_argument("--name", help="with --new-team: the team's name (default: the provider's name)")
    res.add_argument("--not-similar", action="store_true",
                     help="with --new-team: confirm it is a different club from the similar teams listed")

    cc = sub.add_parser("crosscheck", help="agreement between sources (FT, HT, 15')")
    cc.add_argument("--comp", required=True)
    cc.add_argument("--season", required=True)
    cc.add_argument("--show", type=int, default=10, help="disagreements to list")
    acc = sub.add_parser("crosscheck-accept", help="accept a FAILED cross-check after reviewing it")
    acc.add_argument("crosscheck_id", type=UUID)
    acc.add_argument("--note", required=True, help="what was reviewed and why the data is accepted")

    hp = sub.add_parser("health", help="coverage, cross-checks, run audits and open items")
    hp.add_argument("--runs", type=int, default=10, help="latest runs to show")
    hp.add_argument("--quota", action="store_true", help="also show the API-Football quota (not counted)")
    hp.add_argument("--json", action="store_true", help="print the snapshot as JSON")

    bd = sub.add_parser("backfill-daily", help="automatic API-Football backfill (config/backfill.yaml)")
    bd.add_argument("--run", action="store_true", help="run today's backfill now (otherwise: show the plan)")
    sub.add_parser("worker", help="start the scheduler in the foreground (Ctrl+C to stop)")
    tn = sub.add_parser("tune", help="run a pre-registered tuning plan (dry run unless --real)")
    tn.add_argument("--plan", type=Path, default=None, help="plan file (default: the newest registered plan)")
    tn.add_argument("--real", action="store_true",
                    help="the real run: locks the plan for good and may write a new params file")
    tn.add_argument("--tuning", nargs=2, type=date.fromisoformat, metavar=("FIRST", "LAST"),
                    help="dry run only: override the tuning period")
    tn.add_argument("--holdout", nargs=2, type=date.fromisoformat, metavar=("FIRST", "LAST"),
                    help="dry run only: override the holdout period")
    tn.add_argument("--out", type=Path, default=None, help="output folder (default: backtests/tuning_...)")
    tp = sub.add_parser("tuning-plan", help="show and validate a pre-registered tuning plan")
    tp.add_argument("--plan", type=Path, default=None, help="plan file (default: the newest registered plan)")

    pf = sub.add_parser("paper-freeze", help="freeze the predictions of due sessions before kick-off")
    pf.add_argument("--session", action="append", help="session id, e.g. 2026-11-18-NOON (repeatable; default: "
                    "every session that has not ended, yesterday .. tomorrow)")
    pf.add_argument("--params", action="append", help="params version (repeatable; default: the live params)")
    pv = sub.add_parser("paper-verify", help="check the hash chain of frozen predictions; --replay recomputes")
    pv.add_argument("--replay", action="append", metavar="SESSION",
                    help="recompute a frozen session and compare (repeatable)")
    pl = sub.add_parser("paper-list", help="frozen predictions of one session")
    pl.add_argument("session")

    bt = sub.add_parser("backtest", help="replay sessions point in time and report calibration / Gate B")
    bt.add_argument("--from", dest="first", required=True, type=date.fromisoformat, help="first session date")
    bt.add_argument("--to", dest="last", required=True, type=date.fromisoformat, help="last session date")
    bt.add_argument("--comp", action="append", help="competition code (repeatable; default: all registered)")
    bt.add_argument("--split", type=date.fromisoformat, default=None,
                    help="validation period starts on this session date (Gate B: unseen period)")
    bt.add_argument("--out", type=Path, default=None, help="output folder (default: backtests/<params>_<dates>)")

    args = parser.parse_args(argv)
    settings, engine, registry = get_settings(), get_engine(), load_registry()

    if args.cmd.startswith("paper-"):
        return _paper(args, engine, registry, load_params(BACKEND_DIR / settings.params_file))

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
        rules = load_quality_config().crosscheck
        with engine.begin() as conn:
            season_id = health_repo.season_id(conn, args.comp, args.season)
            if season_id is None:
                print(f"{args.comp} {args.season}: no such competition-season in the database", file=sys.stderr)
                return 1
            stored = crosscheck_season(conn, season_id, cutoff, rules)
        report, verdict = stored.report, stored.verdict
        print(f"{args.comp} {args.season}: {report.matches} matches, {report.multi_source} with 2+ sources")
        rows = (("FT", report.ft), ("HT", report.ht), ("15'", report.h15), ("15' vs other HT", report.h15_vs_ht))
        for name, a in rows:
            rate = "n/a (fewer than 2 sources)" if a.rate is None else f"{a.rate:.1%}"
            judged = "" if a.compared >= rules.min_compared else f"  (not judged: < {rules.min_compared} compared)"
            print(f"  {name:<16} {a.agreed}/{a.compared} agree  {rate}{judged}")
        print(f"  status: {verdict.status.value}" + (f" ({', '.join(verdict.failed)} below "
                                                     f"{rules.min_agreement:.0%})" if verdict.failed else ""))
        print("  15' results verified between two goal-minute sources:", "yes" if verdict.fifteen_verified else "no")
        if verdict.failed:
            print(f"  this season's matches cannot qualify until reviewed: "
                  f"python -m app.cli crosscheck-accept {stored.id} --note \"...\"")
        for d in report.disagreements[:args.show]:
            print(f"  ! {d.kind} {d.match_key}: " + ", ".join(f"{s.value}={v}" for s, v in d.values))
        print(f"  stored as {stored.id}")
        return 0

    if args.cmd == "crosscheck-accept":
        with engine.begin() as conn:
            health_repo.accept_crosscheck(conn, args.crosscheck_id, args.note)
        print("accepted")
        return 0

    if args.cmd == "tune":
        if args.real and (args.tuning or args.holdout):
            print("a real run uses the registered periods: --tuning / --holdout are for dry runs", file=sys.stderr)
            return 2
        plan_path = args.plan or current_plan_path()
        try:
            result = run_tuning(
                engine, registry, load_backtest_config(), plan_path, dry_run=not args.real,
                tuning_override=period_of(*args.tuning) if args.tuning else None,
                holdout_override=period_of(*args.holdout) if args.holdout else None,
                progress=lambda m: print(f"  {m}", file=sys.stderr))
        except TuningNotReady as e:
            print(f"NOT READY: {e}", file=sys.stderr)
            return 3
        cfg_bt = load_backtest_config()
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        kind = "real" if args.real else "dryrun"
        out = args.out or BACKEND_DIR / "backtests" / f"tuning_{plan_path.stem}_{kind}_{stamp}"
        out.mkdir(parents=True, exist_ok=True)
        text_report = render_tuning(result, cfg_bt)
        (out / "report.md").write_text(text_report, encoding="utf-8")
        (out / "summary.json").write_text(json.dumps(tuning_json(result), indent=2), encoding="utf-8")
        (out / "variations.csv").write_text(variations_csv(result), encoding="utf-8")
        (out / "holdout_selected.md").write_text(render_markdown(result.holdout_selected, cfg_bt), encoding="utf-8")
        if result.holdout_base is not None:
            (out / "holdout_base.md").write_text(render_markdown(result.holdout_base, cfg_bt), encoding="utf-8")
        print(text_report)
        print(f"written to {out}")
        return 0

    if args.cmd == "tuning-plan":
        path = args.plan or current_plan_path()
        tplan = load_plan(path)
        tplan.check_registry(registry)
        used = check_unchanged(path)
        base = load_params(BACKEND_DIR / "config" / f"params_v{tplan.base_params}.yaml")
        vs = variations(tplan, base)
        (base_v,) = [v for v in vs if v.is_base]
        print(f"{path.name}  plan {tplan.plan_version}, "
              f"registered {tplan.registered_on}, sha256 {sha256_of(path)[:16]}")
        print("  status: " + (f"LOCKED - used by a real run at {used}" if used else "not used yet (editable)"))
        print(f"  base params {tplan.base_params} = {base_v.id} ({base_v.name}); competitions "
              f"{', '.join(tplan.competitions)}")
        for name, older in lineage(path)[1:]:
            print(f"  supersedes {name} (v{older.plan_version}, {older.registered_on}): {tplan.change_reason}")
        pr = tplan.periods
        print(f"  tuning {pr.tuning.first} .. {pr.tuning.last}; holdout {pr.holdout.first} .. {pr.holdout.last} "
              f"(touched once; {tplan.holdout_status.value})")
        if pr.prospective is not None:
            print(f"  prospective {pr.prospective.first} .. {pr.prospective.last} (not yet played at registration)")
        sel = tplan.selection
        print(f"  selection: lowest {sel.metric.value} of {sel.market.value}; ties within {sel.tie_tolerance} -> "
              "fewest changes from the base")
        for dim in tplan.grid:
            print(f"  {dim.name}: " + ", ".join(o.label for o in dim.options))
        print(f"  {len(vs)} variations")
        for h in tplan.hypotheses:
            print(f"  {h.id}: {h.statement}")
        g = tplan.gate_b
        print(f"  Gate B (holdout): >= {g.min_qualified} listed; calibration {g.calibration}; baseline "
              f"{g.beats_baseline}; profit {g.profit}")
        return 0

    if args.cmd in ("backfill-daily", "worker"):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        backfill_cfg = load_backfill_config()
        backfill_cfg.check_registry(registry)
        if args.cmd == "worker":
            sched = build_scheduler(engine, lambda: store_from_settings(settings), registry, settings, backfill_cfg)
            for j in sched.get_jobs():
                print(f"scheduled: {j.name} ({j.trigger})")
            print("worker running - Ctrl+C to stop")
            try:
                sched.start()
            except (KeyboardInterrupt, SystemExit):
                print("worker stopped")
            return 0
        if args.run:
            outcome = run_daily_backfill(engine, lambda: store_from_settings(settings), registry, settings,
                                         backfill_cfg)
            print(json.dumps(outcome.as_dict(), indent=2))
            return 0
        sch = backfill_cfg.schedule
        print(f"daily at {sch.time} {sch.timezone}, at most "
              f"{backfill_cfg.daily_max_requests.for_plan(settings.api_football_plan)} requests "
              f"(plan setting: {settings.api_football_plan})")
        for st in plan(engine, backfill_cfg):
            left = "" if st.still_without_minutes is None else f", {st.still_without_minutes} still without minutes"
            print(f"  {st.target.competition} {st.target.season:<8} {st.target.mode.value:<10} {st.state.value}{left}")
        return 0

    if args.cmd == "health":
        with engine.connect() as conn:
            snap = snapshot(conn, args.runs)
        quota = None
        key = settings.api_football_key.get_secret_value()
        actual_plan = None
        if args.quota and key:
            af = ApiFootball(key, max_requests=1, requests_per_minute=settings.api_football_requests_per_minute)
            quota, actual_plan = af.quota(), af.subscription_plan
        if args.json:
            print(json.dumps({**snap, "quota": quota, "api_football_plan": actual_plan}, indent=2, default=str))
        else:
            print(render_text(snap, quota))
            if actual_plan is not None:
                setting = settings.api_football_plan
                mismatch = (actual_plan.strip().lower() == "free") != (setting == "free")
                print(f"API-FOOTBALL PLAN: {actual_plan} (setting API_FOOTBALL_PLAN={setting})"
                      + ("  <- MISMATCH: change the setting in .env" if mismatch else ""))
        return 0

    if args.cmd == "review":
        with engine.begin() as conn:
            if args.action == "list":
                for row in ref.open_reviews(conn):
                    print(f"{row['id']}  {row['source']:<20} {row['provider_name']:<35} {row['context']}")
            elif args.new_team == (args.team_id is not None):
                print("give either a team id or --new-team", file=sys.stderr)
                return 2
            elif args.new_team:
                try:
                    new_id = ref.resolve_review_new_team(conn, args.review_id, args.name, args.not_similar)
                except ref.SimilarTeamsExist as e:
                    print(f"{e}.\nIf it really is another club: add --not-similar", file=sys.stderr)
                    return 2
                except ValueError as e:
                    print(e, file=sys.stderr)
                    return 2
                print(f"resolved: new team {new_id}. Link the waiting records with that source's ingest --reparse")
            else:
                ref.resolve_review(conn, args.review_id, args.team_id)
                print("resolved")
        return 0

    store = store_from_settings(settings)
    rpm = settings.api_football_requests_per_minute
    if args.source == "openfootball":
        stats = pipeline.openfootball(engine, store, registry, args.comp, args.season, args.full, args.reparse)
    elif args.source == "football-data-org":
        stats = pipeline.football_data_org(engine, store, registry, settings.football_data_org_key.get_secret_value(),
                                           args.comp, args.season, args.reparse)
    elif args.source == "football-data-co-uk":
        stats = pipeline.football_data_co_uk(engine, store, registry, args.comp, args.season, args.reparse)
    elif args.source == "api-football-season":
        stats = pipeline.api_football_season(engine, store, registry, settings.api_football_key.get_secret_value(),
                                             args.comp, args.season, args.reparse, requests_per_minute=rpm)
    elif args.source == "api-football-backfill":
        cap = settings.api_football_max_requests_per_run
        stats = pipeline.api_football_backfill(engine, store, registry, settings.api_football_key.get_secret_value(),
                                               args.comp, args.season, settings.api_football_plan == "pro",
                                               min(args.max_requests or cap, cap), args.reparse,
                                               requests_per_minute=rpm, skip_known_15=args.skip_known)
    else:
        stats = pipeline.api_football_day(engine, store, registry, settings.api_football_key.get_secret_value(),
                                          args.date, args.events, args.odds, args.reparse, requests_per_minute=rpm)
    print(pipeline.format_stats(stats))
    return 0


def _code_version() -> str | None:
    """Short git commit of the running code, '+dirty' when app/ or config/ has uncommitted changes."""
    try:
        head = subprocess.run(["git", "-C", str(BACKEND_DIR), "rev-parse", "--short=12", "HEAD"], check=True,
                              capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(BACKEND_DIR), "status", "--porcelain", "--", "app", "config"],
                               check=True, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return head + ("+dirty" if dirty else "")


def _paper(args: argparse.Namespace, engine: Any, registry: Any, live: Params) -> int:
    if args.cmd == "paper-freeze":
        versions = args.params or [live.params_version]
        plan_path = current_plan_path()
        prov = Provenance(plan_path.name, sha256_of(plan_path), _code_version())
        now = datetime.now(UTC)

        for version in versions:
            params = live if version == live.params_version else load_params(params_path(version))
            cal = SessionCalendar(params)
            sids = [SessionId.parse(x) for x in args.session] if args.session else due_sessions(cal, now)
            for sid in sids:
                r = freeze_session(engine, params, registry, sid, now=now, provenance=prov)
                line = f"{sid}  params {version}  {r.status}"
                if r.status == "TOO_EARLY":
                    line += f"  ({r.fixtures} fixtures; data cut-off {r.as_of:%Y-%m-%d %H:%M}Z: freeze after it)"
                elif r.status == "FROZEN":
                    line += (f"  as_of {r.as_of:%Y-%m-%d %H:%M}Z  fixtures {r.fixtures}  new {r.frozen}  "
                             f"already {r.already}  started {r.started}  listed {r.qualified}")
                    if r.chain_from is not None:
                        line += f"  chain {r.chain_from}..{r.chain_to}"
                print(line)
        return 0
    if args.cmd == "paper-verify":
        with engine.connect() as conn:
            chain = paper_repo.verify_chain(conn)
        print(f"frozen predictions: {chain.rows}")
        if chain.head_pos is not None:
            print(f"head: #{chain.head_pos} {chain.head_hash}  (note it down: a later head must extend this one)")
        for pos, problem in chain.problems:
            print(f"  ! #{pos}: {problem}")
        print("chain:", "INTACT" if chain.intact else "BROKEN")
        ok = chain.intact
        for sid in args.replay or []:
            with engine.connect() as conn:
                versions = sorted({r.params_version for r in paper_repo.frozen(conn, sid)})
            for version in versions:
                params = live if version == live.params_version else load_params(params_path(version))
                rr = replay_session(engine, params, registry, sid)
                print(f"replay {sid} params {version}: {rr.compared} rows, "
                      + ("IDENTICAL" if rr.identical else f"{len(rr.differences)} DIFFERENCES"))
                for d in rr.differences[:20]:
                    print(f"  ~ {d.match_id} {d.field}: frozen {d.frozen}  now {d.replayed}")
                ok = ok and rr.identical
        return 0 if ok else 4
    with engine.connect() as conn:  # paper-list
        rows = paper_repo.frozen(conn, args.session)
    for fp in rows:
        p = "-" if fp.p_under05 is None else f"{fp.p_under05:.3f}"
        print(f"#{fp.chain_pos:<6} {fp.kickoff_utc:%Y-%m-%d %H:%M}Z {fp.competition_code:<5} {fp.decision:<18} "
              f"{fp.grade:<5} P(U0.5) {p:<6} params {fp.params_version}  recorded {fp.recorded_at:%H:%M:%S}Z")
    print(f"{len(rows)} frozen predictions in {args.session}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
