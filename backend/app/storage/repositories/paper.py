"""paper.predictions: append-only predictions frozen before kick-off (migration 20261117120000).

The database stamps recorded_at, refuses rows whose kick-off is not later, links every row into a hash chain and
refuses UPDATE / DELETE / TRUNCATE. This module only inserts and reads.
"""

from __future__ import annotations

import json
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
    odds_seen: tuple[dict[str, Any], ...] = ()
    best_under05_odds: float | None = None
    best_under05_book: str | None = None
    best_draw_odds: float | None = None
    best_draw_book: str | None = None
    bet_market: str | None = None
    bet_odds: float | None = None
    bet_book: str | None = None
    bet_ev: float | None = None
    bet_stake: float | None = None


_COLUMNS = tuple(NewPrediction.__dataclass_fields__)


def insert_prediction(conn: Connection, p: NewPrediction) -> int | None:
    """Freeze one prediction. Returns its chain position, or None if this fixture, session and params version
    was already frozen (nothing is changed then)."""
    values: dict[str, Any] = asdict(p)
    values["failed_checks"] = list(p.failed_checks)
    values["odds_seen"] = json.dumps(list(p.odds_seen))
    cols = ", ".join(_COLUMNS)
    binds = ", ".join("cast(:odds_seen as jsonb)" if c == "odds_seen" else f":{c}" for c in _COLUMNS)
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
    best_under05_odds: float | None
    best_under05_book: str | None
    best_draw_odds: float | None
    best_draw_book: str | None
    odds_seen: list[dict[str, Any]]
    bet_market: str | None
    bet_odds: float | None
    bet_book: str | None
    bet_ev: float | None
    bet_stake: float | None


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


# --- settlements -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MatchResultState:
    match_status: str
    data_status: str
    h15_home: int | None
    h15_away: int | None
    goal_before_15: bool | None


@dataclass(frozen=True)
class SettlementCandidate:
    prediction_id: UUID
    prediction_hash: str
    kickoff_utc: datetime
    bet_market: str | None
    bet_odds: float | None
    bet_stake: float | None
    now: MatchResultState  # the stored result today
    last_id: UUID | None  # the latest settlement of this prediction
    last: MatchResultState | None  # the result it was settled on


def settlement_candidates(conn: Connection, kicked_off_before: datetime) -> list[SettlementCandidate]:
    """Every frozen prediction whose match kicked off before the bound, with its latest settlement (if any)."""
    rows = conn.execute(text("""
        select p.id, p.row_hash, p.kickoff_utc, p.bet_market, p.bet_odds, p.bet_stake,
               m.status::text as m_status, m.data_status::text as m_data, m.h15_home as m_h, m.h15_away as m_a,
               m.goal_before_15 as m_g,
               s.id as s_id, s.match_status as s_status, s.data_status as s_data, s.h15_home as s_h,
               s.h15_away as s_a, s.goal_before_15 as s_g
        from paper.predictions p
        join core.matches m on m.id = p.match_id
        left join lateral (select * from paper.settlements x where x.prediction_id = p.id
                           order by x.settled_at desc limit 1) s on true
        where p.kickoff_utc < :before
        order by p.chain_pos
    """), {"before": kicked_off_before}).mappings().all()
    return [SettlementCandidate(
        r["id"], r["row_hash"], r["kickoff_utc"], r["bet_market"], r["bet_odds"], r["bet_stake"],
        MatchResultState(r["m_status"], r["m_data"], r["m_h"], r["m_a"], r["m_g"]), r["s_id"],
        None if r["s_id"] is None else MatchResultState(r["s_status"], r["s_data"], r["s_h"], r["s_a"], r["s_g"]),
    ) for r in rows]


@dataclass(frozen=True)
class NewSettlement:
    prediction_id: UUID
    prediction_hash: str
    supersedes: UUID | None
    match_status: str
    data_status: str
    h15_home: int | None
    h15_away: int | None
    goal_before_15: bool | None
    under05: bool | None
    draw: bool | None
    bet_outcome: str | None
    profit: float | None


def insert_settlement(conn: Connection, s: NewSettlement) -> UUID:
    cols = tuple(NewSettlement.__dataclass_fields__)
    return UUID(str(conn.execute(text(
        f"insert into paper.settlements ({', '.join(cols)}) values ({', '.join(':' + c for c in cols)}) "
        "returning id"), asdict(s)).scalar_one()))
