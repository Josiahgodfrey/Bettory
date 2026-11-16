"""Data-health settings (config/data_quality.yaml). Strict: unknown or missing keys raise."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.domain.params import CONFIG_DIR


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CrossCheckRules(_Strict):
    min_agreement: float = Field(gt=0, le=1)
    min_compared: int = Field(ge=1)
    max_disagreements_stored: int = Field(ge=0)


class RunAuditRules(_Strict):
    max_skipped_share: float = Field(ge=0, le=1)


class ApiQuota(_Strict):
    reserve_requests: int = Field(ge=0)


class QualityConfig(_Strict):
    crosscheck: CrossCheckRules
    run_audit: RunAuditRules
    api_quota: ApiQuota


def load_quality_config(path: Path | None = None) -> QualityConfig:
    with (path or CONFIG_DIR / "data_quality.yaml").open(encoding="utf-8") as f:
        return QualityConfig.model_validate(yaml.safe_load(f))
