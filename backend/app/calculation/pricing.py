"""15' pricing engine, formula v1.4 (ARCHITECTURE.md O4; FIFTEEN-MINUTE-ODDS-CALCULATION.md 3-8). Pure.

    Step 1  mu_home = w_team x mu_home_team + w_mkt x mu_home_mkt        (whole-match expected goals)
            no usable market (Model C unavailable / MARKET_INCONSISTENT) -> w_team = 1
    Step 2  s = competition share of goals in 0-15 (baselines)
    Step 3  lambda15_home = s x mu_home x I_home ;  lambda15_away = s x mu_away x I_away
            I = Model B v2 index ^ w_I, 1.0 when Model B v2 is unavailable
    Step 4  score grid (optional delta, Draw only) -> P(0-0) = P(Under 0.5), P(Draw), leads
    Step 5  fair odds = 1 / P ; minimum odds = (1 + EV_min) / P

mu_team comes from Model A (FT-equivalent, A_FT + A_HT). mu_mkt comes from Model C: its lambda15 / s
(FT-equivalent of what the market implies for 0-15); when Model C has no 1X2 split, the market total is
split like the team model. P_team (w_mkt = 0) and P_market (Model C) are returned for the agreement check
(pricing doc 10, condition 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.calculation.ev import fair_odds, min_odds
from app.calculation.invariants import InvariantError, check_lambda, check_probability
from app.calculation.models import Expectancy
from app.calculation.models.model_a import ModelAResult
from app.calculation.models.model_b2 import ModelB2Result
from app.calculation.models.model_c import ModelCResult
from app.calculation.poisson import Outcomes, score_grid
from app.domain.params import Params


class MuSource(StrEnum):
    TEAM = "TEAM"
    MARKET = "MARKET"
    BLEND = "BLEND"


@dataclass(frozen=True)
class FairOdds:
    under05: float
    over05: float
    draw: float
    home: float  # home team leads at 15'
    away: float


@dataclass(frozen=True)
class Price:
    """One fixture's 15' price (fifteen_min_prices, pricing doc 14.1)."""

    pricing_version: str  # formula version
    params_version: str
    mu: Expectancy  # blended whole-match expectancies
    mu_source: MuSource
    w_team: float
    w_mkt: float
    share_s: float
    index_home: float  # factors I applied
    index_away: float
    lambda15: Expectancy
    delta: float
    outcomes: Outcomes
    fair: FairOdds
    min_odds_under05: float
    min_odds_draw: float
    p_team_00: float  # team-only price (w_mkt = 0), same index and s
    p_team_draw: float
    p_market_00: float | None  # Model C
    p_market_draw: float | None
    notes: tuple[str, ...]


def _outcomes(mu: Expectancy, s: float, i_home: float, i_away: float, delta: float) -> tuple[Expectancy, Outcomes]:
    l15 = Expectancy(home=check_lambda(s * mu.home * i_home, "lambda15_home"),
                     away=check_lambda(s * mu.away * i_away, "lambda15_away"))
    return l15, score_grid(l15.home, l15.away, delta=delta).outcomes()


def _market_mu(c: ModelCResult, s: float, team_mu: Expectancy) -> Expectancy:
    if c.lambda15 is None:
        raise InvariantError("Model C result without lambda15")
    if c.lambda15_home is not None and c.lambda15_away is not None:
        return Expectancy(home=c.lambda15_home / s, away=c.lambda15_away / s)
    share = team_mu.home / team_mu.total if team_mu.total > 0 else 0.5
    total = c.lambda15 / s
    return Expectancy(home=total * share, away=total * (1.0 - share))


def price(model_a: ModelAResult, s: float, params: Params, model_b2: ModelB2Result | None = None,
          model_c: ModelCResult | None = None) -> Price:
    """Weights: blend.team_weight / market_weight; delta: state_dependence.delta; min odds: value.ev_min."""
    s = check_probability(s, "s", strict=True)
    w_team, w_mkt = params.blend.team_weight, params.blend.market_weight
    delta, ev_min = params.state_dependence.delta, params.value.ev_min
    if abs(model_a.s - s) > 1e-12:
        raise InvariantError("Model A was computed with a different s")
    notes: list[str] = []

    i_home = model_b2.factor_home if model_b2 is not None else 1.0
    i_away = model_b2.factor_away if model_b2 is not None else 1.0
    if model_b2 is None:
        notes.append("Model B v2 unavailable: index factors 1.0")
    else:
        notes.extend(model_b2.flags)

    team_mu = model_a.mu
    market_ok = model_c is not None and model_c.available
    if not market_ok:
        notes.append("no usable market (Model C): w_team = 1" if model_c is None
                     else f"Model C {model_c.status}: w_team = 1")
        w_team, w_mkt = 1.0, 0.0
    if w_mkt == 0.0:
        mu, source = team_mu, MuSource.TEAM
    else:
        if model_c is None:  # unreachable: market_ok implies a Model C result
            raise InvariantError("market weight without a Model C result")
        mkt = _market_mu(model_c, s, team_mu)
        if model_c.lambda15_home is None:
            notes.append("Model C without 1X2: market total split like the team model")
        mu = Expectancy(home=w_team * team_mu.home + w_mkt * mkt.home, away=w_team * team_mu.away + w_mkt * mkt.away)
        source = MuSource.MARKET if w_team == 0.0 else MuSource.BLEND

    l15, out = _outcomes(mu, s, i_home, i_away, delta)
    _, team_out = _outcomes(team_mu, s, i_home, i_away, delta)
    fair = FairOdds(under05=fair_odds(out.p_00), over05=fair_odds(out.p_over05), draw=fair_odds(out.p_draw),
                    home=fair_odds(out.p_home_lead), away=fair_odds(out.p_away_lead))
    return Price(
        pricing_version=params.formula_version, params_version=params.params_version,
        mu=mu, mu_source=source, w_team=w_team, w_mkt=w_mkt, share_s=s,
        index_home=i_home, index_away=i_away, lambda15=l15, delta=delta, outcomes=out, fair=fair,
        min_odds_under05=min_odds(out.p_00, ev_min), min_odds_draw=min_odds(out.p_draw, ev_min),
        p_team_00=team_out.p_00, p_team_draw=team_out.p_draw,
        p_market_00=model_c.p00 if market_ok and model_c else None,
        p_market_draw=model_c.p_draw if market_ok and model_c else None,
        notes=tuple(notes),
    )
