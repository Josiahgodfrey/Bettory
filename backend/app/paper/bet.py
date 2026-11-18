"""The paper bet of one frozen prediction (docs/PAPER-BETTING.md §6). Pure.

Decided at the freeze, from the sealed odds only, with the live decision code:
    listed (QUALIFIED after the session cap)       otherwise no bet
    for each bookmaker with a COMPLETE sealed 15' market (both sides: the margin can be removed)
        verify(analysis with that bookmaker's quotes)   quote mode, layer 3: offered >= minimum odds and
                                                        |P - bookmaker's de-vigged P| <= max gap; the higher-EV
                                                        market of the two, never both
    the best qualifying offer: highest EV; ties -> UNDER05, then the bookmaker name
A flat stake (config/backtest.yaml estimated_value.stake): ROI is then the mean profit per bet.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace

from app.calculation.ev import Bet
from app.decision.layers import FixtureAnalysis, verify
from app.domain.enums import Decision, MarketCode
from app.domain.params import Params
from app.storage.point_in_time import OddsRow

_SIDES = {Bet.UNDER05: (MarketCode.FIRST15_GOAL, frozenset({"YES", "NO"})),
          Bet.DRAW: (MarketCode.FIRST15_RESULT, frozenset({"HOME", "DRAW", "AWAY"}))}
_OFFERED = {Bet.UNDER05: "NO", Bet.DRAW: "DRAW"}


@dataclass(frozen=True)
class PaperBet:
    market: Bet
    odds: float
    bookmaker: str
    ev: float
    stake: float


def bookmaker_quotes(odds: Sequence[OddsRow]) -> dict[str, dict[Bet, dict[str, float]]]:
    """Complete 15' markets per bookmaker (an incomplete one is not a quote: its margin is unknown)."""
    raw: dict[str, dict[MarketCode, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for o in odds:
        if o.market in (MarketCode.FIRST15_GOAL, MarketCode.FIRST15_RESULT):
            raw[o.bookmaker][o.market][o.selection] = o.odds
    out: dict[str, dict[Bet, dict[str, float]]] = {}
    for book, markets in raw.items():
        quotes = {bet: dict(markets[m]) for bet, (m, sides) in _SIDES.items() if set(markets.get(m, {})) == sides}
        if quotes:
            out[book] = quotes
    return out


def paper_bet(analysis: FixtureAnalysis | None, decision: Decision, odds: Sequence[OddsRow], params: Params,
              stake: float) -> PaperBet | None:
    if analysis is None or decision is not Decision.QUALIFIED:
        return None
    offers: list[PaperBet] = []
    for book, quotes in sorted(bookmaker_quotes(odds).items()):
        v = verify(replace(analysis, quotes=quotes, bookmaker=book), params)
        if v.decision is Decision.QUALIFIED and v.selected is not None and v.ev is not None:
            offers.append(PaperBet(v.selected, quotes[v.selected][_OFFERED[v.selected]], book, v.ev, stake))
    if not offers:
        return None
    return min(offers, key=lambda b: (-b.ev, b.market is not Bet.UNDER05, b.bookmaker))
