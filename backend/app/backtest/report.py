"""Backtest report (DEVELOPMENT-PLAN.md 4.2-4.6, Gate B). Pure: BacktestRun -> summary -> Markdown / CSV.

What is measured
    calibration    every PRICED fixture with a known 15' result (the model, independent of the bet filter),
                   and the LISTED (qualified) fixtures separately; 2-point buckets, Brier, log loss
    models         Final price, Model A, Model B v2, Model C, competition-only baseline - on the SAME fixtures
    min-odds ROI   every listed fixture settled at its own minimum odds, per market: the report-mode promise
                   ("bet only at or above these odds") checked against what happened. Expected: about +EV_req
                   if the probabilities are right
    est. value     data doc 9 scenario (needs main-market odds): assumed bookmaker 15' price from Model C
    periods        optional split date: tuning (before) and validation (from the split, unseen)

Gate B (each PASS / FAIL / NOT_EVALUABLE; the gate passes only if all four pass)
    1  >= gate_b.min_qualified listed selections with a known result
    2  calibration: |actual - predicted| <= tolerance in every bucket with >= min_bucket_matches fixtures
    3  final price beats the competition-only baseline (lower Brier, same fixtures)
    4  estimated ROI > 0 on the validation period (estimated-value scenario)
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.backtest.config import BacktestConfig
from app.backtest.runner import BacktestRow, BacktestRun
from app.calculation.ev import Bet
from app.domain.enums import Decision
from app.settlement.performance import Bucket, Pair, Roi, Score, calibration, roi, score
from app.settlement.settle import Settlement, settle

PASS, FAIL, NOT_EVALUABLE = "PASS", "FAIL", "NOT_EVALUABLE"
_MODELS: tuple[tuple[str, Callable[[BacktestRow], float | None]], ...] = (
    ("Final price", lambda r: r.p_under05), ("Model A", lambda r: r.p_model_a),
    ("Model B v2", lambda r: r.p_model_b2), ("Competition only", lambda r: r.p_league_00))


def _outcome(r: BacktestRow, bet: Bet) -> bool | None:
    return r.result.outcome(bet) if r.result is not None else None


def _pairs(rows: Iterable[BacktestRow], p: Callable[[BacktestRow], float | None], bet: Bet) -> list[Pair]:
    out = []
    for r in rows:
        prob, y = p(r), _outcome(r, bet)
        if prob is not None and y is not None:
            out.append((prob, y))
    return out


def _period(r: BacktestRow, split: date | None) -> str:
    if split is None:
        return "all"
    return "validation" if r.session.day >= split else "tuning"


@dataclass(frozen=True)
class Criterion:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class Summary:
    run: BacktestRun
    split: date | None
    decisions: dict[str, int]
    decisions_by_competition: dict[str, dict[str, int]]
    blocking: list[tuple[str, int]]
    qualified_per_session: tuple[float, int]  # (mean, max) over sessions with fixtures
    over_cap: int
    calibration: dict[str, dict[str, tuple[Score | None, list[Bucket]]]]  # market -> set -> (score, buckets)
    models: list[tuple[str, Score | None]]  # Under 0.5, same fixtures
    models_n: int
    by_competition: list[tuple[str, Score | None, Score | None]]  # listed: final, competition-only
    by_grade: list[tuple[str, Score | None]]
    min_odds_roi: dict[str, dict[str, Roi]]  # period -> market -> ROI
    estimated: dict[str, Roi]  # period -> ROI
    with_market: int
    gate: list[Criterion]

    @property
    def gate_passed(self) -> bool:
        return all(c.status == PASS for c in self.gate)


def _min_odds_settlements(rows: Iterable[BacktestRow], bet: Bet, stake: float) -> list[Settlement]:
    out = []
    for r in rows:
        odds = r.min_odds_under05 if bet is Bet.UNDER05 else r.min_odds_draw
        if r.result is not None and odds is not None:
            out.append(settle(bet, r.result, odds, stake))
    return out


def _gate(listed: Sequence[BacktestRow], buckets: list[Bucket], models: list[tuple[str, Score | None]],
          estimated: dict[str, Roi], split: date | None, cfg: BacktestConfig) -> list[Criterion]:
    gate: list[Criterion] = []
    known = sum(_outcome(r, Bet.UNDER05) is not None for r in listed)
    need = cfg.gate_b.min_qualified
    gate.append(Criterion("Qualified selections", PASS if known >= need else FAIL, f"{known} (need >= {need})"))

    c = cfg.calibration
    judged = [b for b in buckets if b.n >= c.min_bucket_matches]
    if not judged:
        gate.append(Criterion("Calibration", NOT_EVALUABLE, f"no bucket with >= {c.min_bucket_matches} fixtures"))
    else:
        worst = max(judged, key=lambda b: abs(b.gap_pts))
        ok = abs(worst.gap_pts) <= c.tolerance_pts
        gate.append(Criterion("Calibration", PASS if ok else FAIL,
                              f"{len(judged)} buckets judged; largest gap {worst.gap_pts:+.1f} pts in "
                              f"{worst.lo_pts:g}-{worst.hi_pts:g} % (n={worst.n}); tolerance {c.tolerance_pts:g}"))

    final, league = dict(models).get("Final price"), dict(models).get("Competition only")
    if final is None or league is None:
        gate.append(Criterion("Beats competition-only baseline", NOT_EVALUABLE, "no comparable fixtures"))
    else:
        gate.append(Criterion("Beats competition-only baseline", PASS if final.brier < league.brier else FAIL,
                              f"Brier {final.brier:.4f} vs {league.brier:.4f} (n={final.n})"))

    period = "validation" if split is not None else "all"
    est = estimated.get(period)
    if split is None:
        gate.append(Criterion("Estimated ROI on unseen period", NOT_EVALUABLE, "no validation split given"))
    elif est is None or est.bets == 0 or est.roi is None:
        gate.append(Criterion("Estimated ROI on unseen period", NOT_EVALUABLE,
                              "no estimated-value bets (needs main-market odds; see the report notes)"))
    else:
        gate.append(Criterion("Estimated ROI on unseen period", PASS if est.roi > 0 else FAIL,
                              f"{est.roi:+.1%} over {est.bets} bets"))
    return gate


def summarise(run: BacktestRun, cfg: BacktestConfig, split: date | None = None) -> Summary:
    rows = run.rows
    listed = [r for r in rows if r.decision is Decision.QUALIFIED]
    width = cfg.calibration.bucket_width_pts
    stake = cfg.estimated_value.stake

    cal: dict[str, dict[str, tuple[Score | None, list[Bucket]]]] = {}
    for bet, p in ((Bet.UNDER05, lambda r: r.p_under05), (Bet.DRAW, lambda r: r.p_draw)):
        cal[bet.value] = {}
        for name, subset in (("priced", rows), ("listed", listed)):
            pairs = _pairs(subset, p, bet)
            cal[bet.value][name] = (score(pairs), calibration(pairs, width))

    common = [r for r in rows if _outcome(r, Bet.UNDER05) is not None
              and all(f(r) is not None for _, f in _MODELS)]
    models = [(name, score(_pairs(common, f, Bet.UNDER05))) for name, f in _MODELS]
    market_rows = [r for r in common if r.p_market is not None]
    if market_rows:
        models.append((f"Model C (n={len(market_rows)})", score(_pairs(market_rows, lambda r: r.p_market,
                                                                         Bet.UNDER05))))

    by_comp = []
    for code in sorted({r.competition for r in listed}):
        sub = [r for r in listed if r.competition == code]
        by_comp.append((code, score(_pairs(sub, lambda r: r.p_under05, Bet.UNDER05)),
                        score(_pairs(sub, lambda r: r.p_league_00, Bet.UNDER05))))
    by_grade = [(g, score(_pairs([r for r in listed if r.grade.value == g], lambda r: r.p_under05, Bet.UNDER05)))
                for g in sorted({r.grade.value for r in listed})]

    periods = ["all"] + (["tuning", "validation"] if split is not None else [])
    min_odds: dict[str, dict[str, Roi]] = {}
    estimated: dict[str, Roi] = {}
    for period in periods:
        sub = [r for r in listed if period == "all" or _period(r, split) == period]
        min_odds[period] = {b.value: roi(_min_odds_settlements(sub, b, stake)) for b in (Bet.UNDER05, Bet.DRAW)}
        est = [settle(r.estimated.bet, r.result, r.estimated.odds, stake) for r in sub
               if r.estimated is not None and r.result is not None]
        estimated[period] = roi(est)

    active = [s for s in run.sessions if s.fixtures]
    qualified = [s.qualified for s in active]
    return Summary(
        run=run, split=split,
        decisions=dict(Counter(r.decision.value for r in rows)),
        decisions_by_competition={c: dict(Counter(r.decision.value for r in rows if r.competition == c))
                                  for c in sorted({r.competition for r in rows})},
        blocking=Counter(code for r in rows for code in r.failed).most_common(10),
        qualified_per_session=(sum(qualified) / len(qualified) if qualified else 0.0, max(qualified, default=0)),
        over_cap=sum(r.decision is Decision.QUALIFIED_OVER_CAP for r in rows),
        calibration=cal, models=models, models_n=len(common), by_competition=by_comp, by_grade=by_grade,
        min_odds_roi=min_odds, estimated=estimated, with_market=sum(r.p_market is not None for r in rows),
        gate=_gate(listed, cal[Bet.UNDER05.value]["priced"][1], models, estimated, split, cfg),
    )


# --- output ------------------------------------------------------------------------------------------------


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:.1%}"


def _score_cells(s: Score | None) -> str:
    if s is None:
        return "0 | — | — | — | —"
    return f"{s.n} | {_pct(s.mean_p)} | {_pct(s.actual)} | {s.brier:.4f} | {s.log_loss:.4f}"


def _roi_cells(r: Roi) -> str:
    return (f"{r.bets} | {_pct(r.hit_rate)} | {_pct(r.break_even)} | {r.profit:+.2f} | "
            f"{'—' if r.roi is None else f'{r.roi:+.1%}'}")


def render_markdown(s: Summary, cfg: BacktestConfig) -> str:
    run = s.run
    c = cfg.calibration
    out = [f"# Backtest {run.first} - {run.last}", "",
           f"params_version **{run.params_version}**, formula {run.formula_version}; competitions "
           f"{', '.join(run.competitions)}; {len(run.rows)} fixtures in {sum(1 for x in run.sessions if x.fixtures)} "
           f"sessions" + (f"; validation from **{s.split}**" if s.split else ""), "",
           "## Gate B", "", "| Criterion | Status | Detail |", "|---|---|---|"]
    out += [f"| {g.name} | **{g.status}** | {g.detail} |" for g in s.gate]
    out += ["", f"Gate B: **{'PASSED' if s.gate_passed else 'NOT PASSED'}**", "",
            "## Decisions", "", "| Competition | " + " | ".join(d.value for d in Decision) + " |",
            "|---|" + "---|" * len(Decision)]
    for comp, counts in [*s.decisions_by_competition.items(), ("**All**", s.decisions)]:
        out.append(f"| {comp} | " + " | ".join(str(counts.get(d.value, 0)) for d in Decision) + " |")
    mean, mx = s.qualified_per_session
    out += ["", f"Qualified per session: mean {mean:.1f}, max {mx}; over the cap: {s.over_cap}.", "",
            "Most frequent blocking checks: " + ", ".join(f"{code} ({n})" for code, n in s.blocking), ""]

    out += ["## Calibration", "",
            f"Buckets of {c.bucket_width_pts:g} pts; judged when n >= {c.min_bucket_matches} "
            f"(tolerance ±{c.tolerance_pts:g} pts). SE = binomial standard error of the actual rate.", ""]
    for market, label in ((Bet.UNDER05.value, "Under 0.5 (0-0 at 15')"), (Bet.DRAW.value, "Draw at 15'")):
        for name in ("priced", "listed"):
            sc, buckets = s.calibration[market][name]
            out += [f"### {label} — {'all priced fixtures' if name == 'priced' else 'listed (qualified)'}", "",
                    "n | mean predicted | actual | Brier | log loss", "---|---|---|---|---", _score_cells(sc), ""]
            if buckets:
                out += ["| Bucket | n | Predicted | Actual | Gap (pts) | SE (pts) | Judged |",
                        "|---|---|---|---|---|---|---|"]
                out += [f"| {b.lo_pts:g}-{b.hi_pts:g} % | {b.n} | {_pct(b.mean_p)} | {_pct(b.actual)} | "
                        f"{b.gap_pts:+.1f} | {b.se_pts:.1f} | {'yes' if b.n >= c.min_bucket_matches else 'no'} |"
                        for b in buckets]
                out.append("")

    out += ["## Models (Under 0.5, same fixtures)", "", f"{s.models_n} fixtures with every model available.", "",
            "| Model | n | Mean predicted | Actual | Brier | Log loss |", "|---|---|---|---|---|---|"]
    out += [f"| {name} | {_score_cells(sc)} |" for name, sc in s.models]
    out += ["", "## Listed fixtures by competition and grade (Under 0.5)", "",
            "| Group | n | Mean predicted | Actual | Brier | Log loss | Brier, competition only |",
            "|---|---|---|---|---|---|---|"]
    out += [f"| {code} | {_score_cells(f)} | {'—' if lg is None else f'{lg.brier:.4f}'} |"
            for code, f, lg in s.by_competition]
    out += [f"| Grade {g} | {_score_cells(sc)} | |" for g, sc in s.by_grade]

    out += ["", "## ROI at the minimum odds (report mode)", "",
            "Every listed fixture settled at its own minimum odds, per market (the report never bets both).",
            "If the probabilities are right, ROI is about +EV_req; a negative ROI means the minimum odds are too low.",
            "", "| Period | Market | Bets | Hit rate | Break-even | Profit | ROI |", "|---|---|---|---|---|---|---|"]
    for period, markets in s.min_odds_roi.items():
        out += [f"| {period} | {m} | {_roi_cells(r)} |" for m, r in markets.items()]
    out += ["", "## Estimated value (data doc 9)", "",
            f"Fixtures with usable main-market odds (Model C): {s.with_market}. Assumed bookmaker 15' price = "
            f"Model C fair odds x (1 - {cfg.estimated_value.assumed_bookmaker_margin:.0%}).", "",
            "| Period | Bets | Hit rate | Break-even | Profit | ROI |", "|---|---|---|---|---|---|"]
    out += [f"| {period} | {_roi_cells(r)} |" for period, r in s.estimated.items()]
    out.append("")
    return "\n".join(out)


CSV_FIELDS = ("fixture_id", "competition", "season", "session", "as_of", "kickoff_utc", "decision", "grade",
              "p_under05", "p_draw", "p_model_a", "p_model_b2", "p_market", "p_market_draw", "p_league_00",
              "p_league_draw", "min_odds_under05", "min_odds_draw", "est_bet", "est_odds", "est_ev",
              "actual_under05", "actual_draw", "data_status", "failed", "error")


def _csv_row(r: BacktestRow) -> dict[str, Any]:
    e = r.estimated
    return {
        "fixture_id": r.fixture_id, "competition": r.competition, "season": r.season, "session": str(r.session),
        "as_of": r.as_of.isoformat(), "kickoff_utc": r.kickoff_utc.isoformat(), "decision": r.decision.value,
        "grade": r.grade.value, "p_under05": r.p_under05, "p_draw": r.p_draw, "p_model_a": r.p_model_a,
        "p_model_b2": r.p_model_b2, "p_market": r.p_market, "p_market_draw": r.p_market_draw,
        "p_league_00": r.p_league_00, "p_league_draw": r.p_league_draw, "min_odds_under05": r.min_odds_under05,
        "min_odds_draw": r.min_odds_draw, "est_bet": e.bet.value if e else None, "est_odds": e.odds if e else None,
        "est_ev": e.ev if e else None, "actual_under05": _outcome(r, Bet.UNDER05),
        "actual_draw": _outcome(r, Bet.DRAW), "data_status": r.result.data_status.value if r.result else "CONFLICT",
        "failed": ";".join(r.failed), "error": r.error,
    }


def rows_csv(rows: Iterable[BacktestRow]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(_csv_row(r))
    return buf.getvalue()


def summary_json(s: Summary) -> dict[str, Any]:
    def sc(x: Score | None) -> dict[str, Any] | None:
        return None if x is None else {"n": x.n, "mean_p": x.mean_p, "actual": x.actual, "brier": x.brier,
                                       "log_loss": x.log_loss}

    def ro(r: Roi) -> dict[str, Any]:
        return {"bets": r.bets, "wins": r.wins, "voids": r.voids, "pending": r.pending, "profit": r.profit,
                "roi": r.roi, "hit_rate": r.hit_rate, "break_even": r.break_even}

    run = s.run
    return {
        "params_version": run.params_version, "formula_version": run.formula_version,
        "first": run.first.isoformat(), "last": run.last.isoformat(), "competitions": list(run.competitions),
        "split": s.split.isoformat() if s.split else None, "fixtures": len(run.rows),
        "sessions": [{"id": str(x.id), "as_of": x.as_of.isoformat(), "fixtures": x.fixtures,
                      "qualified": x.qualified, "listed": x.listed} for x in run.sessions],
        "decisions": s.decisions, "decisions_by_competition": s.decisions_by_competition,
        "blocking": dict(s.blocking), "over_cap": s.over_cap,
        "calibration": {m: {k: {"score": sc(v[0]), "buckets": [
            {"lo_pts": b.lo_pts, "hi_pts": b.hi_pts, "n": b.n, "mean_p": b.mean_p, "actual": b.actual,
             "gap_pts": b.gap_pts, "se_pts": b.se_pts} for b in v[1]]} for k, v in d.items()}
            for m, d in s.calibration.items()},
        "models": {name: sc(x) for name, x in s.models},
        "min_odds_roi": {p: {m: ro(r) for m, r in d.items()} for p, d in s.min_odds_roi.items()},
        "estimated": {p: ro(r) for p, r in s.estimated.items()}, "with_market": s.with_market,
        "gate_b": [{"name": g.name, "status": g.status, "detail": g.detail} for g in s.gate],
        "gate_b_passed": s.gate_passed,
    }
