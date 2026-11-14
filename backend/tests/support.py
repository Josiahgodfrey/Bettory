"""Shared test helpers: the active params file and validated variations of it."""

from typing import Any

from app.core.settings import BACKEND_DIR, get_settings
from app.domain.params import Params, load_params

PARAMS: Params = load_params(BACKEND_DIR / get_settings().params_file)
# The documented v1.4 formula without rating shrinkage (golden tests reproduce the docs' examples).
DOC_FORMULA: Params = PARAMS.with_values({"model_a.rating_prior_matches": {"FT": 0, "HT": 0}})


def params(**changes: Any) -> Params:
    """PARAMS with dotted keys written with '__', e.g. params(shrinkage__team_index_k=0)."""
    return PARAMS.with_values({k.replace("__", "."): v for k, v in changes.items()})
