"""Tuning run of a pre-registered plan (DEVELOPMENT-PLAN 4.5, docs/RESEARCH-PHASE4-TUNING.md §2, §8).

    1  real run: a plan edited after use is refused (tuning_plan.py)
    2  every variation through run_backtest on the TUNING period, sharing one ReadCache
    3  score each on the SAME fixtures (priced in every variation, result known) by the plan's metric;
       variations with identical predictions form one group: the effective number of trials, and dimensions whose
       options never change a prediction are NOT TESTABLE (e.g. the team/market weight without main-market odds).
       Nothing to score -> TuningNotReady with a per-season readiness report, and nothing is locked; otherwise a
       real run locks the plan here, before the holdout is touched
    4  select: lowest metric; within tie_tolerance of the best -> fewest changed dimensions from the base, then
       grid order
    5  HOLDOUT, touched once: the selected variation and the base params (one run if they are the same); paired
       selected-minus-base interval; Gate B by the plan's rules; the pre-registered hypotheses (base params, on the
       tuning period and on the holdout)
    6  output: report, every variation's scores, the holdout report; a new params file only on a real run whose
       selected variation differs from the base (never overwriting an existing file)

A dry run may override the periods (to exercise the tool on the data at hand) and never locks or writes params.
"""

from __future__ import annotations

import logging
import math
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import yaml
from sqlalchemy import Engine

from app.backtest.config import BacktestConfig
from app.backtest.report import FAIL, NOTE, PAPER_BETTING, PASS, Criterion, Summary, summarise
from app.backtest.runner import BacktestRow, BacktestRun, run_backtest
from app.backtest.tuning_plan import (
    LOCK_FILE,
    HoldoutStatus,
    Hypothesis,
    Metric,
    Period,
    TuningPlan,
    Variation,
    check_unchanged,
    lineage,
    load_plan,
    register_use,
    sha256_of,
    variations,
)
from app.calculation.ev import Bet
from app.domain.enums import Decision, Grade
from app.domain.params import CONFIG_DIR, Params, load_params, params_path
from app.domain.registry import Registry
from app.settlement.performance import score
from app.settlement.statistics import Interval, block_bootstrap
from app.storage.point_in_time import ReadCache

log = logging.getLogger(__name__)
CONFIRMED, NOT_CONFIRMED, TOO_FEW = "CONFIRMED", "NOT CONFIRMED", "TOO FEW SESSIONS"
_CLIP = 1e-12


def _p(r: BacktestRow, bet: Bet) -> float | None:
    return r.p_under05 if bet is Bet.UNDER05 else r.p_draw


def _y(r: BacktestRow, bet: Bet) -> bool | None:
    return r.result.outcome(bet) if r.result is not None else None


def _loss(p: float, y: bool) -> float:
    return -math.log(min(max(p if y else 1.0 - p, _CLIP), 1.0))


@dataclass(frozen=True)
class VariationScore:
    variation: Variation
    n: int  # fixtures scored (common to all variations)
    metric: float  # the plan's selection metric (lower is better)
    log_loss: float
    brier: float
    mean_p: float
    actual: float
    listed: int  # QUALIFIED (listed) fixtures in the tuning period
    group: int  # variations with identical predictions share a group


@dataclass(frozen=True)
class HypothesisResult:
    hypothesis: Hypothesis
    period: str  # "tuning" / "holdout"
    n: int
    gap: Interval | None  # actual - predicted, sessions resampled
    status: str


