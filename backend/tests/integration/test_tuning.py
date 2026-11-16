"""Tuning run on the synthetic league: scoring on common fixtures, effective trials, untestable dimensions, the
selection rule, the holdout touched once, the lock, the params file, and the report."""

from __future__ import annotations

import copy
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.backtest import tuning as tuning_module
from app.backtest.config import load_backtest_config
from app.backtest.tuning import PAPER_BETTING, run_tuning, select, write_params
from app.backtest.tuning_plan import load_plan, variations
from app.backtest.tuning_report import render_tuning, tuning_json, variations_csv
from app.domain.params import CONFIG_DIR, load_params, params_path
from tests.integration.conftest import ADMIN_URL
from tests.integration.test_backtest_runner import REGISTRY, league  # noqa: F401 - the synthetic league fixture

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")
CFG = load_backtest_config()
SHIPPED: dict[str, Any] = yaml.safe_load((CONFIG_DIR / "tuning_v1.yaml").read_text(encoding="utf-8"))


def small_plan(tmp_path: Path) -> Path:
    """2 x 2 grid on the synthetic 2025-26 season (no odds: the team/market weight cannot change anything)."""
    data = copy.deepcopy(SHIPPED)
    data["competitions"] = ["TST1"]
    data["periods"] = {"tuning": {"first": "2025-08-20", "last": "2025-09-10"},
                       "holdout": {"first": "2025-09-11", "last": "2025-10-05"}}
    data["grid"] = [
        {"name": "rating_shrinkage", "options": [
            {"label": "m20", "set": {"model_a.rating_prior_matches": {"FT": 20, "HT": 30}}},
            {"label": "m160", "set": {"model_a.rating_prior_matches": {"FT": 160, "HT": 240}}}]},
        {"name": "team_vs_market", "options": [
            {"label": "team1.0", "set": {"blend.team_weight": 1.0, "blend.market_weight": 0.0}},
            {"label": "team0.5", "set": {"blend.team_weight": 0.5, "blend.market_weight": 0.5}}]},
    ]
    path = tmp_path / "tuning_vtest.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_dry_run(league: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    engine, _, _ = league
    calls: list[tuple[date, date]] = []
    real = tuning_module.run_backtest

    def counting(*a: Any, **kw: Any) -> Any:
        calls.append((a[4], a[5]))
        return real(*a, **kw)

    monkeypatch.setattr(tuning_module, "run_backtest", counting)
    lock, params_dir = tmp_path / "lock.yaml", tmp_path / "params"
    params_dir.mkdir()
    t = run_tuning(engine, REGISTRY, CFG, small_plan(tmp_path), dry_run=True, lock=lock, params_dir=params_dir)

    assert len(t.scores) == 4 and len({s.n for s in t.scores}) == 1 and t.scores[0].n > 0   # same fixtures for all
    assert t.untestable == ["team_vs_market"] and t.effective_trials == 2                    # no odds in this league
    best = min(s.metric for s in t.scores)
    chosen = next(s for s in t.scores if s.variation.id == t.selected.id)
    assert chosen.metric <= best + t.plan.selection.tie_tolerance
    assert t.selected.labels[1] == "team0.5"                     # tied team options -> the base option wins
    holdout = [c for c in calls if c[0] == date(2025, 9, 11)]
    assert len(holdout) == (1 if t.selected.is_base else 2)      # the holdout is touched once per params
    assert not lock.exists() and list(params_dir.iterdir()) == [] and t.params_file is None
    assert {h.period for h in t.hypotheses} == {"tuning", "holdout"} and len(t.hypotheses) == 4
    assert [g.name for g in t.gate] == ["Qualified selections", "Calibration", "Beats competition-only baseline",
                                        "Profit"] and t.gate[-1].status == PAPER_BETTING
    md = render_tuning(t, CFG)
    assert md.startswith("# DRY RUN") and "effective trials" in md and "team_vs_market" in md
    js = tuning_json(t)
    assert js["dry_run"] and len(js["variations"]) == 4 and js["selected"] == t.selected.id
    assert len(variations_csv(t).splitlines()) == 5


