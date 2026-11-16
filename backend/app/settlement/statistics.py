"""Forecast-quality statistics for binary outcomes (docs/RESEARCH-PHASE4-TUNING.md §3, §6). Pure.

    decompose            Murphy (1973): Brier = REL - RES + UNC (+ a small within-bin term, reported)
    skill                Brier skill score against a reference forecast: 1 - Brier / Brier_ref
    calibration_in_large z = sum(o - p) / sqrt(sum p(1-p))                   is the AVERAGE right?
    spiegelhalter_z      z = sum (o - p)(1 - 2p) / sqrt(sum (1-2p)^2 p(1-p))  Spiegelhalter (1986), Brier-based
    recalibration        logistic fit  logit P(o=1) = a + b * logit(p):  b < 1 over-confident, b > 1 under-
    block_bootstrap      confidence interval of a statistic, resampling whole blocks (e.g. sessions) - matches in
                         one session share a matchday and an analysis moment, so they are not independent

z values are approximately standard normal when the forecasts are calibrated: |z| > 1.96 is evidence at 5 %.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from app.settlement.performance import Pair

_EPS = 1e-12


def _arrays(pairs: Sequence[Pair]) -> tuple[np.ndarray, np.ndarray]:
    if not pairs:
        raise ValueError("no forecasts")
    p = np.array([x for x, _ in pairs], dtype=float)
    o = np.array([float(y) for _, y in pairs], dtype=float)
    if np.any((p < 0) | (p > 1)) or np.any(np.isnan(p)):
        raise ValueError("probabilities must be in [0, 1]")
    return p, o


@dataclass(frozen=True)
class Decomposition:
    brier: float
    reliability: float  # calibration penalty (lower is better)
    resolution: float  # discrimination reward (higher is better)
    uncertainty: float  # base-rate variance: the Brier score of always forecasting the average
    within_bin: float  # brier - (rel - res + unc): from forecasts varying inside a bin (binning artefact)
    bins: int


def decompose(pairs: Sequence[Pair], width_pts: float) -> Decomposition:
    p, o = _arrays(pairs)
    n, base = len(p), float(o.mean())
    top = math.ceil(100.0 / width_pts) - 1
    keys = np.minimum((p * 100.0 // width_pts).astype(int), top)
    rel = res = 0.0
    for k in np.unique(keys):
        m = keys == k
        nk, pk, ok = int(m.sum()), float(p[m].mean()), float(o[m].mean())
        rel += nk * (pk - ok) ** 2
        res += nk * (ok - base) ** 2
    brier = float(((p - o) ** 2).mean())
    rel, res, unc = rel / n, res / n, base * (1.0 - base)
    return Decomposition(brier, rel, res, unc, brier - (rel - res + unc), len(np.unique(keys)))


def skill(brier: float, reference_brier: float) -> float | None:
    """1 - Brier / reference: > 0 better than the reference, 0 equal, < 0 worse."""
    return None if reference_brier <= 0 else 1.0 - brier / reference_brier


def calibration_in_the_large_z(pairs: Sequence[Pair]) -> float | None:
    p, o = _arrays(pairs)
    var = float((p * (1.0 - p)).sum())
    return None if var <= 0 else float((o - p).sum()) / math.sqrt(var)


def spiegelhalter_z(pairs: Sequence[Pair]) -> float | None:
    p, o = _arrays(pairs)
    var = float(((1.0 - 2.0 * p) ** 2 * p * (1.0 - p)).sum())
    return None if var <= 0 else float(((o - p) * (1.0 - 2.0 * p)).sum()) / math.sqrt(var)


@dataclass(frozen=True)
class Recalibration:
    intercept: float  # a: 0 when the average is right (with slope 1)
    slope: float  # b: 1 when perfectly calibrated; < 1 the forecasts are too extreme (over-confident)
    converged: bool


def recalibration(pairs: Sequence[Pair], iterations: int = 50) -> Recalibration | None:
    """Maximum-likelihood logistic fit of the outcome on logit(p) (Newton-Raphson, 2 parameters)."""
    p, o = _arrays(pairs)
    if len(np.unique(o)) < 2 or float(np.ptp(p)) < _EPS:
        return None  # an all-equal outcome or forecast cannot identify a slope
    q = np.clip(p, 1e-6, 1 - 1e-6)
    x = np.log(q / (1 - q))
    design = np.column_stack([np.ones_like(x), x])
    beta = np.array([0.0, 1.0])
    for _ in range(iterations):
        mu = 1.0 / (1.0 + np.exp(-(design @ beta)))
        w = mu * (1 - mu)
        hessian = design.T @ (design * w[:, None])
        try:
            step = np.linalg.solve(hessian, design.T @ (o - mu))
        except np.linalg.LinAlgError:
            return Recalibration(float(beta[0]), float(beta[1]), False)
        beta = beta + step
        if float(np.abs(step).max()) < 1e-10:
            return Recalibration(float(beta[0]), float(beta[1]), True)
    return Recalibration(float(beta[0]), float(beta[1]), False)


@dataclass(frozen=True)
class Interval:
    estimate: float
    low: float
    high: float
    blocks: int

    def excludes(self, value: float = 0.0) -> bool:
        return not self.low <= value <= self.high


def block_bootstrap(blocks: Sequence[Sequence[tuple[float, float]]], samples: int, confidence: float,
                    seed: int) -> Interval | None:
    """Percentile interval of a ratio of sums, sum(numerator) / sum(denominator), resampling WHOLE blocks with
    replacement. Items are (numerator, denominator): (difference, 1) per match gives the mean difference;
    (profit, stake) per bet gives the ROI. Vectorised (block sums are resampled), deterministic for a seed."""
    usable = [b for b in blocks if b]
    if len(usable) < 2:
        return None
    if not 0 < confidence < 1 or samples < 100:
        raise ValueError("confidence in (0, 1) and at least 100 samples required")
    num = np.array([sum(n for n, _ in b) for b in usable], dtype=float)
    den = np.array([sum(d for _, d in b) for b in usable], dtype=float)
    if den.sum() <= 0:
        return None
    rng = np.random.default_rng(seed)
    k = len(usable)
    counts = np.zeros((samples, k))
    np.add.at(counts, (np.repeat(np.arange(samples), k), rng.integers(0, k, samples * k)), 1.0)
    dens = counts @ den
    stats = np.divide(counts @ num, dens, out=np.zeros(samples), where=dens > 0)
    tail = (1 - confidence) / 2
    low, high = np.quantile(stats, [tail, 1 - tail])
    return Interval(float(num.sum() / den.sum()), float(low), float(high), k)
