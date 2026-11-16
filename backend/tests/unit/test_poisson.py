"""Poisson module: distribution, grid, adjustment, solvers and fail-closed behaviour."""

import math

import pytest
from scipy.stats import poisson as sp

from app.calculation.invariants import InvariantError, check_fair_odds, check_grid, check_probability
from app.calculation.poisson import (
    NoSolutionError,
    asian_whole_under_share,
    cdf,
    grid_size,
    pmf,
    score_grid,
    solve_lambda_from_asian_whole,
    solve_lambda_from_under,
)


@pytest.mark.parametrize("lam", [0.01, 0.3125, 1.0, 2.7, 6.0])
def test_pmf_and_cdf_match_scipy(lam: float) -> None:
    for k in range(12):
        assert pmf(k, lam) == pytest.approx(sp.pmf(k, lam), rel=1e-12, abs=1e-300)
        assert cdf(k, lam) == pytest.approx(sp.cdf(k, lam), rel=1e-12)


def test_lambda_zero() -> None:
    assert pmf(0, 0.0) == 1.0 and pmf(3, 0.0) == 0.0 and cdf(0, 0.0) == 1.0
    out = score_grid(0.0, 0.0).outcomes()
    assert out.p_00 == 1.0 and out.p_draw == 1.0 and out.p_home_lead == 0.0


@pytest.mark.parametrize("bad", [-0.1, math.nan, math.inf, "0.3", True])
def test_bad_lambda_rejected(bad: object) -> None:
    with pytest.raises(InvariantError):
        score_grid(bad, 0.1)  # type: ignore[arg-type]


def test_bad_goal_count_rejected() -> None:
    for k in (-1, 1.0, True):
        with pytest.raises(InvariantError):
            pmf(k, 1.0)  # type: ignore[arg-type]


def test_grid_too_small_is_rejected_not_renormalised() -> None:
    with pytest.raises(InvariantError, match="sums to"):
        score_grid(3.0, 3.0, max_goals=4)


def test_grid_size_follows_lambda() -> None:
    assert grid_size(0.175, 0.1375) == 4                     # doc 7.1: 0..4 is enough for 0-15
    assert grid_size(3.5, 3.5) > 10
    with pytest.raises(InvariantError, match="too large"):
        grid_size(40.0, 1.0)
    with pytest.raises(InvariantError):
        score_grid(0.2, 0.2, max_goals=0)


def test_delta_too_large_for_lambda_rejected() -> None:
    with pytest.raises(InvariantError, match="delta too large"):
        score_grid(3.0, 3.0, delta=1.0)


def test_cdf_negative_n_and_bad_n() -> None:
    assert cdf(-1, 1.0) == 0.0
    with pytest.raises(InvariantError):
        cdf(1.5, 1.0)  # type: ignore[arg-type]
    with pytest.raises(InvariantError):
        asian_whole_under_share(0, 1.0)


def test_grid_outside_cells_are_zero() -> None:
    assert score_grid(0.2, 0.2).p(50, 0) == 0.0


def test_outcomes_partition_the_grid() -> None:
    out = score_grid(1.4, 1.1).outcomes()
    assert out.p_draw + out.p_home_lead + out.p_away_lead == pytest.approx(1.0, abs=1e-4)   # auto-size tail
    assert out.p_00 + out.p_over05 == 1.0


def test_delta_moves_probability_from_1_0_and_0_1_to_1_1() -> None:
    base, adj = score_grid(0.175, 0.1375), score_grid(0.175, 0.1375, delta=0.2)
    assert adj.p(1, 1) == pytest.approx(base.p(1, 1) * 1.2)
    taken = base.p(1, 0) + base.p(0, 1) - adj.p(1, 0) - adj.p(0, 1)
    assert taken == pytest.approx(base.p(1, 1) * 0.2)
    assert (adj.p(1, 0) / adj.p(0, 1)) == pytest.approx(base.p(1, 0) / base.p(0, 1))   # proportional
    assert adj.p(0, 0) == base.p(0, 0)                                                # Under 0.5 unaffected
    assert adj.mass == pytest.approx(base.mass)
    assert adj.outcomes().p_draw > base.outcomes().p_draw


@pytest.mark.parametrize("delta", [1.5, -1.5, math.nan])
def test_bad_delta_rejected(delta: float) -> None:
    with pytest.raises(InvariantError):
        score_grid(0.2, 0.2, delta=delta)


@pytest.mark.parametrize(("line", "lam"), [(0.5, 0.3125), (1.5, 1.2), (2.5, 2.6), (3.5, 3.1)])
def test_under_solver_round_trip(line: float, lam: float) -> None:
    p_under = cdf(int(line - 0.5), lam)
    assert solve_lambda_from_under(line, p_under) == pytest.approx(lam, abs=1e-7)


def test_under_15_equation_from_doc() -> None:
    lam = solve_lambda_from_under(1.5, 0.60)
    assert math.exp(-lam) * (1 + lam) == pytest.approx(0.60, abs=1e-9)


@pytest.mark.parametrize("line", [1.0, 0.0, -0.5, 0.25, math.nan])
def test_non_half_lines_rejected(line: float) -> None:
    with pytest.raises(InvariantError):
        solve_lambda_from_under(line, 0.5)


@pytest.mark.parametrize("p", [0.0, 1.0, 1.2, -0.1])
def test_impossible_probability_rejected(p: float) -> None:
    with pytest.raises(InvariantError):
        solve_lambda_from_under(1.5, p)


def test_no_solution_inside_interval() -> None:
    with pytest.raises(NoSolutionError):
        solve_lambda_from_under(0.5, 0.999)           # lambda 0.001 < 0.01
    with pytest.raises(NoSolutionError):
        solve_lambda_from_under(2.5, 0.0001)          # needs lambda > 6


def test_asian_whole_line_from_doc() -> None:
    lam = 0.9
    share = asian_whole_under_share(1, lam)
    assert share == pytest.approx(math.exp(-lam) / (1 - lam * math.exp(-lam)))
    assert solve_lambda_from_asian_whole(1, share) == pytest.approx(lam, abs=1e-7)
    assert solve_lambda_from_asian_whole(2, asian_whole_under_share(2, 2.2)) == pytest.approx(2.2, abs=1e-7)


def test_invariant_helpers() -> None:
    check_grid([[0.5, 0.25], [0.25, 0.0]])
    with pytest.raises(InvariantError):
        check_grid([[0.5, 0.6]])
    with pytest.raises(InvariantError):
        check_grid([[1.1, -0.1]])
    with pytest.raises(InvariantError):
        check_probability(0.0, strict=True)
    with pytest.raises(InvariantError):
        check_fair_odds(1.0)
    assert check_fair_odds(1.367) == 1.367
