"""Closing line value (DATA-SOURCES-AND-MARKET-MODEL.md 9, docs/PAPER-BETTING.md 11). Pure.

The closing line of a match is the latest price of each bookmaker captured before kick-off (odds entered by hand
excluded: they are the owner's own records). For a bet on one 15' market:

    fair closing P   mean over the bookmakers with a COMPLETE closing market of the de-vigged P of the selection
    CLV (fair)       taken odds x fair closing P - 1      > 0: the bet beat the market's final, margin-free price
    CLV (book)       taken odds / that bookmaker's closing odds - 1   (only when that bookmaker's price is known)

Beating the closing line on average is the earliest sign of an edge: it needs no results.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.calculation.devig import devig
from app.calculation.ev import Bet
from app.domain.params import Params

SIDES = {Bet.UNDER05: ("FIRST15_GOAL", ("NO", "YES")), Bet.DRAW: ("FIRST15_RESULT", ("DRAW", "HOME", "AWAY"))}


@dataclass(frozen=True)
class ClosingLine:
    fair_p: float
    books: int  # bookmakers with a complete closing market
    book_odds: float | None  # the bet's bookmaker's closing price of the selection, if known

    @property
    def fair_odds(self) -> float:
        return 1.0 / self.fair_p


def closing_line(rows: Iterable[Mapping[str, object]], bet: Bet, bookmaker: str | None,
                 params: Params) -> ClosingLine | None:
    """`rows`: bookmaker, market, selection, odds (the latest per bookmaker / market / selection)."""
    market, sides = SIDES[bet]
    books: dict[str, dict[str, float]] = defaultdict(dict)
    for r in rows:
        if r["market"] == market:
            books[str(r["bookmaker"])][str(r["selection"])] = float(r["odds"])  # type: ignore[arg-type]
    v = params.value
    ps = []
    for prices in books.values():
        if set(prices) != set(sides):
            continue
        try:
            d = devig([prices[s] for s in sides], v.devig_method, min_overround=v.overround_min,
                      max_overround=v.overround_max)
        except ValueError:  # an implausible margin: not a usable closing market
            continue
        ps.append(d.probabilities[0])
    if not ps:
        return None
    own = next((p for b, p in books.items() if bookmaker and b.lower() == bookmaker.lower()), None)
    return ClosingLine(sum(ps) / len(ps), len(ps), own.get(sides[0]) if own else None)


def clv_fair(taken_odds: float, line: ClosingLine) -> float:
    return taken_odds * line.fair_p - 1.0


def clv_book(taken_odds: float, line: ClosingLine) -> float | None:
    return taken_odds / line.book_odds - 1.0 if line.book_odds else None
