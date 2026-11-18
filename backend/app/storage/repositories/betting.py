"""Real bets (betting.placed_bets, append-only; cancellations in their own table) and odds entered by hand."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from app.domain.enums import SOURCE_IDS, SourceCode


@dataclass(frozen=True)
class NewBet:
    match_id: UUID
    session_id: str
    prediction_id: UUID | None
    market: str
    odds: float
    stake: float
    currency: str
    bookmaker: str
    placed_at: datetime
    min_odds: float | None
    rule_breaches: tuple[str, ...]
    note: str | None


def insert_bet(conn: Connection, b: NewBet) -> UUID:
    values = {**asdict(b), "rule_breaches": list(b.rule_breaches)}
    cols = ", ".join(values)
    binds = ", ".join(f":{c}" for c in values)
    bet_id: UUID = conn.execute(text(f"insert into betting.placed_bets ({cols}) values ({binds}) returning id"),
                                values).scalar_one()
    return bet_id


class AlreadyCancelled(ValueError):
    pass


def cancel_bet(conn: Connection, bet_id: UUID, reason: str) -> datetime:
    if conn.execute(text("select 1 from betting.bet_cancellations where bet_id = :b"), {"b": bet_id}).first():
        raise AlreadyCancelled(f"bet {bet_id} is already cancelled")
    at: datetime = conn.execute(text(
        "insert into betting.bet_cancellations (bet_id, reason) values (:b, :r) returning cancelled_at"),
        {"b": bet_id, "r": reason}).scalar_one()
    return at


_BET_SELECT = """
    select b.id, b.recorded_at, b.match_id, b.session_id, b.prediction_id, b.market, b.odds, b.stake, b.currency,
           b.bookmaker, b.placed_at, b.min_odds, b.rule_breaches, b.note,
           x.cancelled_at, x.reason as cancel_reason,
           c.code as competition_code, c.name as competition_name, th.name as home_team, ta.name as away_team,
           m.kickoff_utc, m.status::text as status, m.data_status::text as data_status,
           m.h15_home, m.h15_away, m.goal_before_15, m.ft_home, m.ft_away,
           p.grade, p.decision, p.p_under05, p.p_draw
    from betting.placed_bets b
    left join betting.bet_cancellations x on x.bet_id = b.id
    join core.matches m on m.id = b.match_id
    join core.competition_seasons s on s.id = m.competition_season_id
    join core.competitions c on c.id = s.competition_id
    join core.teams th on th.id = m.home_team_id
    join core.teams ta on ta.id = m.away_team_id
    left join paper.predictions p on p.id = b.prediction_id
"""


def bets(conn: Connection, *, match_id: UUID | None = None, session_id: str | None = None,
         placed_from: datetime | None = None, placed_to: datetime | None = None,
         include_cancelled: bool = True) -> list[dict[str, Any]]:
    rows = conn.execute(text(_BET_SELECT + """
        where (cast(:m as uuid) is null or b.match_id = :m)
          and (cast(:s as text) is null or b.session_id = :s)
          and (cast(:a as timestamptz) is null or b.placed_at >= :a)
          and (cast(:z as timestamptz) is null or b.placed_at < :z)
          and (:inc or x.bet_id is null)
        order by b.placed_at desc, b.recorded_at desc
    """), {"m": match_id, "s": session_id, "a": placed_from, "z": placed_to, "inc": include_cancelled}
    ).mappings().all()
    return [dict(r) for r in rows]


def bet(conn: Connection, bet_id: UUID) -> dict[str, Any] | None:
    row = conn.execute(text(_BET_SELECT + " where b.id = :b"), {"b": bet_id}).mappings().one_or_none()
    return dict(row) if row else None


def active_markets(conn: Connection, match_id: UUID) -> list[str]:
    """Markets of the match's bets that are not cancelled."""
    return [r[0] for r in conn.execute(text("""
        select b.market from betting.placed_bets b
        where b.match_id = :m and not exists (select 1 from betting.bet_cancellations x where x.bet_id = b.id)
    """), {"m": match_id})]


def bookmaker_id(conn: Connection, name: str) -> UUID:
    """The bookmaker with this name, created if new."""
    row = conn.execute(text("select id from core.bookmakers where lower(name) = lower(:n)"), {"n": name}).first()
    if row:
        return UUID(str(row[0]))
    new_id: UUID = conn.execute(text("insert into core.bookmakers (name) values (:n) returning id"),
                                {"n": name}).scalar_one()
    return new_id


def insert_manual_odds(conn: Connection, match_id: UUID, bookmaker: UUID,
                       rows: Sequence[tuple[str, str, str, float]], captured_at: datetime) -> int:
    """Odds entered by the owner: (market, provider market name, selection, odds), source MANUAL, BET_TIME."""
    for market, name, selection, odds in rows:
        conn.execute(text("""
            insert into core.odds_snapshots (match_id, bookmaker_id, source_id, market, provider_market_name,
                                             selection, odds, snapshot_type, captured_at)
            values (:m, :b, :src, cast(:mk as core.market_code), :n, :sel, :o, 'BET_TIME', :t)
        """), {"m": match_id, "b": bookmaker, "src": SOURCE_IDS[SourceCode.MANUAL], "mk": market, "n": name,
               "sel": selection, "o": odds, "t": captured_at})
    return len(rows)
