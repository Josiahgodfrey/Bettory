"""Pre-registered tuning plans (config/tuning_v*.yaml, docs/RESEARCH-PHASE4-TUNING.md §2.3 and §8).

    load_plan(path)           strict validation: unknown keys, ordered non-overlapping periods, a registered base
                              params file, labelled options, every grid combination a valid Params, the base
                              parameters present in the grid, hypotheses and Gate B rules
    variations(plan, base)    every combination, in grid order, with its id, labels, changes and Params
    Lock (config/tuning_lock.yaml, committed with the code):
        check_unchanged(path)     refuses a plan whose content changed after a real run used it
        register_use(path, note)  records the SHA-256 when a REAL run starts (dry runs never do)

A plan is evidence only if it was fixed before the data was seen; the lock makes a later edit visible and
refused instead of silent.
"""

from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.calculation.ev import Bet
from app.domain.params import CONFIG_DIR, Params, load_params, params_path
from app.domain.registry import Registry

LOCK_FILE = CONFIG_DIR / "tuning_lock.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Period(_Strict):
    first: date
    last: date

    @model_validator(mode="after")
    def _order(self) -> Self:
        if self.last < self.first:
            raise ValueError("period ends before it starts")
        return self


class Periods(_Strict):
    tuning: Period
    holdout: Period

    @model_validator(mode="after")
    def _disjoint(self) -> Self:
        if self.holdout.first <= self.tuning.last:
            raise ValueError("the holdout must start after the tuning period ends (it is the unseen future)")
        return self


class Metric(StrEnum):
    LOG_LOSS = "log_loss"
    BRIER = "brier"


class Selection(_Strict):
    market: Bet
    metric: Metric
    tie_tolerance: float = Field(ge=0)


class Option(_Strict):
    label: str = Field(min_length=1)
    set: dict[str, Any] = Field(min_length=1)  # dotted params keys -> values


class Dimension(_Strict):
    name: str = Field(min_length=1)
    options: list[Option] = Field(min_length=2)

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        labels = [o.label for o in self.options]
        if len(labels) != len(set(labels)):
            raise ValueError(f"grid dimension {self.name}: duplicate option labels")
        keys = {frozenset(o.set) for o in self.options}
        if len(keys) != 1:
            raise ValueError(f"grid dimension {self.name}: every option must set the same parameters")
        return self


class Subset(_Strict):
    listed: bool
    grade: str | None = None
    p_under05_at_least: float | None = Field(default=None, gt=0, lt=1)


class Hypothesis(_Strict):
    id: str = Field(pattern=r"^H\d+_[A-Z0-9_]+$")
    statement: str = Field(min_length=10)
    subset: Subset
    action_if_confirmed: str = Field(min_length=5)


class GateB(_Strict):
    min_qualified: int = Field(ge=1)
    calibration: str = Field(pattern=r"^noise_aware$")
    beats_baseline: str = Field(pattern=r"^paired_interval$")
    profit: str = Field(pattern=r"^paper_betting$")


class TuningPlan(_Strict):
    plan_version: int = Field(ge=1)
    registered_on: date
    base_params: str = Field(pattern=r"^\d+\.\d+$")
    competitions: list[str] = Field(min_length=1)
    periods: Periods
    selection: Selection
    grid: list[Dimension] = Field(min_length=1)
    hypotheses: list[Hypothesis]
    gate_b: GateB

    @field_validator("competitions")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        if len(v) != len(set(v)):
            raise ValueError("a competition is listed twice")
        return v

    @model_validator(mode="after")
    def _grid(self) -> Self:
        names = [d.name for d in self.grid]
        if len(names) != len(set(names)):
            raise ValueError("duplicate grid dimension names")
        keys = [k for d in self.grid for k in d.options[0].set]
        if len(keys) != len(set(keys)):
            raise ValueError("two grid dimensions set the same parameter")
        ids = [h.id for h in self.hypotheses]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate hypothesis ids")
        return self

    def check_registry(self, registry: Registry) -> None:
        known = {c.code for c in registry.competitions}
        unknown = [c for c in self.competitions if c not in known]
        if unknown:
            raise ValueError(f"unknown competitions in the plan: {unknown}")


@dataclass(frozen=True)
class Variation:
    id: str  # "v01", in grid order
    labels: tuple[str, ...]  # one per dimension, e.g. ("m40", "wI0.25", "team0.5")
    changes: dict[str, Any]
    params: Params
    is_base: bool  # identical to the base params

    @property
    def name(self) -> str:
        return " / ".join(self.labels)

    def distance(self, base: Variation) -> int:
        """How many grid dimensions differ from the base variation (tie-breaker: fewer changes win). Counted per
        dimension, not per parameter: moving the team/market weight is one change even though two values move."""
        return sum(a != b for a, b in zip(self.labels, base.labels, strict=True))


def load_plan(path: Path) -> TuningPlan:
    plan = TuningPlan.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    base = load_params(params_path(plan.base_params))
    vs = variations(plan, base)  # every combination must build a valid Params
    if not any(v.is_base for v in vs):
        raise ValueError(f"the grid does not contain the base parameters {plan.base_params}: the selected variation "
                         "could not be compared with them on the holdout")
    return plan


def variations(plan: TuningPlan, base: Params) -> list[Variation]:
    out = []
    for i, combo in enumerate(itertools.product(*(d.options for d in plan.grid)), start=1):
        changes: dict[str, Any] = {}
        for option in combo:
            changes.update(option.set)
        try:
            params = base.with_values(changes)
        except (KeyError, ValueError) as exc:
            raise ValueError(f"grid combination {[o.label for o in combo]} is not valid: {exc}") from exc
        out.append(Variation(f"v{i:02d}", tuple(o.label for o in combo), changes, params,
                             params.model_dump() == base.model_dump()))
    return out


# --- lock -----------------------------------------------------------------------------------------------------


def sha256_of(path: Path) -> str:
    """Fingerprint of the plan's content, independent of line endings: git turns LF into CRLF on Windows checkouts,
    and the same unchanged plan must not look edited on another machine."""
    return hashlib.sha256(path.read_bytes().replace(b"
", b"
")).hexdigest()


def _read_lock(lock: Path) -> dict[str, Any]:
    if not lock.exists():
        return {}
    data = yaml.safe_load(lock.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{lock} is not a mapping")
    return data


def check_unchanged(path: Path, lock: Path = LOCK_FILE) -> str | None:
    """None if the plan was never used by a real run; its registration time if it was and is unchanged.
    Raises if it was used and its content changed since."""
    entry = _read_lock(lock).get(path.name)
    if entry is None:
        return None
    if entry["sha256"] != sha256_of(path):
        raise ValueError(f"{path.name} was changed after a real tuning run used it ({entry['used_at']}). A plan "
                         "cannot be edited once used: restore it, or register a new plan file (tuning_v2.yaml)")
    return str(entry["used_at"])


def register_use(path: Path, note: str, lock: Path = LOCK_FILE) -> None:
    """Record the plan's SHA-256 when a REAL run starts. Idempotent for an unchanged plan."""
    if check_unchanged(path, lock) is not None:
        return
    data = _read_lock(lock)
    data[path.name] = {"sha256": sha256_of(path), "used_at": datetime.now(UTC).isoformat(timespec="seconds"),
                       "note": note}
    header = ("# Pre-registered tuning plans that a real run has used (app/backtest/tuning_plan.py).\n"
              "# Written by the tool; commit it. A listed plan can no longer be edited.\n")
    lock.write_text(header + yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
