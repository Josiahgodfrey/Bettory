"""Model C: market-implied 0-15 goal expectancy from the main markets. Pure.

FIFTEEN-MINUTE-ODDS-CALCULATION.md 11 and DATA-SOURCES-AND-MARKET-MODEL.md 6:
- 11.1 inputs, any subset: FT 1X2 (split), FT O/U (total), HT O/U 0.5 / 1.5 (first-half total),
       HT Asian whole line 1.0 (first-half total with push). Every quote is one bookmaker's complete market,
       de-vigged with the power method by default.
- 11.2 single half-goal line -> total lambda (closed form for 0.5, bisection otherwise)
- 11.3 Asian whole line -> total lambda with the push removed
- 11.4 1X2 (+ FT totals) -> mu_home, mu_away by least squares on log mu (L-BFGS-B, mu in [0.05, 6]);
       the HT line is used directly with r when available (closer in time to 0-15), FT lines with s
- 11.5 / 6.4 lines whose implied totals differ by more than 20% -> MARKET_INCONSISTENT (w_mkt = 0)
- 6.4 line priority HT O/U 0.5 -> HT O/U 1.5 -> FT O/U 2.5; lines that agree are averaged

The 15' markets themselves (FIRST15_*) are the markets BETTORY bets on; they are priced against this model
and must not be inputs to it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from scipy.optimize import minimize

from app.calculation.devig import devig
from app.calculation.invariants import InvariantError, check_probability
from app.calculation.poisson import (
    cdf,
    score_grid,
    solve_lambda_from_asian_whole,
    solve_lambda_from_under,
)
from app.domain.enums import MarketCode
from app.domain.params import Params

MU_LOW, MU_HIGH = 0.05, 6.0  # doc 11.4 bounds
_TOTAL_SELECTIONS = frozenset({"OVER", "UNDER"})
_1X2_SELECTIONS = ("HOME", "DRAW", "AWAY")
_INPUT_MARKETS = {MarketCode.FT_1X2, MarketCode.FT_TOTAL, MarketCode.HT_TOTAL, MarketCode.HT_ASIAN_TOTAL}
# The lines Model C uses come from params model_c.input_lines (doc 11.1). Extreme lines (odds near 1)
# carry little information and sit in the tail where the Poisson assumption is weakest; any line not
# listed is reported as ignored, never used.


class MarketStatus(StrEnum):  # verification_results.market_consistency
    OK = "OK"
    MARKET_INCONSISTENT = "MARKET_INCONSISTENT"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class MarketQuote:
    """All sides of one bookmaker's market, e.g. HT_TOTAL 0.5 {OVER: 1.40, UNDER: 2.80}."""

    market: MarketCode
    prices: Mapping[str, float]
    line: float | None = None
    bookmaker: str = ""


@dataclass(frozen=True)
class LineEstimate:
    market: MarketCode
    line: float
    bookmaker: str
    p_under: float  # de-vigged (Asian: under share with the push removed)
    lam_period: float  # total lambda of the line's period (HT or FT)
    lambda15: float  # r x lambda_HT or s x lambda_FT


@dataclass(frozen=True)
class ModelCResult:
    status: MarketStatus
    reasons: tuple[str, ...]
    lines: tuple[LineEstimate, ...] = ()
    source: str | None = None  # e.g. "HT_TOTAL 0.5 + HT_TOTAL 1.5"
    disagreement: float | None = None  # max / min - 1 of the lines' lambda15
    lambda15: float | None = None  # total
    home_share: float | None = None  # mu_home / (mu_home + mu_away) from 1X2
    mu_home: float | None = None  # FT expected goals fitted to 1X2 (+ FT totals)
    mu_away: float | None = None
    fit_rmse: float | None = None
    lambda15_home: float | None = None
    lambda15_away: float | None = None
    p00: float | None = None  # P_C(Under 0.5 at 15')
    p_draw: float | None = None  # P_C(Draw at 15'), needs the split

    @property
    def available(self) -> bool:
        return self.status is MarketStatus.OK and self.p00 is not None


# --- single lines ---------------------------------------------------------------------------------


