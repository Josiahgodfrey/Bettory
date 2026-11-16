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


@dataclass(frozen=True)
class SeasonShare:
    """Observed share of goals before the 15' cutoff (descriptive; never used by an analysis)."""

    competition: str
    season: str
    matches: int  # finished, not CONFLICT, 15' score known (COMPLETE / DERIVED)
    goals_15: int
    goals_ht: int  # in the matches that also have a half-time score
    goals_15_with_ht: int
    goals_ft: int

    @property
    def s(self) -> float | None:  # 15' goals / full-time goals
        return self.goals_15 / self.goals_ft if self.goals_ft else None

    @property
    def r(self) -> float | None:  # 15' goals / half-time goals
        return self.goals_15_with_ht / self.goals_ht if self.goals_ht else None


def season_shares(conn: Connection, keys: Sequence[tuple[str, str]]) -> list[SeasonShare]:
    """Per (competition code, season label)."""
    if not keys:
        return []
    rows = conn.execute(text("""
        select c.code, s.label, count(*) as n,
               sum(m.h15_home + m.h15_away) as g15, sum(m.ft_home + m.ft_away) as gft,
               coalesce(sum(m.ht_home + m.ht_away) filter (where m.ht_home is not null), 0) as ght,
               coalesce(sum(m.h15_home + m.h15_away) filter (where m.ht_home is not null), 0) as g15ht
        from core.matches m
        join core.competition_seasons s on s.id = m.competition_season_id
        join core.competitions c on c.id = s.competition_id
        where m.status = 'FINISHED' and m.reconciliation_status <> 'CONFLICT'
          and m.data_status in ('COMPLETE', 'DERIVED') and m.h15_home is not null
          and (c.code || '|' || s.label) = any(:k)
        group by c.code, s.label order by c.code, s.label
    """), {"k": [f"{c}|{s}" for c, s in keys]}).mappings().all()
    return [SeasonShare(r["code"], r["label"], int(r["n"]), int(r["g15"]), int(r["ght"]), int(r["g15ht"]),
                        int(r["gft"])) for r in rows]
