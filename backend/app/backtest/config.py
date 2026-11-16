"""Backtest settings (config/backtest.yaml): how results are judged. Strict: unknown or missing keys raise."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.domain.params import CONFIG_DIR


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Calibration(_Strict):
    bucket_width_pts: float = Field(gt=0, le=50)
    tolerance_pts: float = Field(gt=0)
    noise_multiplier: float = Field(ge=0)  # 0 = the flat tolerance only
    min_bucket_matches: int = Field(ge=1)


class GateB(_Strict):
    min_qualified: int = Field(ge=1)


class EstimatedValue(_Strict):
    assumed_bookmaker_margin: float = Field(ge=0, lt=1)
    stake: float = Field(gt=0)


class BacktestConfig(_Strict):
    calibration: Calibration
    gate_b: GateB
    estimated_value: EstimatedValue


def load_backtest_config(path: Path | None = None) -> BacktestConfig:
    with (path or CONFIG_DIR / "backtest.yaml").open(encoding="utf-8") as f:
        return BacktestConfig.model_validate(yaml.safe_load(f))
