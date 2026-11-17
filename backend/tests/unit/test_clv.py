"""Closing line value against the fair (de-vigged, mean over complete markets) closing price."""

from __future__ import annotations

from app.calculation.ev import Bet
from app.settlement.clv import closing_line, clv_book, clv_fair
from tests.support import PARAMS


def rows(book: str, market: str, prices: dict[str, float]) -> list[dict[str, object]]:
    return [{"bookmaker": book, "market": market, "selection": s, "odds": o} for s, o in prices.items()]


def test_fair_closing_price_and_clv() -> None:
    line_rows = (rows("A", "FIRST15_GOAL", {"NO": 1.40, "YES": 3.10})
                 + rows("B", "FIRST15_GOAL", {"NO": 1.36, "YES": 3.30})
                 + rows("C", "FIRST15_GOAL", {"NO": 1.45}))               # incomplete: ignored
    line = closing_line(line_rows, Bet.UNDER05, "a", PARAMS)              # bookmaker name: case-insensitive
    assert line is not None and line.books == 2 and line.book_odds == 1.40
    assert 0.68 < line.fair_p < 0.72 and abs(line.fair_odds - 1 / line.fair_p) < 1e-12
    assert abs(clv_fair(1.50, line) - (1.50 * line.fair_p - 1)) < 1e-12 and clv_fair(1.50, line) > 0
    assert clv_fair(1.30, line) < 0
    assert clv_book(1.50, line) is not None and abs((clv_book(1.50, line) or 0) - (1.50 / 1.40 - 1)) < 1e-12
    other = closing_line(line_rows, Bet.UNDER05, "Unknown", PARAMS)
    assert other is not None and other.book_odds is None and clv_book(1.5, other) is None


def test_draw_market_and_no_usable_line() -> None:
    draw = rows("A", "FIRST15_RESULT", {"HOME": 6.0, "DRAW": 1.35, "AWAY": 7.0})     # 5 % margin
    line = closing_line(draw, Bet.DRAW, None, PARAMS)
    assert line is not None and 0.68 < line.fair_p < 0.73 and line.book_odds is None
    assert closing_line(draw, Bet.UNDER05, None, PARAMS) is None
    absurd = rows("A", "FIRST15_GOAL", {"NO": 1.01, "YES": 1.01})          # implausible margin
    assert closing_line(absurd, Bet.UNDER05, "A", PARAMS) is None
