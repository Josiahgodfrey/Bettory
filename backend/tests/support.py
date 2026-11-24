"""Shared test helpers: params files and validated variations of them.

PARAMS is params 1.5 (odds-based Layer 3, legacy): kept only so the odds-based checks stay tested.
base. LIVE is the active file (params 1.6 since 2026-11-23: probabilities only, analysis 2 h before each window)."""

from typing import Any

from app.core.settings import BACKEND_DIR, get_settings
from app.domain.params import Params, load_params

PARAMS: Params = load_params(BACKEND_DIR / "config" / "params_v1.5.yaml")
LIVE: Params = load_params(BACKEND_DIR / get_settings().params_file)
# The documented v1.4 formula without rating shrinkage (golden tests reproduce the docs' examples).
DOC_FORMULA: Params = PARAMS.with_values({"model_a.rating_prior_matches": {"FT": 0, "HT": 0}})


def params(**changes: Any) -> Params:
    """PARAMS with dotted keys written with '__', e.g. params(shrinkage__team_index_k=0)."""
    return PARAMS.with_values({k.replace("__", "."): v for k, v in changes.items()})
