"""Invariant checks used by every calculation. Pure. They raise instead of returning a wrong number.

- lambda (goal expectancy): finite and >= 0
- probability: finite and 0 <= p <= 1 (strict 0 < p < 1 where a price is derived from it)
- score grid: total probability 1 +/- GRID_TOLERANCE, no negative cell
- fair odds: finite and > 1
"""

from __future__ import annotations

import math
from collections.abc import Iterable

GRID_TOLERANCE = 0.001


class InvariantError(ValueError):
    """A calculation produced or received a value that cannot be right. Never caught silently."""


def check_lambda(value: float, name: str = "lambda") -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise InvariantError(f"{name} must be a finite number >= 0, got {value!r}")
    return float(value)


def check_probability(value: float, name: str = "probability", *, strict: bool = False) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise InvariantError(f"{name} must be a finite number, got {value!r}")
    ok = 0.0 < value < 1.0 if strict else 0.0 <= value <= 1.0
    if not ok:
        bounds = "0 < p < 1" if strict else "0 <= p <= 1"
        raise InvariantError(f"{name} must satisfy {bounds}, got {value!r}")
    return float(value)


def check_grid(cells: Iterable[Iterable[float]], tolerance: float = GRID_TOLERANCE) -> float:
    """Returns the grid's total probability; raises if a cell is negative or the total is not 1 +/- tolerance."""
    total = 0.0
    for row in cells:
        for p in row:
            if not math.isfinite(p) or p < -1e-15:
                raise InvariantError(f"score grid has an invalid cell {p!r}")
            total += p
    if abs(total - 1.0) > tolerance:
        raise InvariantError(f"score grid sums to {total:.6f}, not 1 +/- {tolerance} (grid too small for lambda?)")
    return total


def check_fair_odds(value: float, name: str = "fair odds") -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 1.0:
        raise InvariantError(f"{name} must be a finite number > 1, got {value!r}")
    return float(value)