@dataclass(frozen=True)
class TuningResult:
    plan: TuningPlan
    plan_name: str
    plan_sha256: str
    dry_run: bool
    tuning: Period
    holdout: Period
    base: Variation
    scores: list[VariationScore]  # grid order
    effective_trials: int
    untestable: list[str]  # grid dimensions whose options never changed a prediction
    selected: Variation
    tied: list[str]  # ids within the tie tolerance of the best
    holdout_selected: Summary
    holdout_base: Summary | None  # None when the selected variation IS the base
    selected_minus_base: tuple[Interval | None, Interval | None, int]  # Brier, log loss, n (holdout)
    gate: list[Criterion]
    hypotheses: list[HypothesisResult]
    params_file: Path | None
    prospective: Prospective | None = None
    lineage: tuple[tuple[str, int, str, str | None], ...] = ()  # (file, version, registered, change reason)

    @property
    def gate_passed(self) -> bool:
        return all(c.status == PASS for c in self.gate if c.status not in (PAPER_BETTING, NOTE))

    def selection_text(self) -> str:
        sel = self.plan.selection
        best = min(s.metric for s in self.scores)
        chosen = next(s for s in self.scores if s.variation.id == self.selected.id)
        tie = (f"{len(self.tied)} variations within {sel.tie_tolerance} of the best ({', '.join(self.tied)}); "
               "the one with the fewest changed dimensions from the base, then grid order, wins. "
               if len(self.tied) > 1 else "")
        return (f"Rule (pre-registered): lowest {sel.metric.value}; ties within {sel.tie_tolerance} -> fewest changes "
                f"from the base. Best {best:.5f}. {tie}Selected **{self.selected.id} ({self.selected.name})**, "
                f"{chosen.metric:.5f}, {self.selected.distance(self.base)} dimension(s) changed from the base.")


# --- scoring and selection -----------------------------------------------------------------------------------


class TuningNotReady(ValueError):
    """The registered tuning period has nothing to score yet: its seasons are not loaded, or have no earlier
    season to build ratings and the league baseline from. A real run locks nothing in this case."""


def readiness(run: BacktestRun, bet: Bet, competitions: Sequence[str]) -> list[str]:
    """One line per competition season in the run: fixtures, priced, known 15' results, most common failed checks."""
    by: dict[tuple[str, str], list[BacktestRow]] = defaultdict(list)
    for r in run.rows:
        by[(r.competition, r.season)].append(r)
    out = []
    for (comp, season), rows in sorted(by.items()):
        priced = sum(_p(r, bet) is not None for r in rows)
        known = sum(_y(r, bet) is not None for r in rows)
        missing = Counter(f for r in rows if _p(r, bet) is None for f in r.failed).most_common(3)
        out.append(f"{comp} {season}: {len(rows)} fixtures, {priced} priced, {known} with a known 15' result"
                   + (f"; most common failed checks: {', '.join(f'{k} ({n})' for k, n in missing)}"
                      if missing else ""))
    absent = sorted(set(competitions) - {c for c, _ in by})
    if absent:
        out.append(f"no fixture loaded in the period: {', '.join(absent)}")
    return out


def _score_variations(runs: dict[str, BacktestRun], vs: Sequence[Variation], metric: Metric,
                      bet: Bet) -> list[VariationScore]:
    probs = {v.id: {r.fixture_id: (p, y) for r in runs[v.id].rows
                    if (p := _p(r, bet)) is not None and (y := _y(r, bet)) is not None} for v in vs}
    common = sorted(set.intersection(*(set(d) for d in probs.values()))) if probs else []
    groups: dict[tuple[float, ...], int] = {}
    out = []
    for v in vs:
        pairs = [probs[v.id][k] for k in common]
        if not pairs:
            raise TuningNotReady("no fixture was priced with a known result in every variation: nothing to tune on")
        sc = score(pairs)
        assert sc is not None
        key = tuple(round(p, 12) for p, _ in pairs)
        group = groups.setdefault(key, len(groups) + 1)
        listed = sum(r.decision is Decision.QUALIFIED for r in runs[v.id].rows)
        out.append(VariationScore(v, sc.n, sc.log_loss if metric is Metric.LOG_LOSS else sc.brier, sc.log_loss,
                                  sc.brier, sc.mean_p, sc.actual, listed, group))
    return out


