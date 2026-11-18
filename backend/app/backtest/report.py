"""Backtest report (DEVELOPMENT-PLAN.md 4.2-4.6, Gate B). Pure: BacktestRun -> summary -> Markdown / CSV.

What is measured
    calibration    every PRICED fixture with a known 15' result (the model, independent of the bet filter),
                   and the LISTED (qualified) fixtures separately; 2-point buckets, Brier, log loss
    quality        per model: Brier = reliability - resolution + uncertainty, skill vs the competition-only
                   baseline, calibration-in-the-large z, Spiegelhalter z, recalibration slope / intercept
    paired         model minus competition-only, per match, with a confidence interval from resampling whole
                   sessions: "better" only when the interval excludes 0
    season shares  observed 15' share of goals per season (s = of full-time goals, r = of half-time goals)
    models         Final price, Model A, Model B v2, Model C, competition-only baseline - on the SAME fixtures
    min-odds ROI   every listed fixture settled at its own minimum odds, per market: the report-mode promise
                   ("bet only at or above these odds") checked against what happened. Expected: about +EV_req
                   if the probabilities are right
    est. value     data doc 9 scenario (needs main-market odds): assumed bookmaker 15' price from Model C
    periods        optional split date: tuning (before) and validation (from the split, unseen)

Gate B (owner decisions 2026-11-16; the same rules as the tuning report)
    1  >= gate_b.min_qualified listed selections with a known result
    2  calibration: |actual - predicted| <= max(tolerance_pts, noise_multiplier x SE) in every bucket with
       >= min_bucket_matches fixtures (SE: binomial standard error of the bucket's actual rate)
    3  beats the competition-only baseline: the paired Brier interval (sessions resampled) lies entirely below 0
    4  profit: PAPER BETTING - ROI at the minimum odds is shown with its interval as a consistency check only;
       profit is proven in paper betting, never by a historical number
The gate passes when 1-3 pass (statuses PAPER BETTING and NOTE never block).
"""

from __future__ import annotations

import csv
import io
import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.backtest.config import BacktestConfig, Calibration, Uncertainty
from app.backtest.runner import BacktestRow, BacktestRun
from app.calculation.ev import Bet
from app.domain.enums import BetOutcome, Decision
from app.settlement.performance import Bucket, Pair, Roi, Score, calibration, roi, score
from app.settlement.settle import Settlement, settle
from app.settlement.statistics import (
    Decomposition,
    Interval,
    Recalibration,
    block_bootstrap,
    calibration_in_the_large_z,
    decompose,
    recalibration,
    skill,
    spiegelhalter_z,
)
from app.storage.results import SeasonShare

PASS, FAIL, NOT_EVALUABLE = "PASS", "FAIL", "NOT_EVALUABLE"
PAPER_BETTING, NOTE = "PAPER BETTING", "NOTE"  # informative statuses: never block the gate
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
class ModelQuality:
    name: str
    market: str
    score: Score
    decomposition: Decomposition
    skill_vs_league: float | None
    citl_z: float | None
    spiegelhalter_z: float | None
    recalibration: Recalibration | None


@dataclass(frozen=True)
class Paired:
    name: str  # "<model> - Competition only"
    market: str
    n: int
    brier: Interval | None  # mean per-match difference: negative = the model is better
    log_loss: Interval | None


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
    quality: list[ModelQuality]
    paired: list[Paired]
    roi_interval: dict[str, dict[str, Interval | None]]  # period -> market -> ROI interval
    season_shares: tuple[SeasonShare, ...]

    @property
    def gate_passed(self) -> bool:
        return all(c.status == PASS for c in self.gate if c.status not in (PAPER_BETTING, NOTE))


def allowed_gap_pts(b: Bucket, c: Calibration) -> float:
    """Largest acceptable |actual - predicted| for this bucket: the tolerance, or the noise if larger."""
    return max(c.tolerance_pts, c.noise_multiplier * b.se_pts)