def test_real_run_locks_the_plan_and_writes_params_only_if_changed(league: Any, tmp_path: Path) -> None:  # noqa: F811
    engine, _, _ = league
    plan, lock, params_dir = small_plan(tmp_path), tmp_path / "lock.yaml", tmp_path / "params"
    params_dir.mkdir()
    t = run_tuning(engine, REGISTRY, CFG, plan, dry_run=False, lock=lock, params_dir=params_dir)
    assert lock.exists() and plan.name in lock.read_text(encoding="utf-8")
    assert (t.params_file is None) == t.selected.is_base
    if t.params_file is not None:
        written = load_params(t.params_file)
        assert written.params_version == "1.6" and written.model_dump(exclude={"params_version"}) == \
            t.selected.params.model_dump(exclude={"params_version"})
    plan.write_text(plan.read_text(encoding="utf-8").replace("0.0005", "0.01"), encoding="utf-8")
    with pytest.raises(ValueError, match="changed after a real tuning run"):
        run_tuning(engine, REGISTRY, CFG, plan, dry_run=False, lock=lock, params_dir=params_dir)


def test_real_run_cannot_override_periods(league: Any, tmp_path: Path) -> None:  # noqa: F811
    from app.backtest.tuning_plan import Period

    engine, _, _ = league
    with pytest.raises(ValueError, match="dry run"):
        run_tuning(engine, REGISTRY, CFG, small_plan(tmp_path), dry_run=False, lock=tmp_path / "l.yaml",
                   tuning_override=Period(first=date(2025, 8, 1), last=date(2025, 8, 31)))


def test_selection_rule_and_params_writer(tmp_path: Path) -> None:
    from app.backtest.tuning import VariationScore

    plan = load_plan(CONFIG_DIR / "tuning_v1.yaml")
    vs = variations(plan, load_params(params_path(plan.base_params)))
    base = next(v for v in vs if v.is_base)

    def sc(i: int, metric: float) -> VariationScore:
        return VariationScore(vs[i], 100, metric, metric, 0.2, 0.7, 0.7, 10, i)

    far = next(i for i, v in enumerate(vs) if v.distance(base) == 3)
    near = next(i for i, v in enumerate(vs) if v.distance(base) == 1)
    scores = [sc(far, 0.5000), sc(near, 0.5004), sc(vs.index(base), 0.5100)]
    chosen, tied = select(scores, base, 0.0005)
    assert chosen.id == vs[near].id and set(tied) == {vs[far].id, vs[near].id}   # tie -> fewer changes wins
    chosen, _ = select(scores, base, 0.0)
    assert chosen.id == vs[far].id                                                # no tolerance -> the best

    path = write_params(vs[near], "1.5", "tuning_v1.yaml", "abc" * 22, tmp_path)
    assert path.name == "params_v1.6.yaml" and load_params(path).params_version == "1.6"
    assert "tuning_v1.yaml" in path.read_text(encoding="utf-8").splitlines()[1]
    with pytest.raises(FileExistsError):
        write_params(vs[near], "1.5", "tuning_v1.yaml", "abc" * 22, tmp_path)


def protocol_plan(tmp_path: Path) -> Path:
    """The synthetic league with an examined holdout and a prospective period after the registration date."""
    path = small_plan(tmp_path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.update({"registered_on": "2025-08-01", "holdout_status": "examined_before_registration",
                 "holdout_note": "examined in the test before registration, on purpose"})
    data["periods"] = {"tuning": {"first": "2025-08-20", "last": "2025-09-05"},
                       "holdout": {"first": "2025-09-06", "last": "2025-09-20"},
                       "prospective": {"first": "2025-09-21", "last": "2025-10-05"}}
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_prospective_period_and_honest_holdout(league: Any, tmp_path: Path) -> None:  # noqa: F811
    from app.backtest.report import NOTE

    engine, _, _ = league
    plan = protocol_plan(tmp_path)
    t = run_tuning(engine, REGISTRY, CFG, plan, dry_run=True, lock=tmp_path / "l.yaml", today=date(2026, 1, 1))
    assert t.prospective is not None and t.prospective.status.startswith("EVALUATED")
    assert t.prospective.selected is not None and t.prospective.baseline is not None
    note = next(c for c in t.gate if c.name == "Holdout status")
    assert note.status == NOTE and t.gate_passed == all(c.status == "PASS" for c in t.gate[:3])
    md = render_tuning(t, CFG)
    assert "Holdout examined before registration" in md and "## Prospective period" in md
    early = run_tuning(engine, REGISTRY, CFG, plan, dry_run=True, lock=tmp_path / "l.yaml", today=date(2025, 9, 1))
    assert early.prospective is not None and early.prospective.status.startswith("PENDING")
    assert tuning_json(early)["prospective"]["status"].startswith("PENDING")
