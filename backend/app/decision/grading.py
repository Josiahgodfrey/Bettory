"""Confidence grades A / B / SKIP (DATA-SOURCES-AND-MARKET-MODEL.md 7.3, SYSTEM-DOCUMENTATION.md 7). Pure.

    A    : all available models within grades.a_max_gap_pts AND full samples
    B    : all available models within grades.b_max_gap_pts
    SKIP : otherwise
"""

from __future__ import annotations

from collections.abc import Sequence

from app.calculation.invariants import InvariantError
from app.domain.enums import Grade
from app.domain.params import Params


def model_spread_pts(probabilities: Sequence[float]) -> float:
    """Largest disagreement between the available models, in percentage points."""
    if len(probabilities) < 2:
        raise InvariantError("a model spread needs at least two models")
    return (max(probabilities) - min(probabilities)) * 100.0


def grade(spread_pts: float, full_samples: bool, params: Params) -> Grade:
    g = params.grades
    if spread_pts <= g.a_max_gap_pts and full_samples:
        return Grade.A
    if spread_pts <= g.b_max_gap_pts:
        return Grade.B
    return Grade.SKIP
