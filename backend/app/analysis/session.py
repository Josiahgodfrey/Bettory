"""One session analysed at one moment: the step shared by the backtest replay and the live paper-betting freeze.

    AnalysisContext(as_of).evaluate(fixture)    point-in-time reads -> models -> price -> verdict
    rank_and_cap(session verdicts)              the 50-match cap

Keeping this in one place is what makes a frozen live prediction and its later replay comparable: both run this
function with the same params, fixtures and as_of.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection

from app.analysis.builder import AnalysisContext
from app.calculation.pricing import league_outcomes
from app.decision.layers import FixtureAnalysis, Verdict
from app.decision.ranking import rank_and_cap
from app.domain.enums import Decision
from app.domain.params import Params
from app.domain.registry import Registry
from app.storage.point_in_time import ReadCache

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionVerdict:
    match_id: UUID
    analysis: FixtureAnalysis | None
    verdict: Verdict
    decision: Decision  # after the session cap (QUALIFIED = listed in the report)


def evaluate_session(conn: Connection, params: Params, registry: Registry, as_of: datetime,
                     match_ids: Sequence[UUID], cache: ReadCache | None = None) -> list[SessionVerdict]:
    """Verdicts for the session's fixtures (in the given order) as known strictly before `as_of`."""
    ctx = AnalysisContext(conn, params, registry, as_of, cache)
    evaluated = [(m, *ctx.evaluate(m)) for m in match_ids]
    report = rank_and_cap([v for _, _, v in evaluated], params)
    final = {v.fixture_id: v.decision for v in (*report.listed, *report.over_cap, *report.not_qualified)}
    return [SessionVerdict(m, a, v, final[v.fixture_id]) for m, a, v in evaluated]


def league_baseline(a: FixtureAnalysis | None, params: Params) -> tuple[float | None, float | None]:
    """(P(0-0 at 15'), P(draw at 15')) from the competition baseline alone: the comparison price of Gate B."""
    b = a.baseline if a is not None else None
    if b is None or b.s is None:
        return None, None
    try:
        o = league_outcomes(b, params)
    except Exception:  # a baseline that cannot be priced simply has no comparison value
        log.warning("league baseline could not be priced for %s", a.fixture_id if a else "?")
        return None, None
    return o.p_00, o.p_draw