def _untestable(plan: TuningPlan, scores: Sequence[VariationScore]) -> list[str]:
    """Dimensions whose options give identical predictions for every combination of the other dimensions."""
    out = []
    for d_index, dim in enumerate(plan.grid):
        by_rest: dict[tuple[str, ...], set[int]] = defaultdict(set)
        for s in scores:
            rest = tuple(lab for i, lab in enumerate(s.variation.labels) if i != d_index)
            by_rest[rest].add(s.group)
        if all(len(g) == 1 for g in by_rest.values()):
            out.append(dim.name)
    return out


def select(scores: Sequence[VariationScore], base: Variation, tie_tolerance: float) -> tuple[Variation, list[str]]:
    best = min(s.metric for s in scores)
    tied = [s for s in scores if s.metric <= best + tie_tolerance]
    order = {s.variation.id: i for i, s in enumerate(scores)}
    winner = min(tied, key=lambda s: (s.variation.distance(base), order[s.variation.id]))
    return winner.variation, [s.variation.id for s in tied]


# --- holdout, gate, hypotheses ----------------------------------------------------------------------------------


def _paired_selected_base(sel: BacktestRun, base: BacktestRun, bet: Bet,
                          cfg: BacktestConfig) -> tuple[Interval | None, Interval | None, int]:
    b = {r.fixture_id: r for r in base.rows}
    brier: dict[str, list[tuple[float, float]]] = defaultdict(list)
    loss: dict[str, list[tuple[float, float]]] = defaultdict(list)
    n = 0
    for r in sel.rows:
        other = b.get(r.fixture_id)
        ps, y = _p(r, bet), _y(r, bet)
        pb = _p(other, bet) if other is not None else None
        if ps is None or pb is None or y is None:
            continue
        n += 1
        brier[str(r.session)].append(((ps - y) ** 2 - (pb - y) ** 2, 1.0))
        loss[str(r.session)].append((_loss(ps, y) - _loss(pb, y), 1.0))
    u = cfg.uncertainty
    return (block_bootstrap(list(brier.values()), u.bootstrap_samples, u.confidence, u.seed),
            block_bootstrap(list(loss.values()), u.bootstrap_samples, u.confidence, u.seed), n)


def plan_gate(plan: TuningPlan, holdout: Summary) -> list[Criterion]:
    """Gate B by the plan's rules on the holdout summary of the selected variation: the summary already applies the
    shared rules (report.py); the plan sets the minimum number of selections and the holdout's honesty note."""
    g = plan.gate_b
    known = sum(r.decision is Decision.QUALIFIED and _y(r, Bet.UNDER05) is not None for r in holdout.run.rows)
    out = [Criterion("Qualified selections", PASS if known >= g.min_qualified else FAIL,
                     f"{known} (need >= {g.min_qualified})")]
    out += [c for c in holdout.gate if c.name != "Qualified selections"]
    if plan.holdout_status is HoldoutStatus.EXAMINED:
        out.append(Criterion("Holdout status", NOTE, "examined before registration - weaker evidence; the decisive "
                             "unseen tests are the prospective period and paper betting"))
    return out


@dataclass(frozen=True)
class Prospective:
    """The period registered as not yet played: evaluated with whatever has been played and loaded so far."""

    first: date
    last: date  # last session date evaluated (min(registered last, today))
    status: str  # PENDING (nothing to evaluate yet) / EVALUATED
    selected: Summary | None
    base: Summary | None
    selected_minus_base: tuple[Interval | None, Interval | None, int] | None
    baseline: Criterion | None  # beats competition-only on the prospective fixtures (selected params)


