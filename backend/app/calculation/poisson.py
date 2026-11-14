"""Poisson goal model: probabilities, score grid, 0-15 outcomes and lambda solvers. Pure.

FIFTEEN-MINUTE-ODDS-CALCULATION.md:
- section 7.1  score grid: independent Poisson for home and away goals
- section 7.2  outcomes: Under 0.5 (= 0-0), Over 0.5, Draw, Home leads, Away leads
- section 7.3  optional state-dependence adjustment (Draw only): P(1,1) x (1 + delta), the extra
               probability taken proportionally from 1-0 and 0-1; 0-0 is never affected
- section 11.2 single half-goal line -> lambda (closed form for 0.5, bisection otherwise)
- section 11.3 Asian whole line (push on exactly n goals) -> lambda

The grid is 0..max_goals per team. By default its size is chosen per lambda so that the cut-off tail is
at most GRID_TOLERANCE / 10 (0..4 as in the doc is enough for 0-15, full-match grids need more). It is
never renormalised: if an explicit max_goals leaves a tail larger than GRID_TOLERANCE, the grid is
rejected instead of silently inflating the other cells.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from app.calculation.invariants import (
    GRID_TOLERANCE,
    InvariantError,
    check_grid,
    check_lambda,
    check_probability,
)

MIN_GRID_GOALS = 4  # doc 7.1
MAX_GRID_GOALS = 40
SOLVER_LOW = 0.01  # doc 11.2: lambda in [0.01, 6]
SOLVER_HIGH = 6.0
SOLVER_TOLERANCE = 1e-9  # doc asks for 0.0001; bisection is cheap, so solve much tighter
_MAX_ITERATIONS = 200


class NoSolutionError(ValueError):
    """No lambda in the search interval reproduces the market probability (fail closed: do not price)."""


# --- distribution ------------------------------------------------------------------------------


def pmf(k: int, lam: float) -> float:
    """P(X = k) for X ~ Poisson(lam)."""
    if not isinstance(k, int) or isinstance(k, bool) or k < 0:
        raise InvariantError(f"goal count must be an int >= 0, got {k!r}")
    lam = check_lambda(lam)
    if lam == 0.0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))


def cdf(n: int, lam: float) -> float:
    """P(X <= n). n < 0 gives 0."""
    if not isinstance(n, int) or isinstance(n, bool):
        raise InvariantError(f"n must be an int, got {n!r}")
    if n < 0:
        check_lambda(lam)
        return 0.0
    return min(1.0, math.fsum(pmf(k, lam) for k in range(n + 1)))


# --- score grid --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcomes:
    """Probabilities read from one score grid (period = the grid's period, e.g. 0-15)."""

    p_00: float  # = P(Under 0.5)
    p_over05: float
    p_draw: float
    p_home_lead: float
    p_away_lead: float


@dataclass(frozen=True)
class ScoreGrid:
    lam_home: float
    lam_away: float
    delta: float
    cells: tuple[tuple[float, ...], ...]  # cells[h][a] = P(home h, away a)

    @property
    def max_goals(self) -> int:
        return len(self.cells) - 1

    @property
    def mass(self) -> float:
        return math.fsum(p for row in self.cells for p in row)

    def p(self, home: int, away: int) -> float:
        if 0 <= home <= self.max_goals and 0 <= away <= self.max_goals:
            return self.cells[home][away]
        return 0.0

    def outcomes(self) -> Outcomes:
        n = self.max_goals + 1
        p_00 = self.cells[0][0]
        draw = math.fsum(self.cells[i][i] for i in range(n))
        home = math.fsum(self.cells[h][a] for h in range(n) for a in range(h))
        away = math.fsum(self.cells[h][a] for h in range(n) for a in range(h + 1, n))
        out = Outcomes(p_00=p_00, p_over05=1.0 - p_00, p_draw=draw, p_home_lead=home, p_away_lead=away)
        for name, value in vars(out).items():
            check_probability(value, name)
        if draw < p_00:
            raise InvariantError("P(Draw) must be >= P(0-0)")
        return out


def grid_size(lam_home: float, lam_away: float, tolerance: float = GRID_TOLERANCE) -> int:
    """Smallest max_goals (>= MIN_GRID_GOALS) whose cut-off tail is at most tolerance / 10."""
    lam_home, lam_away = check_lambda(lam_home, "lam_home"), check_lambda(lam_away, "lam_away")
    for n in range(MIN_GRID_GOALS, MAX_GRID_GOALS + 1):
        if 1.0 - cdf(n, lam_home) * cdf(n, lam_away) <= tolerance / 10:
            return n
    raise InvariantError(f"lambda too large for a score grid: {lam_home}, {lam_away}")


def score_grid(lam_home: float, lam_away: float, max_goals: int | None = None,
               delta: float = 0.0, tolerance: float = GRID_TOLERANCE) -> ScoreGrid:
    """Independent Poisson grid, with the optional section 7.3 adjustment (delta = 0 by default).
    `max_goals=None` sizes the grid automatically (see grid_size)."""
    lam_home = check_lambda(lam_home, "lam_home")
    lam_away = check_lambda(lam_away, "lam_away")
    if max_goals is None:
        max_goals = grid_size(lam_home, lam_away, tolerance)
    if not isinstance(max_goals, int) or isinstance(max_goals, bool) or max_goals < 1:
        raise InvariantError(f"max_goals must be an int >= 1, got {max_goals!r}")
    if not math.isfinite(delta) or not -1.0 <= delta <= 1.0:
        raise InvariantError(f"delta must be within [-1, 1], got {delta!r}")

    home = [pmf(h, lam_home) for h in range(max_goals + 1)]
    away = [pmf(a, lam_away) for a in range(max_goals + 1)]
    cells = [[ph * pa for pa in away] for ph in home]

    if delta != 0.0:
        extra = cells[1][1] * delta
        pool = cells[1][0] + cells[0][1]
        if extra > pool:
            raise InvariantError("delta too large: 1-0 and 0-1 cannot give up that much probability")
        if pool > 0.0:
            cells[1][0] -= extra * cells[1][0] / pool
            cells[0][1] -= extra * cells[0][1] / pool
            cells[1][1] += extra

    check_grid(cells, tolerance)
    return ScoreGrid(lam_home, lam_away, delta, tuple(tuple(row) for row in cells))


# --- solvers: market probability -> lambda ------------------------------------------------------


def _bisect_decreasing(f: Callable[[float], float], target: float, lo: float, hi: float, tol: float) -> float:
    """Root of f(lam) = target for f strictly decreasing on [lo, hi]."""
    f_lo, f_hi = f(lo), f(hi)
    if not f_hi <= target <= f_lo:
        raise NoSolutionError(f"target {target:.6f} outside [{f_hi:.6f}, {f_lo:.6f}] for lambda in [{lo}, {hi}]")
    for _ in range(_MAX_ITERATIONS):
        mid = (lo + hi) / 2.0
        if f(mid) > target:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2.0


def _goal_count_below(line: float) -> int:
    """0.5 -> 0, 1.5 -> 1, 2.5 -> 2. Only half-goal lines (no push)."""
    if not math.isfinite(line) or line <= 0 or (line * 2) % 2 != 1:
        raise InvariantError(f"expected a half-goal line such as 0.5, 1.5, 2.5; got {line!r}")
    return int(line - 0.5)


def solve_lambda_from_under(line: float, p_under: float, lo: float = SOLVER_LOW, hi: float = SOLVER_HIGH,
                            tol: float = SOLVER_TOLERANCE) -> float:
    """Total lambda such that P(total <= line - 0.5) = p_under (p_under already de-vigged)."""
    n = _goal_count_below(line)
    p_under = check_probability(p_under, "p_under", strict=True)
    if n == 0:
        lam = -math.log(p_under)  # closed form, doc 11.2
        if not lo <= lam <= hi:
            raise NoSolutionError(f"lambda {lam:.6f} for Under 0.5 = {p_under:.6f} is outside [{lo}, {hi}]")
        return lam
    return _bisect_decreasing(lambda lam: cdf(n, lam), p_under, lo, hi, tol)


def asian_whole_under_share(n: int, lam: float) -> float:
    """Under share of an Asian whole line n once the push (exactly n goals) is removed:
    P(total < n) / (1 - P(total = n)). n = 1: e^-lam / (1 - lam e^-lam), doc 11.3."""
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise InvariantError(f"Asian whole line must be an int >= 1, got {n!r}")
    return cdf(n - 1, lam) / (1.0 - pmf(n, lam))


def solve_lambda_from_asian_whole(n: int, under_share: float, lo: float = SOLVER_LOW, hi: float = SOLVER_HIGH,
                                  tol: float = SOLVER_TOLERANCE) -> float:
    """Total lambda reproducing the de-vigged Under share of Asian whole line n (e.g. HT 1.0 -> n = 1)."""
    under_share = check_probability(under_share, "under_share", strict=True)
    return _bisect_decreasing(lambda lam: asian_whole_under_share(n, lam), under_share, lo, hi, tol)
