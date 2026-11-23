"""Combine every source's version of one match into the trusted record (DATA-MODEL.md section 6). Pure.

- Each field comes from the highest-priority source that has it.
- Goal lists are used only if consistent with that source's FT and HT scores.
- Any disagreement on FT, HT or the goal-derived 15' score -> CONFLICT (excluded from statistics).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.calculation.recovery import FifteenMinute, derive_fifteen
from app.domain.enums import FixtureStatus, ReconciliationStatus, Severity, SourceCode
from app.domain.models import Score, SourceGoal, SourceMatch

# ESPN (docs/ESPN.md) ranks after the established sources everywhere: it fills gaps; where another source has the
# value, ESPN's version only takes part in the agreement check (a disagreement is a CONFLICT, as for any source).
SCORE_PRIORITY = (
    SourceCode.API_FOOTBALL,
    SourceCode.FOOTBALL_DATA_ORG,
    SourceCode.OPENFOOTBALL,
    SourceCode.ESPN,
    SourceCode.FOOTBALL_DATA_CO_UK,
)
GOALS_PRIORITY = (SourceCode.API_FOOTBALL, SourceCode.OPENFOOTBALL, SourceCode.ESPN)
KICKOFF_PRIORITY = (
    SourceCode.API_FOOTBALL,
    SourceCode.FOOTBALL_DATA_ORG,
    SourceCode.ESPN,
    SourceCode.FOOTBALL_DATA_CO_UK,
    SourceCode.OPENFOOTBALL,
)


@dataclass(frozen=True)
class Issue:
    code: str
    severity: Severity
    detail: dict[str, object]


@dataclass(frozen=True)
class TrustedMatch:
    status: FixtureStatus
    kickoff_utc: datetime | None
    round: str | None
    ft: Score | None
    ht: Score | None
    fifteen: FifteenMinute
    goals: tuple[SourceGoal, ...]
    reconciliation: ReconciliationStatus
    scores_source: SourceCode | None
    goals_source: SourceCode | None
    issues: tuple[Issue, ...] = field(default=())


def _rank(order: tuple[SourceCode, ...], source: SourceCode) -> int:
    return order.index(source) if source in order else len(order)


def reconcile(records: list[SourceMatch], cutoff_minute: int) -> TrustedMatch:
    """`records`: the latest record per source for one match (at least one)."""
    if not records:
        raise ValueError("reconcile needs at least one source record")
    if len({r.source for r in records}) != len(records):
        raise ValueError("reconcile expects one record per source")

    issues: list[Issue] = []
    by_score = sorted(records, key=lambda r: _rank(SCORE_PRIORITY, r.source))
    by_kickoff = sorted(records, key=lambda r: _rank(KICKOFF_PRIORITY, r.source))

    finished = [r for r in by_score if r.status is FixtureStatus.FINISHED and r.ft is not None]
    lead = finished[0] if finished else by_score[0]
    status = lead.status
    kickoff = next((r.kickoff_utc for r in by_kickoff if r.kickoff_utc is not None), None)
    round_ = next((r.round for r in by_score if r.round), None)

    ft = lead.ft
    ht = next((r.ht for r in finished if r.ht is not None), None) if finished else None
    conflict = False

    # FT / HT agreement between finished sources
    for r in finished[1:]:
        if r.ft != ft:
            conflict = True
            issues.append(Issue("FT_CONFLICT", Severity.BLOCKING,
                                {"sources": [lead.source, r.source], "values": [str(ft), str(r.ft)]}))
        if r.ht is not None and ht is not None and r.ht != ht:
            conflict = True
            issues.append(Issue("HT_CONFLICT", Severity.BLOCKING,
                                {"sources": [r.source], "values": [str(ht), str(r.ht)]}))

    # Goal lists: consistent ones only, highest priority wins, others must agree on 15'
    usable: list[SourceMatch] = []
    for r in sorted(records, key=lambda r: _rank(GOALS_PRIORITY, r.source)):
        if not r.has_goal_minutes or r.status is not FixtureStatus.FINISHED:
            continue
        if r.goals_consistent():
            usable.append(r)
        else:
            issues.append(Issue("GOALS_INCONSISTENT", Severity.WARN, {"source": r.source}))

    goals_source = usable[0] if usable else None
    goals = goals_source.goals if goals_source else None
    fifteen = derive_fifteen(status, ft, ht, goals, cutoff_minute)

    for other in usable[1:]:
        alt = derive_fifteen(status, other.ft, other.ht, other.goals, cutoff_minute)
        if alt.h15 != fifteen.h15:
            conflict = True
            issues.append(Issue("H15_CONFLICT", Severity.BLOCKING,
                                {"sources": [goals_source.source if goals_source else None, other.source],
                                 "values": [str(fifteen.h15), str(alt.h15)]}))

    if conflict:
        reconciliation = ReconciliationStatus.CONFLICT
    elif len(finished) >= 2:
        reconciliation = ReconciliationStatus.AGREED
    else:
        reconciliation = ReconciliationStatus.SINGLE_SOURCE

    return TrustedMatch(
        status=status,
        kickoff_utc=kickoff,
        round=round_,
        ft=ft if status is FixtureStatus.FINISHED else None,
        ht=ht if status is FixtureStatus.FINISHED else None,
        fifteen=fifteen,
        goals=goals or (),
        reconciliation=reconciliation,
        scores_source=lead.source if finished else None,
        goals_source=goals_source.source if goals_source else None,
        issues=tuple(issues),
    )
