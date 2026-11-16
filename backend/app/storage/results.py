"""Fixtures of a period with their stored results, for settlement and the backtest.

This is deliberately NOT part of PointInTimeReader: a result is known only after the match, so it must never
reach an analysis. The backtest reads it here, separately, and uses it only to settle verdicts that were
already made with the point-in-time reader.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection, text

from app.domain.enums import DataStatus, FixtureStatus, ReconciliationStatus

# Played or abandoned fixtures. POSTPONED / CANCELLED fixtures have no kick-off to analyse at; a live run
# would have seen them as SCHEDULED, and the bet (if any) would be void.
_STATUSES = (FixtureStatus.FINISHED.value, FixtureStatus.ABANDONED.value)


@dataclass(frozen=True)
class FixtureResult:
    id: UUID
    competition_code: str
    season_label: str
    kickoff_utc: datetime
    status: FixtureStatus
    data_status: DataStatus
    reconciliation_status: ReconciliationStatus
    h15: tuple[int, int] | None
    goal_before_15: bool | None


def fixtures_between(conn: Connection, start_utc: datetime, end_utc: datetime,
                     competition_codes: Sequence[str]) -> list[FixtureResult]:
    """Fixtures with start_utc <= kick-off < end_utc in the given competitions, ordered by kick-off."""
    if start_utc.utcoffset() is None or end_utc.utcoffset() is None:
        raise ValueError("timezone-aware bounds required")
    rows = conn.execute(text("""
        select m.id, c.code, s.label, m.kickoff_utc, m.status, m.data_status, m.reconciliation_status,
               m.h15_home, m.h15_away, m.goal_before_15
        from core.matches m
        join core.competition_seasons s on s.id = m.competition_season_id
        join core.competitions c on c.id = s.competition_id
        where m.kickoff_utc >= :start and m.kickoff_utc < :end
          and c.code = any(:codes) and m.status::text = any(:statuses)
        order by m.kickoff_utc, m.id
    """), {"start": start_utc, "end": end_utc, "codes": list(competition_codes),
           "statuses": list(_STATUSES)}).mappings().all()
    return [FixtureResult(
        id=r["id"], competition_code=r["code"], season_label=r["label"], kickoff_utc=r["kickoff_utc"],
        status=FixtureStatus(r["status"]), data_status=DataStatus(r["data_status"]),
        reconciliation_status=ReconciliationStatus(r["reconciliation_status"]),
        h15=(int(r["h15_home"]), int(r["h15_away"])) if r["h15_home"] is not None else None,
        goal_before_15=r["goal_before_15"],
    ) for r in rows]
