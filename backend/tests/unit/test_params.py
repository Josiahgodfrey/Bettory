"""The params file is the single, validated source of model parameters."""

import re
from pathlib import Path

import pytest
import yaml

from app.core.settings import BACKEND_DIR, get_settings
from app.domain.params import CONFIG_DIR, Params, load_params, params_path
from tests.support import PARAMS, params

CALCULATION = BACKEND_DIR / "app" / "calculation"


def test_active_params_file_loads_and_is_named_by_its_version() -> None:
    active = BACKEND_DIR / get_settings().params_file
    p = load_params(active)
    assert active == params_path(p.params_version) and p.params_version == "1.6"
    assert p.probability is not None and (p.blend.team_weight, p.blend.market_weight) == (1.0, 0.0)


def test_unknown_or_missing_keys_are_rejected(tmp_path: Path) -> None:
    data = yaml.safe_load(params_path("1.5").read_text(encoding="utf-8"))
    extra = {**data, "surprise": 1}
    with pytest.raises(ValueError):
        Params(**extra)
    missing = {k: v for k, v in data.items() if k != "model_c"}
    with pytest.raises(ValueError):
        Params(**missing)
    nested = {**data, "blend": {**data["blend"], "typo_weight": 0.1}}
    with pytest.raises(ValueError):
        Params(**nested)


def test_file_name_must_match_version(tmp_path: Path) -> None:
    wrong = tmp_path / "params_v9.9.yaml"
    wrong.write_text(params_path("1.5").read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match=r"declares params_version 1\.5"):
        load_params(wrong)


@pytest.mark.parametrize("changes", [
    {"blend__team_weight": 0.7},                       # team + market weight != 1
    {"blend__venue_weight": 0.5},                      # venue + general != 1
    {"shrinkage__early_index_trust_w_I": 1.5},
    {"value__overround_min": 0.2},                     # min above max
    {"competition_baselines__share_review_low": 0.2},  # low above high
    {"model_c__input_lines": {"FIRST15_GOAL": [0.5]}},  # the bet market is not a Model C input
    {"model_c__input_lines": {"HT_TOTAL": [1.0]}},     # whole line on an O/U market
    {"state_dependence__delta": 2.0},
    {"model_a__rating_prior_matches": {"FT": -1, "HT": 0}},
])
def test_invalid_values_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        params(**changes)


def test_with_values_rejects_unknown_keys_and_keeps_original() -> None:
    with pytest.raises(KeyError):
        PARAMS.with_values({"shrinkage.no_such_key": 1})
    changed = PARAMS.with_values({"shrinkage.team_index_k": 5})
    assert changed.shrinkage.team_index_k == 5 and PARAMS.shrinkage.team_index_k == 3


def test_no_parameter_copies_in_calculation_code() -> None:
    """Parameter values live only in config/params_vX.Y.yaml (no DEFAULT_... copies, no version strings)."""
    offenders = []
    for path in CALCULATION.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"^\s*DEFAULT_[A-Z_]+\s*=", text, re.MULTILINE) or "params_v1" in text:
            offenders.append(path.relative_to(BACKEND_DIR).as_posix())
    assert offenders == []


def test_old_versions_are_kept_unchanged() -> None:
    assert (CONFIG_DIR / "params_v1.4.yaml").exists()      # never edited, never deleted