_LEAGUE = "Competition only"
_DRAW_MODELS: tuple[tuple[str, Callable[[BacktestRow], float | None]], ...] = (
    ("Final price", lambda r: r.p_draw), (_LEAGUE, lambda r: r.p_league_draw))
_CLIP = 1e-12


def _quality(name: str, market: str, pairs: list[Pair], league: list[Pair], width: float) -> ModelQuality | None:
    sc = score(pairs)
    if sc is None:
        return None
    ref = score(league)
    return ModelQuality(name, market, sc, decompose(pairs, width),
                        skill(sc.brier, ref.brier) if ref is not None else None,
                        calibration_in_the_large_z(pairs), spiegelhalter_z(pairs), recalibration(pairs))


def _loss(p: float, y: bool) -> float:
    return -math.log(min(max(p if y else 1.0 - p, _CLIP), 1.0))


def _paired(name: str, market: str, rows: Sequence[BacktestRow], model: Callable[[BacktestRow], float | None],
            league: Callable[[BacktestRow], float | None], bet: Bet, u: Uncertainty) -> Paired:
    brier: dict[str, list[tuple[float, float]]] = defaultdict(list)
    loss: dict[str, list[tuple[float, float]]] = defaultdict(list)
    n = 0
    for r in rows:
        pm, pl, y = model(r), league(r), _outcome(r, bet)
        if pm is None or pl is None or y is None:
            continue
        n += 1
        brier[str(r.session)].append(((pm - y) ** 2 - (pl - y) ** 2, 1.0))
        loss[str(r.session)].append((_loss(pm, y) - _loss(pl, y), 1.0))
    def interval(blocks: dict[str, list[tuple[float, float]]]) -> Interval | None:
        return block_bootstrap(list(blocks.values()), u.bootstrap_samples, u.confidence, u.seed)
    return Paired(f"{name} - {_LEAGUE}", market, n, interval(brier), interval(loss))


def _roi_interval(rows: Sequence[BacktestRow], bet: Bet, stake: float, u: Uncertainty) -> Interval | None:
    blocks: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for r in rows:
        odds = r.min_odds_under05 if bet is Bet.UNDER05 else r.min_odds_draw
        if r.result is None or odds is None:
            continue
        s = settle(bet, r.result, odds, stake)
        if s.outcome in (BetOutcome.WIN, BetOutcome.LOSE):
            blocks[str(r.session)].append((s.profit, s.stake))
    return block_bootstrap(list(blocks.values()), u.bootstrap_samples, u.confidence, u.seed)


def _min_odds_settlements(rows: Iterable[BacktestRow], bet: Bet, stake: float) -> list[Settlement]:
    out = []
    for r in rows:
        odds = r.min_odds_under05 if bet is Bet.UNDER05 else r.min_odds_draw
        if r.result is not None and odds is not None:
            out.append(settle(bet, r.result, odds, stake))
    return out


def profit_criterion(roi_: Roi, interval: Interval | None, label: str) -> Criterion:
    if roi_.roi is None:
        return Criterion("Profit", PAPER_BETTING, "no listed bets; profit is proven in paper betting")
    ci = "" if interval is None else f", interval [{interval.low:+.1%}, {interval.high:+.1%}]"
    return Criterion("Profit", PAPER_BETTING,
                     f"{label}: ROI at the minimum odds {roi_.roi:+.1%} over {roi_.bets} bets{ci} - consistency check "
                     "only; profit is proven in paper betting")


def calibration_criterion(buckets: Sequence[Bucket], c: Calibration) -> Criterion:
    """Gate B calibration (owner decision 2026-11-16): every bucket with >= min_bucket_matches fixtures within
    max(tolerance, noise_multiplier x SE)."""
    judged = [b for b in buckets if b.n >= c.min_bucket_matches]
    if not judged:
        return Criterion("Calibration", NOT_EVALUABLE, f"no bucket with >= {c.min_bucket_matches} fixtures")
    worst = max(judged, key=lambda b: abs(b.gap_pts) / allowed_gap_pts(b, c))
    allowed = allowed_gap_pts(worst, c)
    failing = sum(abs(b.gap_pts) > allowed_gap_pts(b, c) for b in judged)
    return Criterion("Calibration", PASS if failing == 0 else FAIL,
                     f"{len(judged)} buckets judged, {failing} outside; tightest: {worst.gap_pts:+.1f} pts "
                     f"in {worst.lo_pts:g}-{worst.hi_pts:g} % (n={worst.n}, allowed ±{allowed:.1f})")