def _check_quote(q: MarketQuote) -> None:
    if q.market not in _INPUT_MARKETS:
        raise InvariantError(f"{q.market} is not a Model C input (15' markets are priced, not used as inputs)")
    if q.market is MarketCode.FT_1X2:
        if set(q.prices) != set(_1X2_SELECTIONS):
            raise InvariantError(f"1X2 quote needs exactly HOME / DRAW / AWAY, got {sorted(q.prices)}")
        return
    if set(q.prices) != _TOTAL_SELECTIONS:
        raise InvariantError(f"totals quote needs exactly OVER / UNDER, got {sorted(q.prices)}")
    if q.line is None or not math.isfinite(q.line) or q.line <= 0:
        raise InvariantError(f"totals quote needs a positive line, got {q.line!r}")
    whole = float(q.line).is_integer()
    half = (q.line * 2) % 2 == 1
    if q.market is MarketCode.HT_ASIAN_TOTAL and not (whole or half):
        raise InvariantError(f"quarter Asian lines are not supported: {q.line}")
    if q.market in (MarketCode.HT_TOTAL, MarketCode.FT_TOTAL) and not half:
        raise InvariantError(f"O/U line must be a half-goal line: {q.line}")


def _devig(odds: tuple[float, ...], params: Params) -> tuple[float, ...]:
    v = params.value
    return devig(odds, params.model_c.devig_method, min_overround=v.overround_min,
                 max_overround=v.overround_max).probabilities


def line_estimate(q: MarketQuote, r: float | None, s: float | None, params: Params) -> LineEstimate:
    """One totals quote -> lambda of its period -> lambda15. Raises ValueError subclasses if unusable."""
    _check_quote(q)
    if q.market is MarketCode.FT_1X2 or q.line is None:
        raise InvariantError("line_estimate takes totals quotes")
    p_under = _devig((q.prices["UNDER"], q.prices["OVER"]), params)[0]
    if q.market is MarketCode.HT_ASIAN_TOTAL and float(q.line).is_integer():
        lam = solve_lambda_from_asian_whole(int(q.line), p_under)
    else:
        lam = solve_lambda_from_under(q.line, p_under)
    share = s if q.market is MarketCode.FT_TOTAL else r
    if share is None:
        raise InvariantError(f"no {'s' if q.market is MarketCode.FT_TOTAL else 'r'} for {q.market}")
    return LineEstimate(q.market, q.line, q.bookmaker, p_under, lam, check_probability(share, "share") * lam)


# --- 1X2 split ------------------------------------------------------------------------------------


def _model_probs(mu_h: float, mu_a: float, ft_lines: Sequence[float]) -> list[float]:
    out = score_grid(mu_h, mu_a).outcomes()
    probs = [out.p_home_lead, out.p_draw, out.p_away_lead]
    probs += [cdf(int(line - 0.5), mu_h + mu_a) for line in ft_lines]
    return probs


def fit_mu(p_1x2: tuple[float, float, float],
           ft_totals: Sequence[tuple[float, float]] = ()) -> tuple[float, float, float]:
    """doc 11.4: (mu_home, mu_away, rmse) minimising squared error to de-vigged 1X2 (+ FT under probabilities)."""
    lines = [line for line, _ in ft_totals]
    target = [*p_1x2, *(p for _, p in ft_totals)]

    def loss(x: Sequence[float]) -> float:
        probs = _model_probs(math.exp(x[0]), math.exp(x[1]), lines)
        return math.fsum((m - t) ** 2 for m, t in zip(probs, target, strict=True))

    total0 = 2.6 if not ft_totals else solve_lambda_from_under(ft_totals[0][0], ft_totals[0][1])
    share0 = 0.5 + 0.5 * (p_1x2[0] - p_1x2[2])
    x0 = [math.log(min(max(total0 * share0, MU_LOW), MU_HIGH)),
          math.log(min(max(total0 * (1 - share0), MU_LOW), MU_HIGH))]
    bounds = [(math.log(MU_LOW), math.log(MU_HIGH))] * 2
    res = minimize(loss, x0, method="L-BFGS-B", bounds=bounds, options={"ftol": 1e-14, "gtol": 1e-10})
    if not res.success:  # doc 11.4: "L-BFGS-B (or Powell)"
        res = minimize(loss, x0, method="Powell", bounds=bounds, options={"xtol": 1e-10, "ftol": 1e-14})
    if not res.success:
        raise InvariantError(f"1X2 fit did not converge: {res.message}")
    return math.exp(res.x[0]), math.exp(res.x[1]), math.sqrt(float(res.fun) / len(target))


# --- Model C --------------------------------------------------------------------------------------

_PRIORITY = (MarketCode.HT_TOTAL, MarketCode.HT_ASIAN_TOTAL, MarketCode.FT_TOTAL)


