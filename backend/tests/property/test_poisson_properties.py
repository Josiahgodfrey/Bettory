"""Properties that must hold for every input (DEVELOPMENT-PLAN.md Phase 2 tests)."""

from hypothesis import given
from hypothesis import strategies as st

from app.calculation.poisson import (
    asian_whole_under_share,
    cdf,
    score_grid,
    solve_lambda_from_asian_whole,
    solve_lambda_from_under,
)

lam15 = st.floats(min_value=0.0, max_value=1.5, allow_nan=False)            # realistic 0-15 per team
lam_ft = st.floats(min_value=0.0, max_value=3.5, allow_nan=False)           # full match per team
solvable = st.floats(min_value=0.05, max_value=5.5, allow_nan=False)
delta = st.floats(min_value=-1.0, max_value=1.0, allow_nan=False)


@given(lam15, lam15, st.floats(min_value=0.001, max_value=0.5))
def test_p00_decreases_as_lambda_increases(h: float, a: float, step: float) -> None:
    assert score_grid(h + step, a).outcomes().p_00 < score_grid(h, a).outcomes().p_00


@given(lam_ft, lam_ft)
def test_full_match_grid_is_a_distribution(h: float, a: float) -> None:
    grid = score_grid(h, a)
    assert abs(grid.mass - 1.0) <= 0.0001                                   # auto size: tail <= tolerance/10
    out = grid.outcomes()
    assert out.p_draw >= out.p_00
    assert abs(out.p_draw + out.p_home_lead + out.p_away_lead - 1.0) <= 0.0001


@given(lam15, lam15, delta)
def test_15_minute_grid_with_delta_and_draw_at_least_00(h: float, a: float, d: float) -> None:
    out = score_grid(h, a, delta=d).outcomes()
    assert out.p_draw >= out.p_00
    total = out.p_draw + out.p_home_lead + out.p_away_lead
    assert abs(total - 1.0) <= 0.001


@given(lam15, lam15, delta)
def test_delta_never_changes_00_or_total(h: float, a: float, d: float) -> None:
    base, adj = score_grid(h, a), score_grid(h, a, delta=d)
    assert adj.p(0, 0) == base.p(0, 0)
    assert abs(adj.mass - base.mass) < 1e-12


@given(solvable, st.sampled_from([0.5, 1.5, 2.5, 3.5]))
def test_under_solver_inverts_cdf(lam: float, line: float) -> None:
    p = cdf(int(line - 0.5), lam)
    if 1e-6 < p < 1 - 1e-6:   # outside this the probability carries no usable information about lambda
        assert abs(solve_lambda_from_under(line, p) - lam) < 1e-5


@given(solvable, st.integers(min_value=1, max_value=3))
def test_asian_solver_inverts_share(lam: float, n: int) -> None:
    share = asian_whole_under_share(n, lam)
    if 1e-6 < share < 1 - 1e-6:
        assert abs(solve_lambda_from_asian_whole(n, share) - lam) < 1e-5


@given(st.integers(min_value=1, max_value=3), st.floats(min_value=0.01, max_value=5.9), st.floats(0.001, 0.1))
def test_asian_share_decreases_in_lambda(n: int, lam: float, step: float) -> None:
    assert asian_whole_under_share(n, lam + step) < asian_whole_under_share(n, lam)
