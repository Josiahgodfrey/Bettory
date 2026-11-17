"""Real-bet settings (config/betting.yaml). Strict: unknown or missing keys raise."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.domain.params import CONFIG_DIR


class BettingConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    currency: str = Field(pattern=r"^[A-Z]{3}$")
    max_stake: float = Field(gt=0)


def load_betting_config(path: Path | None = None) -> BettingConfig:
    with (path or CONFIG_DIR / "betting.yaml").open(encoding="utf-8") as f:
        return BettingConfig.model_validate(yaml.safe_load(f))
