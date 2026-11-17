"""The odds a frozen prediction saw: every price known at as_of, sealed with it (docs/PAPER-BETTING.md §5).

The rows come from PointInTimeReader.odds (latest snapshot per bookmaker, market, line and selection captured
strictly before as_of) - the same read Model C uses - so the sealed prices are exactly what the analysis could
know. The paper bet of step 3 is taken at the best sealed 15' price, never at a price seen later.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC
from typing import Any

from app.domain.enums import MarketCode
from app.storage.point_in_time import OddsRow

# the markets sealed with a prediction: the two 15' markets BETTORY bets, and the main markets behind Model C
SEALED_MARKETS = (MarketCode.FIRST15_GOAL, MarketCode.FIRST15_RESULT, MarketCode.FT_TOTAL, MarketCode.HT_TOTAL,
                  MarketCode.FT_1X2)
UNDER05 = (MarketCode.FIRST15_GOAL, "NO")  # "Goal in 1-15 minutes: No" = 0-0 after 15'
DRAW15 = (MarketCode.FIRST15_RESULT, "DRAW")  # "1x2 - 15 minutes: Draw"


@dataclass(frozen=True)
class SeenOdds:
    rows: list[dict[str, Any]]  # JSON-ready, in a fixed order
    best_under05: tuple[float, str] | None  # (odds, bookmaker)
    best_draw: tuple[float, str] | None


def _best(rows: Sequence[OddsRow], market: MarketCode, selection: str) -> tuple[float, str] | None:
    offers = [(r.odds, r.bookmaker) for r in rows if r.market is market and r.selection == selection]
    if not offers:
        return None
    top = max(o for o, _ in offers)
    return top, min(b for o, b in offers if o == top)  # ties: the alphabetically first bookmaker


def seen_odds(rows: Sequence[OddsRow]) -> SeenOdds:
    ordered = sorted(rows, key=lambda r: (r.market.value, r.bookmaker, r.line if r.line is not None else -1.0,
                                          r.selection))
    as_json = [{"bookmaker": r.bookmaker, "market": r.market.value, "line": r.line, "selection": r.selection,
                "odds": r.odds, "captured_at": r.captured_at.astimezone(UTC).isoformat()} for r in ordered]
    return SeenOdds(as_json, _best(rows, *UNDER05), _best(rows, *DRAW15))
