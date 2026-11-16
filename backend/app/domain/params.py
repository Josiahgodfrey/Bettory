"""Model parameters: one strict, versioned model of config/params_vX.Y.yaml. Pure except load_params().

The params file is the ONLY source of parameter values. Calculation functions receive a Params object
explicitly; there are no parameter defaults in the calculation code, so a stale value can never be
used silently. Unknown keys, missing keys and out-of-range values are rejected at load time.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import Field, model_validator

from app.domain.enums import DevigMethod, MarketCode
from app.domain.models import Strict

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
Fraction = Field(ge=0.0, le=1.0)


class SessionWindow(Strict):
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")
    deadline: str = Field(pattern=r"^\d{2}:\d{2}$")


class Sessions(Strict):
    timezone: str
    night: SessionWindow
    noon: SessionWindow
    lead_time_hours: float = Field(ge=0)
    run_buffer_minutes: int = Field(ge=0)
    max_matches_per_session: int = Field(ge=1)


class Screen(Strict):
    league_p00_min: float = Fraction


class DataGate(Strict):
    history_matches: int = Field(ge=1)


class Blend(Strict):
    venue_weight: float = Fraction
    general_weight: float = Fraction
    team_weight: float = Fraction
    market_weight: float = Fraction

    @model_validator(mode="after")
    def _sums(self) -> Self:
        if abs(self.venue_weight + self.general_weight - 1.0) > 1e-9:
            raise ValueError("blend.venue_weight + blend.general_weight must be 1")
        if abs(self.team_weight + self.market_weight - 1.0) > 1e-9:
            raise ValueError("blend.team_weight + blend.market_weight must be 1")
        return self


class Shrinkage(Strict):
    early_share_K_goals: float = Field(ge=0)
    team_index_k: float = Field(ge=0)
    early_index_trust_w_I: float = Fraction


class RatingPrior(Strict):
    FT: float = Field(ge=0)
    HT: float = Field(ge=0)


class ModelA(Strict):
    ft_weight: float = Fraction
    rating_prior_matches: RatingPrior


class CompetitionBaselines(Strict):
    min_matches: int = Field(ge=1)
    max_pooled_seasons: int = Field(ge=1)
    share_review_low: float = Fraction
    share_review_high: float = Fraction

    @model_validator(mode="after")
    def _range(self) -> Self:
        if self.share_review_low >= self.share_review_high:
            raise ValueError("share_review_low must be below share_review_high")
        return self


class ModelC(Strict):
    max_line_disagreement: float = Field(gt=0)
    devig_method: DevigMethod
    bookmaker_priority: tuple[str, ...]
    input_lines: dict[MarketCode, tuple[float, ...]]

    @model_validator(mode="after")
    def _lines(self) -> Self:
        allowed = {MarketCode.HT_TOTAL, MarketCode.HT_ASIAN_TOTAL, MarketCode.FT_TOTAL}
        if not set(self.input_lines) <= allowed:
            raise ValueError(f"model_c.input_lines: only {sorted(allowed)} are totals inputs")
        for market, lines in self.input_lines.items():
            for line in lines:
                half = (line * 2) % 2 == 1
                if line <= 0 or not (half or (market is MarketCode.HT_ASIAN_TOTAL and float(line).is_integer())):
                    raise ValueError(f"model_c.input_lines: invalid {market} line {line}")
        return self


class Value(Strict):
    ev_min: float = Field(ge=0)
    ev_min_no_market: float = Field(ge=0)
    max_model_gap_pts: float = Field(ge=0)
    max_bookmaker_gap_pts: float = Field(ge=0)
    overround_min: float
    overround_max: float
    devig_method: DevigMethod

    @model_validator(mode="after")
    def _range(self) -> Self:
        if not self.overround_min < self.overround_max:
            raise ValueError("value.overround_min must be below value.overround_max")
        return self


class Grades(Strict):
    a_max_gap_pts: float = Field(ge=0)
    b_max_gap_pts: float = Field(ge=0)


class RedFlags(Strict):
    favourite_odds_below: float = Field(gt=1)
    favourite_proxy_home_win_rate: float = Fraction
    favourite_proxy_away_win_rate: float = Fraction
    recent_window: int = Field(ge=1)
    recent_early_goals_last5: int = Field(ge=1)
    h2h_min_meetings: int = Field(ge=1)
    h2h_early_goal_rate: float = Fraction
    h2h_bonus_zero_zero_rate: float = Fraction


class H2H(Strict):
    max_meetings: int = Field(ge=1)
    max_years: int = Field(ge=1)


class StateDependence(Strict):
    delta: float = Field(ge=-1.0, le=1.0)


class Settlement(Strict):
    cutoff_label_minute: int = Field(ge=1, le=45)


class Params(Strict):
    params_version: str = Field(pattern=r"^\d+\.\d+$")
    formula_version: str
    sessions: Sessions
    screen: Screen
    data_gate: DataGate
    blend: Blend
    shrinkage: Shrinkage
    model_a: ModelA
    competition_baselines: CompetitionBaselines
    model_c: ModelC
    value: Value
    grades: Grades
    red_flags: RedFlags
    h2h: H2H
    state_dependence: StateDependence
    settlement: Settlement

    def with_values(self, changes: Mapping[str, Any]) -> Params:
        """A validated copy with dotted keys replaced, e.g. {"shrinkage.team_index_k": 0} (backtest tuning,
        tests). Unknown keys are rejected; the copy goes through the same validation as a loaded file."""
        data = self.model_dump()
        for dotted, value in changes.items():
            node = data
            *path, last = dotted.split(".")
            for key in path:
                if not isinstance(node.get(key), dict):
                    raise KeyError(f"unknown parameter {dotted}")
                node = node[key]
            if last not in node:
                raise KeyError(f"unknown parameter {dotted}")
            node[last] = value
        return Params(**data)


def params_path(version: str) -> Path:
    return CONFIG_DIR / f"params_v{version}.yaml"


@cache
def load_params(path: Path) -> Params:
    """Load and validate one params file; the file name must match its params_version."""
    params = Params(**yaml.safe_load(path.read_text(encoding="utf-8")))
    if path.name != f"params_v{params.params_version}.yaml":
        raise ValueError(f"{path.name} declares params_version {params.params_version}")
    return params