def model_c(quotes: Sequence[MarketQuote], r: float | None, s: float | None, params: Params) -> ModelCResult:
    """`r` / `s`: the fixture competition's shares (CompetitionBaseline). Quotes that cannot be used are
    listed in `reasons`; the model is UNAVAILABLE when no totals line or 1X2 is usable."""
    max_line_disagreement = params.model_c.max_line_disagreement
    input_lines = params.model_c.input_lines
    reasons: list[str] = []
    lines: list[LineEstimate] = []
    p_1x2: tuple[float, float, float] | None = None
    for q in quotes:
        _check_quote(q)  # caller errors (wrong market / selections) are raised, never skipped
        try:
            if q.market is MarketCode.FT_1X2:
                if p_1x2 is not None:
                    reasons.append(f"second 1X2 quote ignored ({q.bookmaker or 'unnamed'})")
                    continue
                d = _devig(tuple(q.prices[k] for k in _1X2_SELECTIONS), params)
                p_1x2 = (d[0], d[1], d[2])
            elif q.line not in input_lines.get(q.market, ()):
                reasons.append(f"{q.market} {q.line:g} {q.bookmaker}: line not a Model C input (doc 11.1)".strip())
            else:
                lines.append(line_estimate(q, r, s, params))
        except ValueError as exc:  # MarketError, NoSolutionError, missing share
            reasons.append(f"{q.market} {q.line or ''} {q.bookmaker}: {exc}".strip())

    # 1X2 split (and FT-total-only fallback) ----------------------------------------------------------
    mu_h = mu_a = rmse = None
    ft_totals = [(e.line, e.p_under) for e in lines if e.market is MarketCode.FT_TOTAL]
    if p_1x2 is not None:
        try:
            mu_h, mu_a, rmse = fit_mu(p_1x2, ft_totals)
        except ValueError as exc:
            reasons.append(f"1X2 fit failed: {exc}")
    if not lines and mu_h is not None and mu_a is not None:
        if s is None:
            reasons.append("1X2 only, but no s to convert FT goals to 0-15")
        else:  # 1X2 alone fixes both mu: total from the fit
            lines_total = s * (mu_h + mu_a)
            return _finish(MarketStatus.OK, reasons, (), "FT_1X2", None, lines_total, mu_h, mu_a, rmse)
    if not lines:
        return ModelCResult(MarketStatus.UNAVAILABLE, (*reasons, "no usable totals line or 1X2"))

    # consistency across every usable line (doc 11.5) --------------------------------------------------
    l15 = [e.lambda15 for e in lines]
    disagreement = max(l15) / min(l15) - 1.0 if min(l15) > 0 else math.inf
    if disagreement > max_line_disagreement:
        return ModelCResult(MarketStatus.MARKET_INCONSISTENT,
                            (*reasons, f"lines disagree by {disagreement:.0%} (> {max_line_disagreement:.0%})"),
                            tuple(lines), disagreement=disagreement)

    # total from the highest-priority period: HT lines (with r) before FT lines (with s) -------------------
    best = next(m for m in _PRIORITY if any(e.market is m for e in lines))
    period_markets = {MarketCode.HT_TOTAL, MarketCode.HT_ASIAN_TOTAL} if best is not MarketCode.FT_TOTAL \
        else {MarketCode.FT_TOTAL}
    used = sorted((e for e in lines if e.market in period_markets), key=lambda e: (_PRIORITY.index(e.market), e.line))
    total = math.fsum(e.lambda15 for e in used) / len(used)
    source = " + ".join(dict.fromkeys(f"{e.market} {e.line:g}" for e in used))
    return _finish(MarketStatus.OK, reasons, tuple(lines), source, disagreement, total, mu_h, mu_a, rmse)


def _finish(status: MarketStatus, reasons: list[str], lines: tuple[LineEstimate, ...], source: str,
            disagreement: float | None, total: float, mu_h: float | None, mu_a: float | None,
            rmse: float | None) -> ModelCResult:
    p00 = check_probability(math.exp(-total), "P_C")
    if mu_h is None or mu_a is None:
        return ModelCResult(status, (*reasons, "no 1X2: home/away split unknown, Draw not priced by Model C"),
                            lines, source, disagreement, total, p00=p00)
    share = mu_h / (mu_h + mu_a)
    l_home, l_away = total * share, total * (1 - share)
    p_draw = score_grid(l_home, l_away).outcomes().p_draw
    return ModelCResult(status, tuple(reasons), lines, source, disagreement, total, share, mu_h, mu_a, rmse,
                        l_home, l_away, p00, check_probability(p_draw, "P_C draw"))
