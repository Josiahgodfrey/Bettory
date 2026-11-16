"""Tuning report: every variation, the selection, the holdout (touched once), Gate B and the hypotheses. Pure."""

from __future__ import annotations

import csv
import io
from typing import Any

from app.backtest.config import BacktestConfig
from app.backtest.report import PASS, Summary
from app.backtest.tuning import PAPER_BETTING, TuningResult
from app.settlement.statistics import Interval


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:.1%}"


def _ci(i: Interval | None, fmt: str = "+.4f") -> str:
    return "—" if i is None else f"{i.estimate:{fmt}} [{i.low:{fmt}}, {i.high:{fmt}}] ({i.blocks} sessions)"


def _holdout_cells(s: Summary) -> str:
    final = next((q for q in s.quality if q.name == "Final price" and q.market == "UNDER05"), None)
    if final is None:
        return "— | — | — | — | — | —"
    rc = final.recalibration
    return (f"{final.score.n} | {_pct(final.score.mean_p)} | {_pct(final.score.actual)} | {final.score.brier:.4f} | "
            f"{final.score.log_loss:.4f} | {'—' if rc is None else f'{rc.slope:.2f}'}")


def render_tuning(t: TuningResult, cfg: BacktestConfig) -> str:
    plan, sel = t.plan, t.selection_text()
    title = "DRY RUN - " if t.dry_run else ""
    out = [f"# {title}Tuning run: {t.plan_name}", "",
           f"Plan {plan.plan_version} registered {plan.registered_on}, sha256 `{t.plan_sha256[:16]}`; base params "
           f"{plan.base_params} = {t.base.id} ({t.base.name}); competitions {', '.join(plan.competitions)}.", "",
           f"Tuning {t.tuning.first} .. {t.tuning.last}; holdout {t.holdout.first} .. {t.holdout.last} (touched once)."]
    if t.dry_run:
        out += ["", "**Dry run**: the plan was not locked and no params file was written. Results on overridden or "
                "incomplete periods are a test of the tool, not evidence."]
    out += ["", "## Variations (all of them, grid order)", "",
            f"Selection metric: **{plan.selection.metric.value}** of {plan.selection.market.value} on the "
            f"{t.scores[0].n} fixtures priced in every variation (lower is better).", "",
            "| Id | Variation | n | Metric | vs base | Log loss | Brier | Mean predicted | Actual | Listed | Group |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    base_metric = next(s.metric for s in t.scores if s.variation.is_base)
    for s in t.scores:
        mark = " **(selected)**" if s.variation.id == t.selected.id else (" (base)" if s.variation.is_base else "")
        out.append(f"| {s.variation.id}{mark} | {s.variation.name} | {s.n} | {s.metric:.5f} | "
                   f"{s.metric - base_metric:+.5f} | {s.log_loss:.5f} | {s.brier:.5f} | {_pct(s.mean_p)} | "
                   f"{_pct(s.actual)} | {s.listed} | {s.group} |")
    out += ["", f"Variations run: {len(t.scores)}; **effective trials** (distinct predictions): {t.effective_trials}.",
            "Not testable (options never changed a prediction): "
            + (", ".join(t.untestable) if t.untestable else "none") + ".", "", "## Selection", "", sel, ""]

    a, b, n = t.selected_minus_base
    out += ["## Holdout (touched once)", "",
            "| Params | n | Mean predicted | Actual | Brier | Log loss | Calibration slope |",
            "|---|---|---|---|---|---|---|",
            f"| selected {t.selected.id} | {_holdout_cells(t.holdout_selected)} |"]
    if t.holdout_base is not None:
        out.append(f"| base {t.base.id} | {_holdout_cells(t.holdout_base)} |")
        out += ["", f"Selected minus base, per match, {n} fixtures (negative = selected better): Brier {_ci(a)}; "
                f"log loss {_ci(b)}."]
    else:
        out += ["", "The selected variation is the base: params stay as they are."]

    out += ["", "## Gate B (plan rules, holdout, selected variation)", "", "| Criterion | Status | Detail |",
            "|---|---|---|"]
    out += [f"| {g.name} | **{g.status}** | {g.detail} |" for g in t.gate]
    out += ["", f"Gate B: **{'PASSED' if t.gate_passed else 'NOT PASSED'}** (profit is decided in paper betting).", "",
            "## Pre-registered hypotheses (base params)", "",
            "| Id | Period | n | Actual - predicted | Status | If confirmed |", "|---|---|---|---|---|---|"]
    for h in t.hypotheses:
        out.append(f"| {h.hypothesis.id} | {h.period} | {h.n} | {_ci(h.gap, '+.3f')} | **{h.status}** | "
                   f"{h.hypothesis.action_if_confirmed} |")
    out += ["", "## Output", "",
            f"New params file: `{t.params_file.name}` (adopting it is a separate decision)." if t.params_file else
            ("None (dry run)." if t.dry_run else "None: the base parameters were selected."), ""]
    return "\n".join(out)


def tuning_json(t: TuningResult) -> dict[str, Any]:
    def iv(i: Interval | None) -> dict[str, Any] | None:
        return None if i is None else {"estimate": i.estimate, "low": i.low, "high": i.high, "blocks": i.blocks}

    a, b, n = t.selected_minus_base
    return {
        "plan": t.plan_name, "plan_sha256": t.plan_sha256, "dry_run": t.dry_run,
        "tuning": {"first": t.tuning.first.isoformat(), "last": t.tuning.last.isoformat()},
        "holdout": {"first": t.holdout.first.isoformat(), "last": t.holdout.last.isoformat()},
        "base": t.base.id, "selected": t.selected.id, "tied": t.tied,
        "effective_trials": t.effective_trials, "untestable": t.untestable,
        "variations": [{"id": s.variation.id, "labels": list(s.variation.labels), "n": s.n, "metric": s.metric,
                        "log_loss": s.log_loss, "brier": s.brier, "mean_p": s.mean_p, "actual": s.actual,
                        "listed": s.listed, "group": s.group} for s in t.scores],
        "holdout_selected_minus_base": {"brier": iv(a), "log_loss": iv(b), "n": n},
        "gate_b": [{"name": g.name, "status": g.status, "detail": g.detail} for g in t.gate],
        "gate_b_passed": t.gate_passed,
        "hypotheses": [{"id": h.hypothesis.id, "period": h.period, "n": h.n, "gap": iv(h.gap), "status": h.status}
                       for h in t.hypotheses],
        "params_file": t.params_file.name if t.params_file else None,
    }


def variations_csv(t: TuningResult) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["id", "labels", "n", "metric", "log_loss", "brier", "mean_p", "actual", "listed", "group",
                "selected", "base"])
    for s in t.scores:
        w.writerow([s.variation.id, " / ".join(s.variation.labels), s.n, f"{s.metric:.6f}", f"{s.log_loss:.6f}",
                    f"{s.brier:.6f}", f"{s.mean_p:.6f}", f"{s.actual:.6f}", s.listed, s.group,
                    s.variation.id == t.selected.id, s.variation.is_base])
    return buf.getvalue()


__all__ = ["PAPER_BETTING", "PASS", "render_tuning", "tuning_json", "variations_csv"]