def _prospective(plan: TuningPlan, selected: Variation, base: Variation, engine: Engine, registry: Registry,
                 cfg: BacktestConfig, today: date) -> Prospective | None:
    period = plan.periods.prospective
    if period is None:
        return None
    last = min(period.last, today)
    if last < period.first:
        return Prospective(period.first, last, "PENDING - the period has not started", None, None, None, None)
    cache = ReadCache()
    sel = run_backtest(engine, selected.params, registry, cfg, period.first, last, plan.competitions, cache=cache)
    priced = [r for r in sel.rows if r.p_under05 is not None and _y(r, Bet.UNDER05) is not None]
    if not priced:
        return Prospective(period.first, last, "PENDING - no priced fixture with a known result yet", None, None,
                           None, None)
    bas = sel if selected.is_base else run_backtest(engine, base.params, registry, cfg, period.first, last,
                                                    plan.competitions, cache=cache)
    sel_summary = summarise(sel, cfg)
    baseline = next(c for c in sel_summary.gate if c.name == "Beats competition-only baseline")
    return Prospective(period.first, last, f"EVALUATED - {len(priced)} fixtures so far", sel_summary,
                       None if selected.is_base else summarise(bas, cfg),
                       None if selected.is_base else _paired_selected_base(sel, bas, plan.selection.market, cfg),
                       baseline)


def _subset(rows: Sequence[BacktestRow], h: Hypothesis) -> list[BacktestRow]:
    s = h.subset
    out = []
    for r in rows:
        if s.listed and r.decision is not Decision.QUALIFIED:
            continue
        if s.grade is not None and r.grade is not Grade(s.grade):
            continue
        if s.p_under05_at_least is not None and (r.p_under05 is None or r.p_under05 < s.p_under05_at_least):
            continue
        out.append(r)
    return out


def evaluate_hypothesis(h: Hypothesis, rows: Sequence[BacktestRow], period: str,
                        cfg: BacktestConfig) -> HypothesisResult:
    """CONFIRMED when the whole interval of (actual - predicted) for P(Under 0.5) lies below 0."""
    blocks: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for r in _subset(rows, h):
        p, y = r.p_under05, _y(r, Bet.UNDER05)
        if p is not None and y is not None:
            blocks[str(r.session)].append((float(y) - p, 1.0))
    u = cfg.uncertainty
    n = sum(len(b) for b in blocks.values())
    gap = block_bootstrap(list(blocks.values()), u.bootstrap_samples, u.confidence, u.seed)
    too_few = gap is None or gap.blocks < u.min_sessions
    status = TOO_FEW if too_few else (CONFIRMED if gap is not None and gap.high < 0 else NOT_CONFIRMED)
    return HypothesisResult(h, period, n, gap, status)


# --- params file -----------------------------------------------------------------------------------------------


def next_version(version: str) -> str:
    major, minor = version.split(".")
    return f"{major}.{int(minor) + 1}"


def write_params(selected: Variation, base_version: str, plan_name: str, plan_sha: str,
                 directory: Path = CONFIG_DIR) -> Path:
    """A NEW params file for the selected variation; never overwrites."""
    version = next_version(base_version)
    path = directory / f"params_v{version}.yaml"
    if path.exists():
        raise FileExistsError(f"{path.name} already exists: params files are never overwritten")
    data = selected.params.model_dump(mode="json")
    data["params_version"] = version
    Params(**data)  # validate before writing
    changes = "\n".join(f"#   {k}: {v}" for k, v in selected.changes.items())
    header = (f"# BETTORY calculation parameters - params_version {version}\n"
              f"# Generated by the tuning run of {plan_name} (sha256 {plan_sha[:16]}): variation {selected.id} "
              f"({selected.name}), selected on the tuning period by the pre-registered rule.\n"
              f"# Based on params {base_version}; the tuned values:\n{changes}\n"
              "# Never edit a version that has produced results; adopting it is a separate decision "
              "(PARAMS_FILE in .env).\n\n")
    path.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    load_params.cache_clear()
    load_params(path)  # the file name / version check
    return path


# --- the run ---------------------------------------------------------------------------------------------------


