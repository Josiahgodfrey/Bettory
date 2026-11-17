"""Reads for the frontend screens (docs/API.md): fixtures with names, one match with its goals, the latest odds,
bookmakers, frozen predictions per session. Plain rows; the API layer shapes them."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

_FIXTURE_COLUMNS = """
    m.id as match_id, c.code as competition_code, c.name as competition_name, c.country as competition_country,
    s.label as season, m.round, m.kickoff_utc, m.status::text as status, m.data_status::text as data_status,
    m.home_team_id, th.name as home_team, m.away_team_id, ta.name as away_team,
    m.ft_home, m.ft_away, m.ht_home, m.ht_away, m.h15_home, m.h15_away, m.goal_before_15,
    m.first_goal_minute, m.reconciliation_status::text as reconciliation_status, m.updated_at
"""
_FIXTURE_FROM = """
    from core.matches m
    join core.competition_seasons s on s.id = m.competition_season_id
    join core.competitions c on c.id = s.competition_id
    join core.teams th on th.id = m.home_team_id
    join core.teams ta on ta.id = m.away_team_id
"""


def fixtures_between(conn: Connection, start_utc: datetime, end_utc: datetime,
                     competition_codes: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """Fixtures with start_utc <= kick-off < end_utc (all statuses), optionally of some competitions, by kick-off."""
    rows = conn.execute(text(f"""
        select {_FIXTURE_COLUMNS} {_FIXTURE_FROM}
        where m.kickoff_utc >= :a and m.kickoff_utc < :b
          and (cast(:codes as text[]) is null or c.code = any(cast(:codes as text[])))
        order by m.kickoff_utc, c.code, th.name
    """), {"a": start_utc, "b": end_utc,
           "codes": list(competition_codes) if competition_codes is not None else None}).mappings().all()
    return [dict(r) for r in rows]


def fixtures_by_ids(conn: Connection, match_ids: Sequence[UUID]) -> dict[UUID, dict[str, Any]]:
    if not match_ids:
        return {}
    rows = conn.execute(text(f"select {_FIXTURE_COLUMNS} {_FIXTURE_FROM} where m.id = any(:ids)"),
                        {"ids": list(match_ids)}).mappings().all()
    return {r["match_id"]: dict(r) for r in rows}


def match(conn: Connection, match_id: UUID) -> dict[str, Any] | None:
    row = conn.execute(text(f"select {_FIXTURE_COLUMNS} {_FIXTURE_FROM} where m.id = :m"),
                       {"m": match_id}).mappings().one_or_none()
    return dict(row) if row else None


def goals(conn: Connection, match_id: UUID) -> list[dict[str, Any]]:
    rows = conn.execute(text("""
        select g.sequence, g.minute, g.extra_minute, t.name as team, g.team_id, g.is_own_goal, g.is_penalty,
               g.player_name, src.code as source
        from core.match_goals g join core.teams t on t.id = g.team_id join core.sources src on src.id = g.source_id
        where g.match_id = :m order by g.sequence
    """), {"m": match_id}).mappings().all()
    return [dict(r) for r in rows]


def match_sources(conn: Connection, match_id: UUID) -> list[dict[str, Any]]:
    rows = conn.execute(text("""
        select src.code as source, r.provider_match_key, r.status::text as status
        from core.match_source_records r join core.sources src on src.id = r.source_id
        where r.match_id = :m order by src.code
    """), {"m": match_id}).mappings().all()
    return [dict(r) for r in rows]


def latest_odds(conn: Connection, match_id: UUID) -> list[dict[str, Any]]:
    """The newest price per bookmaker, market, line and selection (every snapshot type and source)."""
    rows = conn.execute(text("""
        select distinct on (b.name, o.market, o.line, o.selection)
               b.name as bookmaker, b.is_users_bookmaker, o.market::text as market, o.provider_market_name,
               o.line, o.selection, o.odds, o.snapshot_type::text as snapshot_type, src.code as source,
               o.captured_at
        from core.odds_snapshots o join core.bookmakers b on b.id = o.bookmaker_id
        join core.sources src on src.id = o.source_id
        where o.match_id = :m
        order by b.name, o.market, o.line, o.selection, o.captured_at desc
    """), {"m": match_id}).mappings().all()
    return [{**r, "odds": float(r["odds"]), "line": float(r["line"]) if r["line"] is not None else None}
            for r in rows]


def bookmakers(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(text("""
        select b.id, b.name, b.first15_cutoff, b.own_goals_count, b.is_users_bookmaker,
               (select max(o.captured_at) from core.odds_snapshots o where o.bookmaker_id = b.id) as last_odds_at
        from core.bookmakers b order by b.is_users_bookmaker desc, b.name
    """)).mappings().all()
    return [dict(r) for r in rows]


def frozen_counts(conn: Connection, session_ids: Sequence[str], params_version: str) -> dict[str, dict[str, int]]:
    """Per session: frozen rows, listed (QUALIFIED) rows and paper bets for one params version."""
    rows = conn.execute(text("""
        select session_id, count(*) as frozen, count(*) filter (where decision = 'QUALIFIED') as listed,
               count(bet_market) as paper_bets, min(as_of) as as_of,
               count(*) filter (where exists (select 1 from paper.rechecks r where r.prediction_id = p.id
                                              and r.status = 'WITHDRAWN')) as withdrawn
        from paper.predictions p where session_id = any(:s) and params_version = :v group by session_id
    """), {"s": list(session_ids), "v": params_version}).mappings().all()
    return {r["session_id"]: dict(r) for r in rows}


def predictions_for_match(conn: Connection, match_id: UUID) -> list[dict[str, Any]]:
    """Frozen predictions of one fixture (any session, any params version), newest first."""
    rows = conn.execute(text("""
        select session_id, params_version, as_of, recorded_at, chain_pos, decision
        from paper.predictions where match_id = :m order by recorded_at desc
    """), {"m": match_id}).mappings().all()
    return [dict(r) for r in rows]


def lineups(conn: Connection, match_id: UUID) -> list[dict[str, Any]]:
    """The newest stored lineup of each team of the match (home first)."""
    rows = conn.execute(text("""
        select distinct on (l.team_id) l.team_id, t.name as team, (l.team_id = m.home_team_id) as home,
               l.formation, l.players, src.code as source, l.fetched_at
        from core.match_lineups l join core.matches m on m.id = l.match_id join core.teams t on t.id = l.team_id
        join core.sources src on src.id = l.source_id
        where l.match_id = :m order by l.team_id, l.fetched_at desc
    """), {"m": match_id}).mappings().all()
    return sorted((dict(r) for r in rows), key=lambda r: not r["home"])
