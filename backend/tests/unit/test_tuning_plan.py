"""Pre-registered tuning plan: the shipped plan, every rejection rule, and the lock against later edits."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from app.backtest.tuning_plan import check_unchanged, load_plan, register_use, sha256_of, variations
from app.domain.params import CONFIG_DIR, load_params, params_path
from app.domain.registry import load_registry

PLAN = CONFIG_DIR / "tuning_v1.yaml"
RAW: dict[str, Any] = yaml.safe_load(PLAN.read_text(encoding="utf-8"))


def write(tmp_path: Path, data: dict[str, Any], name: str = "tuning_vx.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def changed(**edits: Any) -> dict[str, Any]:
    data = copy.deepcopy(RAW)
    for dotted, value in edits.items():
        node = data
        *path, last = dotted.split("__")
        for key in path:
            node = node[int(key)] if key.isdigit() else node[key]
        node[int(last) if last.isdigit() else last] = value
    return data


def test_shipped_plan() -> None:
    plan = load_plan(PLAN)
    plan.check_registry(load_registry())
    vs = variations(plan, load_params(params_path(plan.base_params)))
    assert len(vs) == 4 * 3 * 3 and len({v.id for v in vs}) == 36
    (base,) = [v for v in vs if v.is_base]
    assert base.name == "m20 / wI0.5 / team0.5"
    assert base.distance(base) == 0 and vs[0].distance(base) == 2                     # wI0 and team1.0 differ
    assert max(v.distance(base) for v in vs) == 3
    assert plan.selection.metric == "log_loss" and plan.gate_b.profit == "paper_betting"
    assert [h.id for h in plan.hypotheses] == ["H1_GRADE_B", "H2_TOP_PREDICTIONS"]


@pytest.mark.parametrize(("edits", "message"), [
    ({"periods__holdout__first": "2025-06-01"}, "holdout must start after"),
    ({"periods__tuning__last": "2021-01-01"}, "ends before it starts"),
    ({"grid__0__options__0__set": {"model_a.no_such_key": 1}}, "every option must set the same"),
    ({"grid__2__options__0__set": {"blend.team_weight": 1.0, "blend.market_weight": 0.5}}, "not valid"),
    ({"grid__0__options__1__label": "m20"}, "duplicate option labels"),
    ({"grid__1__options__2__set": {"shrinkage.early_index_trust_w_I": 0.75}}, "does not contain the base"),
    ({"unexpected": 1}, "Extra inputs"),
    ({"selection__metric": "roi"}, "metric"),
    ({"gate_b__profit": "historical_roi"}, "profit"),
])
def test_invalid_plans_are_rejected(tmp_path: Path, edits: dict[str, Any], message: str) -> None:
    with pytest.raises((ValidationError, ValueError), match=message):
        load_plan(write(tmp_path, changed(**edits)))


def test_unknown_parameter_key_is_rejected(tmp_path: Path) -> None:
    data = changed()
    for option in data["grid"][1]["options"]:
        option["set"] = {"shrinkage.no_such_key": option["set"]["shrinkage.early_index_trust_w_I"]}
    with pytest.raises(ValueError, match="not valid"):
        load_plan(write(tmp_path, data))


def test_unknown_competition(tmp_path: Path) -> None:
    plan = load_plan(write(tmp_path, changed(competitions=["ENG1", "XXX9"])))
    with pytest.raises(ValueError, match="XXX9"):
        plan.check_registry(load_registry())


def test_lock_refuses_a_plan_edited_after_a_real_run(tmp_path: Path) -> None:
    plan, lock = write(tmp_path, changed(), "tuning_v1.yaml"), tmp_path / "tuning_lock.yaml"
    assert check_unchanged(plan, lock) is None                                   # never used: editable
    register_use(plan, "real run", lock)
    used_at = check_unchanged(plan, lock)
    assert used_at is not None and sha256_of(plan) in lock.read_text(encoding="utf-8")
    register_use(plan, "again", lock)                                            # idempotent
    assert check_unchanged(plan, lock) == used_at
    plan.write_text(plan.read_text(encoding="utf-8").replace("0.0005", "0.005"), encoding="utf-8")
    with pytest.raises(ValueError, match="changed after a real tuning run"):
        check_unchanged(plan, lock)
    with pytest.raises(ValueError, match="changed after a real tuning run"):
        register_use(plan, "sneaky", lock)
