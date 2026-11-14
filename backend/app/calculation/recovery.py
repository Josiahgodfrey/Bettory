"""15-minute fields from a match's goals and scores (DATA-MODEL.md section 7). Pure.

Rules, in order:
1. Not finished -> PENDING (scheduled/live/postponed/cancelled) or INCOMPLETE (abandoned/awarded).
2. A consistent goal list (counts = FT, first-half goals = HT) -> COMPLETE.
3. Half-time 0-0 (or full-time 0-0) -> no goal before 15' -> DERIVED 0-0.
4. Anything else -> INCOMPLETE (never assumed 0-0).

Cutoff: a goal belongs to the 0-15 period when its minute label is <= `cutoff_minute`
(provider label "15'" = 14:00-14:59 elapsed). Stoppage time does not occur within 0-15.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import DataStatus, FixtureStatus, Side
from app.domain.models import Score, SourceGoal

DEFAULT_CUTOFF_MINUTE = 15
_UNPLAYED = {FixtureStatus.SCHEDULED, FixtureStatus.LIVE, FixtureStatus.POSTPONED, FixtureStatus.CANCELLED}


@dataclass(frozen=True)
class FifteenMinute:
    data_status: DataStatus
    h15: Score | None
    goal_before_15: bool | None
    first_goal: SourceGoal | None


def derive_fifteen(
    status: FixtureStatus,
    ft: Score | None,
    ht: Score | None,
    goals: tuple[SourceGoal, ...] | None,
    cutoff_minute: int = DEFAULT_CUTOFF_MINUTE,
) -> FifteenMinute:
    """`goals` must be None unless the caller has verified the list is complete and consistent."""
    if status in _UNPLAYED:
        return FifteenMinute(DataStatus.PENDING, None, None, None)
    if status is not FixtureStatus.FINISHED or ft is None:
        return FifteenMinute(DataStatus.INCOMPLETE, None, None, None)

    if goals is not None:
        ordered = sorted(goals, key=lambda g: g.sort_key)
        early = [g for g in ordered if g.minute <= cutoff_minute]
        h15 = Score(
            home=sum(g.side is Side.HOME for g in early),
            away=sum(g.side is Side.AWAY for g in early),
        )
        return FifteenMinute(DataStatus.COMPLETE, h15, h15.total > 0, ordered[0] if ordered else None)

    if (ht is not None and ht.total == 0) or ft.total == 0:
        return FifteenMinute(DataStatus.DERIVED, Score(home=0, away=0), False, None)

    return FifteenMinute(DataStatus.INCOMPLETE, None, None, None)