def baseline_criterion(paired: Paired | None, u: Uncertainty) -> Criterion:
    ci = paired.brier if paired is not None else None
    if ci is None:
        return Criterion("Beats competition-only baseline", NOT_EVALUABLE, "no comparable fixtures")
    if ci.blocks < u.min_sessions:
        return Criterion("Beats competition-only baseline", NOT_EVALUABLE,
                         f"only {ci.blocks} sessions (need {u.min_sessions})")
    return Criterion("Beats competition-only baseline", PASS if ci.high < 0 else FAIL,
                     f"paired Brier difference {ci.estimate:+.4f} [{ci.low:+.4f}, {ci.high:+.4f}] over "
                     f"{paired.n if paired else 0} fixtures; PASS needs the whole interval below 0")


def _gate(listed: Sequence[BacktestRow], buckets: list[Bucket], split: date | None, cfg: BacktestConfig,
          paired: Paired | None, roi_: Roi, roi_ci: Interval | None) -> list[Criterion]:
    gate: list[Criterion] = []
    known = sum(_outcome(r, Bet.UNDER05) is not None for r in listed)
    need = cfg.gate_b.min_qualified
    gate.append(Criterion("Qualified selections", PASS if known >= need else FAIL, f"{known} (need >= {need})"))

    gate.append(calibration_criterion(buckets, cfg.calibration))
    gate.append(baseline_criterion(paired, cfg.uncertainty))
    gate.append(profit_criterion(roi_, roi_ci, "validation period" if split is not None else "whole period"))
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

    u = cfg.uncertainty
    league_pairs = _pairs(common, lambda r: r.p_league_00, Bet.UNDER05)
    quality = [q for name, f in _MODELS
               if (q := _quality(name, "UNDER05", _pairs(common, f, Bet.UNDER05), league_pairs, width)) is not None]
    if market_rows:
        q = _quality("Model C", "UNDER05", _pairs(market_rows, lambda r: r.p_market, Bet.UNDER05),
                     _pairs(market_rows, lambda r: r.p_league_00, Bet.UNDER05), width)
        if q is not None:
            quality.append(q)
    common_draw = [r for r in rows if _outcome(r, Bet.DRAW) is not None
                   and all(f(r) is not None for _, f in _DRAW_MODELS)]
    draw_league = _pairs(common_draw, lambda r: r.p_league_draw, Bet.DRAW)
    quality += [q for name, f in _DRAW_MODELS
                if (q := _quality(name, "DRAW", _pairs(common_draw, f, Bet.DRAW), draw_league, width)) is not None]
    paired = [_paired(name, "UNDER05", common, f, lambda r: r.p_league_00, Bet.UNDER05, u)
              for name, f in _MODELS if name != _LEAGUE]
    paired.append(_paired("Final price", "DRAW", common_draw, lambda r: r.p_draw, lambda r: r.p_league_draw,
                          Bet.DRAW, u))
    roi_ci: dict[str, dict[str, Interval | None]] = {}
    for period in periods:
        sub = [r for r in listed if period == "all" or _period(r, split) == period]
        roi_ci[period] = {b.value: _roi_interval(sub, b, stake, u) for b in (Bet.UNDER05, Bet.DRAW)}

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
        gate=_gate(listed, cal[Bet.UNDER05.value]["priced"][1], split, cfg, paired[0],
                   min_odds["validation" if split is not None else "all"][Bet.UNDER05.value],
                   roi_ci["validation" if split is not None else "all"][Bet.UNDER05.value]),
        quality=quality, paired=paired, roi_interval=roi_ci, season_shares=run.season_shares,
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


def _z(x: float | None) -> str:
    return "—" if x is None else f"{x:+.2f}"


