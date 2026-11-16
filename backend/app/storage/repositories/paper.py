"""paper.predictions: append-only predictions frozen before kick-off (migration 20261117120000).

The database stamps recorded_at, refuses rows whose kick-off is not later, links every row into a hash chain and
refuses UPDATE / DELETE / TRUNCATE. This module only inserts and reads.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from app.domain.enums import FixtureStatus


@dataclass(frozen=True)
class SessionFixture:
    match_id: UUID
    competition_code: str
    kickoff_utc: datetime
    status: FixtureStatus


# Everything a live session would have had on its list: the cap ranks this whole set, also fixtures that have
# started or finished by the time a late freeze runs (they are evaluated, never frozen). Postponed and cancelled
# fixtures have no kick-off in the session.
_IN_SESSION = (FixtureStatus.SCHEDULED, FixtureStatus.LIVE, FixtureStatus.FINISHED, FixtureStatus.ABANDONED)


def session_fixtures(conn: Connection, start_utc: datetime, end_utc: datetime,
                     competition_codes: Sequence[str]) -> list[SessionFixture]:
    """Fixtures with start_utc <= kick-off < end_utc in the given competitions, by kick-off."""
    if start_utc.utcoffset() is None or end_utc.utcoffset() is None:
        raise ValueError("timezone-aware bounds required")
    rows = conn.execute(text("""
        select m.id, c.code, m.kickoff_utc, m.status::text
        from core.matches m
        join core.competition_seasons s on s.id = m.competition_season_id
        join core.competitions c on c.id = s.competition_id
        where m.kickoff_utc >= :start and m.kickoff_utc < :end
          and c.code = any(:codes) and m.status::text = any(:statuses)
        order by m.kickoff_utc, m.id
    """), {"start": start_utc, "end": end_utc, "codes": list(competition_codes),
           "statuses": [x.value for x in _IN_SESSION]}).all()
    return [SessionFixture(r[0], r[1], r[2], FixtureStatus(r[3])) for r in rows]


@dataclass(frozen=True)
class NewPrediction:
    match_id: UUID
    competition_code: str
    kickoff_utc: datetime
    session_id: str
    as_of: datetime
    params_version: str
    formula_version: str
    plan_file: str | None
    plan_sha256: str | None
    code_version: str | None
    decision: str
    grade: str
    failed_checks: tuple[str, ...]
    error: str | None
    p_under05: float | None
    p_draw: float | None
    p_model_a: float | None
    p_model_b2: float | None
    p_market: float | None
    p_market_draw: float | None
    p_league_00: float | None
    p_league_draw: float | None
    min_odds_under05: float | None
    min_odds_draw: float | None


_COLUMNS = tuple(NewPrediction.__dataclass_fields__)


def insert_prediction(conn: Connection, p: NewPrediction) -> int | None:
    """Freeze one prediction. Returns its chain position, or None if this fixture, session and params version
    was already frozen (nothing is changed then)."""
    values: dict[str, Any] = asdict(p)
    values["failed_checks"] = list(p.failed_checks)
    cols = ", ".join(_COLUMNS)
    binds = ", ".join(f":{c}" for c in _COLUMNS)
    return conn.execute(text(f"""
        insert into paper.predictions ({cols}) values ({binds})
        on conflict (match_id, session_id, params_version) do nothing
        returning chain_pos
    """), values).scalar()


@dataclass(frozen=True)
class FrozenPrediction:
    chain_pos: int
    row_hash: str
    recorded_at: datetime
    match_id: UUID
    competition_code: str
    kickoff_utc: datetime
    session_id: str
    as_of: datetime
    params_version: str
    decision: str
    grade: str
    p_under05: float | None
    p_draw: float | None
    min_odds_under05: float | None
    min_odds_draw: float | None


def frozen(conn: Connection, session_id: str | None = None,
           params_version: str | None = None) -> list[FrozenPrediction]:
    fields = ", ".join(FrozenPrediction.__dataclass_fields__)
    rows = conn.execute(text(f"""
        select {fields} from paper.predictions
        where (cast(:sid as text) is null or session_id = :sid)
          and (cast(:pv as text) is null or params_version = :pv)
        order by chain_pos
    """), {"sid": session_id, "pv": params_version}).mappings().all()
    return [FrozenPrediction(**r) for r in rows]


@dataclass(frozen=True)
class ChainStatus:
    rows: int
    head_pos: int | None
    head_hash: str | None
    problems: tuple[tuple[int, str], ...]  # (chain position, problem)

    @property
    def intact(self) -> bool:
        return not self.problems


def verify_chain(conn: Connection) -> ChainStatus:
    problems = tuple((int(r[0]), str(r[1])) for r in conn.execute(text("select * from paper.verify_chain()")))
    head = conn.execute(text("select chain_pos, row_hash from paper.predictions "
                             "order by chain_pos desc limit 1")).first()
    count = conn.execute(text("select count(*) from paper.predictions")).scalar_one()
    return ChainStatus(int(count), int(head[0]) if head else None, str(head[1]) if head else None, problems)