def run_tuning(engine: Engine, registry: Registry, cfg: BacktestConfig, plan_path: Path, *, dry_run: bool,
               tuning_override: Period | None = None, holdout_override: Period | None = None,
               lock: Path = LOCK_FILE, params_dir: Path = CONFIG_DIR,
               progress: Callable[[str], None] | None = None, today: date | None = None) -> TuningResult:
    if (tuning_override or holdout_override) and not dry_run:
        raise ValueError("periods can only be overridden in a dry run: a real run uses the registered periods")
    say = progress or (lambda _m: None)
    plan = load_plan(plan_path)
    plan.check_registry(registry)
    sha = sha256_of(plan_path)
    if not dry_run:
        check_unchanged(plan_path, lock)  # refuse an edited plan before any work
    base_params = load_params(params_path(plan.base_params))
    vs = variations(plan, base_params)
    base = next(v for v in vs if v.is_base)
    tuning = tuning_override or plan.periods.tuning
    holdout = holdout_override or plan.periods.holdout
    if holdout.first <= tuning.last:
        raise ValueError("the holdout must start after the tuning period")
    bet = plan.selection.market

    cache = ReadCache()
    runs: dict[str, BacktestRun] = {}
    for v in vs:
        runs[v.id] = run_backtest(engine, v.params, registry, cfg, tuning.first, tuning.last, plan.competitions,
                                  cache=cache)
        priced = sum(_p(r, bet) is not None and _y(r, bet) is not None for r in runs[v.id].rows)
        say(f"tuning {v.id} {v.name}: {len(runs[v.id].rows)} fixtures, {priced} priced with a known result")
    try:
        scores = _score_variations(runs, vs, plan.selection.metric, bet)
    except TuningNotReady as e:
        lines = "\n".join(f"  {x}" for x in readiness(runs[base.id], bet, plan.competitions))
        raise TuningNotReady(
            f"{e}.\nTuning period {tuning.first} .. {tuning.last}, base params:\n{lines}\n"
            "Nothing was locked and the holdout was not touched. A competition's first loaded season cannot be "
            "priced (no earlier season for ratings and the league baseline); run again once the backfill has "
            "loaded the tuning seasons (python -m app.cli backfill-daily).") from None
    if not dry_run:
        register_use(plan_path, "tuning run", lock)  # locked once the tuning period scores, before the holdout
    selected, tied = select(scores, base, plan.selection.tie_tolerance)
    say(f"selected {selected.id} {selected.name}")

    hold_cache = ReadCache()
    hold_sel = run_backtest(engine, selected.params, registry, cfg, holdout.first, holdout.last, plan.competitions,
                            cache=hold_cache)
    hold_base = hold_sel if selected.is_base else run_backtest(
        engine, base.params, registry, cfg, holdout.first, holdout.last, plan.competitions, cache=hold_cache)
    say("holdout evaluated")
    sel_summary = summarise(hold_sel, cfg)
    base_summary = None if selected.is_base else summarise(hold_base, cfg)
    paired = _paired_selected_base(hold_sel, hold_base, bet, cfg)
    hypotheses = [evaluate_hypothesis(h, runs[base.id].rows, "tuning", cfg) for h in plan.hypotheses]
    hypotheses += [evaluate_hypothesis(h, hold_base.rows, "holdout", cfg) for h in plan.hypotheses]

    params_file = None
    if not dry_run and not selected.is_base:
        params_file = write_params(selected, plan.base_params, plan_path.name, sha, params_dir)
    return TuningResult(
        plan=plan, plan_name=plan_path.name, plan_sha256=sha, dry_run=dry_run, tuning=tuning, holdout=holdout,
        base=base, scores=scores, effective_trials=len({s.group for s in scores}),
        untestable=_untestable(plan, scores), selected=selected, tied=tied, holdout_selected=sel_summary,
        holdout_base=base_summary, selected_minus_base=paired, gate=plan_gate(plan, sel_summary),
        hypotheses=hypotheses, params_file=params_file,
        prospective=_prospective(plan, selected, base, engine, registry, cfg, today or datetime.now(UTC).date()),
        lineage=tuple((name, pl.plan_version, pl.registered_on.isoformat(), pl.change_reason)
                      for name, pl in lineage(plan_path)),
    )


def period_of(first: date, last: date) -> Period:
    return Period(first=first, last=last)
