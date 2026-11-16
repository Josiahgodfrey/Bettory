"""Forecast-quality statistics: exact small cases and large simulations with known truth."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.settlement.performance import Pair, score
from app.settlement.statistics import (
    block_bootstrap,
    calibration_in_the_large_z,
    decompose,
    recalibration,
    skill,
    spiegelhalter_z,
)


def sample(p: np.ndarray, truth: np.ndarray, seed: int) -> list[Pair]:
    rng = np.random.default_rng(seed)
    return [(float(a), bool(b)) for a, b in zip(p, rng.random(len(truth)) < truth, strict=True)]


def test_decomposition_is_exact_when_bins_are_constant() -> None:
    pairs = [(0.7, True)] * 7 + [(0.7, False)] * 3 + [(0.9, True)] * 8 + [(0.9, False)] * 2
    d = decompose(pairs, 2)
    base = 15 / 20
    assert d.reliability == pytest.approx((10 * 0.0 ** 2 + 10 * 0.1 ** 2) / 20)
    assert d.resolution == pytest.approx((10 * (0.7 - base) ** 2 + 10 * (0.8 - base) ** 2) / 20)
    assert d.uncertainty == pytest.approx(base * (1 - base))
    s = score(pairs)
    assert s is not None and d.brier == pytest.approx(s.brier)
    assert d.within_bin == pytest.approx(0.0, abs=1e-12) and d.bins == 2


def test_skill() -> None:
    assert skill(0.18, 0.20) == pytest.approx(0.1) and skill(0.2, 0.2) == 0.0 and skill(0.1, 0.0) is None


def test_calibration_in_the_large_by_hand() -> None:
    pairs = [(0.5, True), (0.5, True), (0.5, False), (0.8, True)]
    expected = ((1 - 0.5) + (1 - 0.5) + (0 - 0.5) + (1 - 0.8)) / math.sqrt(3 * 0.25 + 0.16)
    assert calibration_in_the_large_z(pairs) == pytest.approx(expected)


def test_spiegelhalter_by_hand() -> None:
    pairs = [(0.2, False), (0.7, True), (0.9, True)]
    num = (0 - 0.2) * 0.6 + (1 - 0.7) * (-0.4) + (1 - 0.9) * (-0.8)
    den = math.sqrt(0.36 * 0.16 + 0.16 * 0.21 + 0.64 * 0.09)
    assert spiegelhalter_z(pairs) == pytest.approx(num / den)
    assert spiegelhalter_z([(0.5, True), (0.5, False)]) is None          # p = 0.5 carries no information


def test_calibrated_forecasts_look_calibrated_and_overconfident_ones_do_not() -> None:
    rng = np.random.default_rng(1)
    truth = rng.uniform(0.6, 0.85, 20_000)
    honest = sample(truth, truth, seed=2)
    logit = np.log(truth / (1 - truth))
    overconfident = sample(1 / (1 + np.exp(-2.0 * logit)), truth, seed=2)   # logit doubled: too extreme
    z_ok, z_bad = spiegelhalter_z(honest), spiegelhalter_z(overconfident)
    assert z_ok is not None and abs(z_ok) < 3 and z_bad is not None and abs(z_bad) > 5
    r_ok, r_bad = recalibration(honest), recalibration(overconfident)
    assert r_ok is not None and r_ok.converged and r_ok.slope == pytest.approx(1.0, abs=0.1)
    assert r_bad is not None and r_bad.slope == pytest.approx(0.5, abs=0.06)
    assert recalibration([(0.7, True), (0.7, True)]) is None


def test_block_bootstrap() -> None:
    blocks = [[(1.0, 1.0), (3.0, 1.0)], [(2.0, 1.0)], [(5.0, 1.0), (1.0, 1.0)]]
    a = block_bootstrap(blocks, 500, 0.95, seed=7)
    b = block_bootstrap(blocks, 500, 0.95, seed=7)
    assert a == b and a is not None and a.estimate == pytest.approx(2.4) and a.low <= a.estimate <= a.high
    assert a.blocks == 3 and not a.excludes(2.4)
    assert block_bootstrap([[(1.0, 1.0)]], 500, 0.95, seed=7) is None
    roi = block_bootstrap([[(0.5, 1.0), (-1.0, 1.0)], [(0.5, 1.0)]], 200, 0.9, seed=1)
    assert roi is not None and roi.estimate == pytest.approx(0.0)
    with pytest.raises(ValueError):
        block_bootstrap(blocks, 10, 0.95, seed=1)


def test_a_clear_improvement_is_detected_and_noise_is_not() -> None:
    """Per-session Brier differences: a real improvement gives an interval below 0; pure noise covers 0."""
    rng = np.random.default_rng(3)
    better = [[(float(x), 1.0) for x in rng.normal(-0.01, 0.05, 5)] for _ in range(200)]
    noise = [[(float(x), 1.0) for x in rng.normal(0.0, 0.05, 5)] for _ in range(200)]
    good = block_bootstrap(better, 1000, 0.95, seed=4)
    null = block_bootstrap(noise, 1000, 0.95, seed=4)
    assert good is not None and good.high < 0 and good.excludes(0.0)
    assert null is not None and not null.excludes(0.0)