def _ci(i: Interval | None, u: Uncertainty, fmt: str = "+.4f") -> str:
    if i is None:
        return "—"
    few = f" (only {i.blocks} sessions: unreliable)" if i.blocks < u.min_sessions else ""
    return f"{i.estimate:{fmt}} [{i.low:{fmt}}, {i.high:{fmt}}]{few}"


def _verdict(i: Interval | None, u: Uncertainty) -> str:
    if i is None:
        return "—"
    if i.blocks < u.min_sessions:
        return "too few sessions"
    if not i.excludes(0.0):
        return "not distinguishable"
    return "model better" if i.high < 0 else "model worse"


def _judged(b: Bucket, c: Calibration) -> str:
    if b.n < c.min_bucket_matches:
        return "no (small)"
    return "pass" if abs(b.gap_pts) <= allowed_gap_pts(b, c) else "**FAIL**"


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
            f"Buckets of {c.bucket_width_pts:g} pts; judged when n >= {c.min_bucket_matches}. Allowed gap = "
            f"max({c.tolerance_pts:g} pts, {c.noise_multiplier:g} x SE); SE = binomial standard error of the "
            "actual rate.", ""]
    for market, label in ((Bet.UNDER05.value, "Under 0.5 (0-0 at 15')"), (Bet.DRAW.value, "Draw at 15'")):
        for name in ("priced", "listed"):
            sc, buckets = s.calibration[market][name]
            out += [f"### {label} — {'all priced fixtures' if name == 'priced' else 'listed (qualified)'}", "",
                    "n | mean predicted | actual | Brier | log loss", "---|---|---|---|---", _score_cells(sc), ""]
            if buckets:
                out += ["| Bucket | n | Predicted | Actual | Gap (pts) | SE (pts) | Allowed (pts) | Judged |",
                        "|---|---|---|---|---|---|---|---|"]
                out += [f"| {b.lo_pts:g}-{b.hi_pts:g} % | {b.n} | {_pct(b.mean_p)} | {_pct(b.actual)} | "
                        f"{b.gap_pts:+.1f} | {b.se_pts:.1f} | ±{allowed_gap_pts(b, c):.1f} | "
                        f"{_judged(b, c)} |" for b in buckets]
                out.append("")

    out += ["## Models (Under 0.5, same fixtures)", "", f"{s.models_n} fixtures with every model available.", "",
            "| Model | n | Mean predicted | Actual | Brier | Log loss |", "|---|---|---|---|---|---|"]
    out += [f"| {name} | {_score_cells(sc)} |" for name, sc in s.models]
    u = cfg.uncertainty
    out += ["", "## Probability quality", "",
            "Brier = reliability - resolution + uncertainty (Murphy). Reliability: calibration penalty (lower is "
            "better). Resolution: how well the model separates matches (higher is better). Skill: 1 - Brier / "
            "competition-only Brier. z values are ~N(0,1) when calibrated (|z| > 1.96: evidence of miscalibration). "
            "Slope < 1: over-confident, > 1: under-confident.", "",
            "| Model | Market | n | Brier | Reliability | Resolution | Uncertainty | Skill | Mean z | "
            "Spiegelhalter z | Slope | Intercept |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for q in s.quality:
        d, rc = q.decomposition, q.recalibration
        out.append(f"| {q.name} | {q.market} | {q.score.n} | {d.brier:.4f} | {d.reliability:.5f} | "
                   f"{d.resolution:.5f} | {d.uncertainty:.4f} | "
                   f"{'—' if q.skill_vs_league is None else f'{q.skill_vs_league:+.2%}'} | {_z(q.citl_z)} | "
                   f"{_z(q.spiegelhalter_z)} | {'—' if rc is None else f'{rc.slope:.2f}'} | "
                   f"{'—' if rc is None else f'{rc.intercept:+.2f}'} |")
    out += ["", f"## Paired comparisons with the competition-only baseline ({u.confidence:.0%} intervals, "
            f"{u.bootstrap_samples} resamples of whole sessions)", "",
            "Per-match difference (model minus baseline): negative = the model is better.", "",
            "| Comparison | Market | n | Brier difference | Log-loss difference | Verdict (Brier) |",
            "|---|---|---|---|---|---|"]
    out += [f"| {p.name} | {p.market} | {p.n} | {_ci(p.brier, u)} | {_ci(p.log_loss, u)} | "
            f"{_verdict(p.brier, u)} |" for p in s.paired]
    if s.season_shares:
        out += ["", "## Observed 15' share of goals per season", "",
                "s = goals before 15' / full-time goals; r = goals before 15' / half-time goals (matches with a known "
                "15' result). Drift between seasons (e.g. after stoppage-time rule changes) matters for the baselines.",
                "", "| Competition | Season | Matches | s | r |", "|---|---|---|---|---|"]
        out += [f"| {x.competition} | {x.season} | {x.matches} | {_pct(x.s)} | {_pct(x.r)} |"
                for x in s.season_shares]

    out += ["", "## Listed fixtures by competition and grade (Under 0.5)", "",
            "| Group | n | Mean predicted | Actual | Brier | Log loss | Brier, competition only |",
            "|---|---|---|---|---|---|---|"]
    out += [f"| {code} | {_score_cells(f)} | {'—' if lg is None else f'{lg.brier:.4f}'} |"
            for code, f, lg in s.by_competition]
    out += [f"| Grade {g} | {_score_cells(sc)} | |" for g, sc in s.by_grade]

    out += ["", "## ROI at the minimum odds (report mode)", "",
            "Every listed fixture settled at its own minimum odds, per market (the report never bets both).",
            "If the probabilities are right, ROI is about +EV_req; a negative ROI means the minimum odds are too low.",
            f"Interval: {u.confidence:.0%}, resampling whole sessions.",
            "", "| Period | Market | Bets | Hit rate | Break-even | Profit | ROI | ROI interval |",
            "|---|---|---|---|---|---|---|---|"]
    for period, markets in s.min_odds_roi.items():
        out += [f"| {period} | {m} | {_roi_cells(r)} | "
                f"{'—' if (i := s.roi_interval[period][m]) is None else f'[{i.low:+.1%}, {i.high:+.1%}]'}"
                f"{'' if i is None or i.blocks >= u.min_sessions else f' (only {i.blocks} sessions: unreliable)'} |"
                for m, r in markets.items()]
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


def _iv(i: Interval | None) -> dict[str, Any] | None:
    return None if i is None else {"estimate": i.estimate, "low": i.low, "high": i.high, "blocks": i.blocks}


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
        "quality": [{"model": q.name, "market": q.market, "score": sc(q.score),
                     "reliability": q.decomposition.reliability, "resolution": q.decomposition.resolution,
                     "uncertainty": q.decomposition.uncertainty, "within_bin": q.decomposition.within_bin,
                     "skill_vs_league": q.skill_vs_league, "calibration_in_the_large_z": q.citl_z,
                     "spiegelhalter_z": q.spiegelhalter_z,
                     "recalibration": None if q.recalibration is None else {
                         "slope": q.recalibration.slope, "intercept": q.recalibration.intercept,
                         "converged": q.recalibration.converged}} for q in s.quality],
        "paired": [{"comparison": p.name, "market": p.market, "n": p.n, "brier": _iv(p.brier),
                    "log_loss": _iv(p.log_loss)} for p in s.paired],
        "roi_interval": {per: {m: _iv(i) for m, i in d.items()} for per, d in s.roi_interval.items()},
        "season_shares": [{"competition": x.competition, "season": x.season, "matches": x.matches, "s": x.s,
                           "r": x.r} for x in s.season_shares],
        "min_odds_roi": {p: {m: ro(r) for m, r in d.items()} for p, d in s.min_odds_roi.items()},
        "estimated": {p: ro(r) for p, r in s.estimated.items()}, "with_market": s.with_market,
        "gate_b": [{"name": g.name, "status": g.status, "detail": g.detail} for g in s.gate],
        "gate_b_passed": s.gate_passed,
    }
