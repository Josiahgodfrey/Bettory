"""Point-in-time reads: everything the calculation needs about the past, as known strictly before `as_of`.

DATA-MODEL.md 9 and ARCHITECTURE.md R4 / 11 ("point-in-time repository"). Every read goes through the SQL
functions core.team_history / core.h2h / core.competition_matches (FINISHED, not CONFLICT, no friendlies,
kick-off < as_of) or applies the same rule, and every returned row is checked again here: a row with
kick-off >= as_of raises PointInTimeError instead of being returned. Odds are the latest snapshot captured
strictly before `as_of`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from app.domain.enums import CompetitionType, DataStatus, FixtureStatus, MarketCode, ReconciliationStatus


class PointInTimeError(RuntimeError):
    """A read returned data that was not known before as_of. Never caught silently."""


@dataclass(frozen=True)
class MatchRow:
    id: UUID
    competition_season_id: UUID
    kickoff_utc: datetime
    home_team_id: UUID
    away_team_id: UUID
    status: FixtureStatus
    ft: tuple[int, int] | None
    ht: tuple[int, int] | None
    h15: tuple[int, int] | None
    goal_before_15: bool | None
    data_status: DataStatus
    reconciliation_status: ReconciliationStatus


@dataclass(frozen=True)
class FixtureRow:
    """The fixture being analysed (its own kick-off may be after as_of; its result is never read)."""

    id: UUID
    kickoff_utc: datetime
    home_team_id: UUID
    away_team_id: UUID
    status: FixtureStatus
    competition_season_id: UUID
    season_label: str
    competition_id: UUID
    competition_code: str | None
    competition_type: CompetitionType


@dataclass(frozen=True)
class SeasonRow:
    id: UUID
    competition_id: UUID
    label: str
    competition_type: CompetitionType


@dataclass(frozen=True)
class OddsRow:
    bookmaker: str
    market: MarketCode
    line: float | None
    selection: str
    odds: float
    captured_at: datetime


def _pair(row: Any, side: str) -> tuple[int, int] | None:
    h, a = row[f"{side}_home"], row[f"{side}_away"]
    return (int(h), int(a)) if h is not None else None


def _match(row: Any) -> MatchRow:
    return MatchRow(
        id=row["id"], competition_season_id=row["competition_season_id"], kickoff_utc=row["kickoff_utc"],
        home_team_id=row["home_team_id"], away_team_id=row["away_team_id"], status=FixtureStatus(row["status"]),
        ft=_pair(row, "ft"), ht=_pair(row, "ht"), h15=_pair(row, "h15"), goal_before_15=row["goal_before_15"],
        data_status=DataStatus(row["data_status"]),
        reconciliation_status=ReconciliationStatus(row["reconciliation_status"]),
    )


class PointInTimeReader:
    """All reads for one `as_of`. One instance per analysis moment (e.g. a session run, or a backtest kick-off)."""

    def __init__(self, conn: Connection, as_of: datetime) -> None:
        if as_of.tzinfo is None:
            raise PointInTimeError("as_of must be timezone-aware")
        self.conn = conn
        self.as_of = as_of

    def _guard(self, rows: Sequence[MatchRow]) -> list[MatchRow]:
        for r in rows:
            if r.kickoff_utc >= self.as_of:
                raise PointInTimeError(f"match {r.id} kicked off {r.kickoff_utc}, not before as_of {self.as_of}")
            if r.status is not FixtureStatus.FINISHED or r.reconciliation_status is ReconciliationStatus.CONFLICT:
                raise PointInTimeError(f"match {r.id} is {r.status} / {r.reconciliation_status}: not usable history")
        return list(rows)

    # --- the fixture ------------------------------------------------------------------------------------

    def fixture(self, match_id: UUID) -> FixtureRow:
        row = self.conn.execute(text("""
            select m.id, m.kickoff_utc, m.home_team_id, m.away_team_id, m.status, m.competition_season_id,
                   s.label as season_label, c.id as competition_id, c.code, c.competition_type
            from core.matches m join core.competition_seasons s on s.id = m.competition_season_id
            join core.competitions c on c.id = s.competition_id where m.id = :m"""), {"m": match_id}).mappings().one()
        return FixtureRow(row["id"], row["kickoff_utc"], row["home_team_id"], row["away_team_id"],
                          FixtureStatus(row["status"]), row["competition_season_id"], row["season_label"],
                          row["competition_id"], row["code"], CompetitionType(row["competition_type"]))

    # --- history ---------------------------------------------------------------------------------------

    def team_history(self, team_id: UUID, category: str, limit: int) -> list[MatchRow]:
        """Newest first (core.team_history: GENERAL / HOME / AWAY)."""
        rows = self.conn.execute(text("select * from core.team_history(:t, cast(:c as core.history_category), :a, :n)"),
                                 {"t": team_id, "c": category, "a": self.as_of, "n": limit}).mappings().all()
        return self._guard([_match(r) for r in rows])

    def h2h(self, team_a: UUID, team_b: UUID, limit: int, max_years: int) -> list[MatchRow]:
        rows = self.conn.execute(text(
            "select * from core.h2h(:a, :b, :t, :n, cast(:t as timestamptz) - make_interval(years => :y))"),
            {"a": team_a, "b": team_b, "t": self.as_of, "n": limit, "y": max_years}).mappings().all()
        return self._guard([_match(r) for r in rows])

    def competition_matches(self, competition_season_id: UUID) -> list[MatchRow]:
        rows = self.conn.execute(text("select * from core.competition_matches(:s, :a)"),
                                 {"s": competition_season_id, "a": self.as_of}).mappings().all()
        return self._guard([_match(r) for r in rows])

    # --- seasons ---------------------------------------------------------------------------------------

    def competition_seasons(self, competition_id: UUID) -> list[SeasonRow]:
        """Seasons of a competition with at least one finished match before as_of."""
        rows = self.conn.execute(text("""
            select s.id, s.competition_id, s.label, c.competition_type
            from core.competition_seasons s join core.competitions c on c.id = s.competition_id
            where s.competition_id = :c and exists (
              select 1 from core.matches m where m.competition_season_id = s.id
                and m.status = 'FINISHED' and m.kickoff_utc < :a)"""), {"c": competition_id, "a": self.as_of})
        return [SeasonRow(r["id"], r["competition_id"], r["label"], CompetitionType(r["competition_type"]))
                for r in rows.mappings()]

    def season(self, competition_season_id: UUID) -> SeasonRow:
        r = self.conn.execute(text("""
            select s.id, s.competition_id, s.label, c.competition_type
            from core.competition_seasons s join core.competitions c on c.id = s.competition_id
            where s.id = :s"""), {"s": competition_season_id}).mappings().one()
        return SeasonRow(r["id"], r["competition_id"], r["label"], CompetitionType(r["competition_type"]))

    def all_league_seasons(self) -> list[SeasonRow]:
        """Every LEAGUE competition-season with finished matches before as_of (global shrinkage targets)."""
        rows = self.conn.execute(text("""
            select s.id, s.competition_id, s.label, c.competition_type
            from core.competition_seasons s join core.competitions c on c.id = s.competition_id
            where c.competition_type = 'LEAGUE' and exists (
              select 1 from core.matches m where m.competition_season_id = s.id
                and m.status = 'FINISHED' and m.kickoff_utc < :a)"""), {"a": self.as_of})
        return [SeasonRow(r["id"], r["competition_id"], r["label"], CompetitionType(r["competition_type"]))
                for r in rows.mappings()]

    def domestic_league_season(self, team_id: UUID, season_label: str) -> UUID | None:
        """The LEAGUE competition-season the team played in that season (Model B v2 fallback, data doc 16.7)."""
        rows = self.conn.execute(text("""
            select distinct s.id from core.matches m
            join core.competition_seasons s on s.id = m.competition_season_id
            join core.competitions c on c.id = s.competition_id
            where c.competition_type = 'LEAGUE' and s.label = :l and m.kickoff_utc < :a
              and (m.home_team_id = :t or m.away_team_id = :t)"""),
            {"t": team_id, "l": season_label, "a": self.as_of}).scalars().all()
        if len(rows) > 1:
            raise PointInTimeError(f"team {team_id} played in {len(rows)} leagues in {season_label}")
        return rows[0] if rows else None

    # --- odds ------------------------------------------------------------------------------------------

    def odds(self, match_id: UUID, markets: Sequence[MarketCode]) -> list[OddsRow]:
        """Latest snapshot per (bookmaker, market, line, selection) captured strictly before as_of."""
        rows = self.conn.execute(text("""
            select distinct on (b.name, o.market, o.line, o.selection)
                   b.name as bookmaker, o.market, o.line, o.selection, o.odds, o.captured_at
            from core.odds_snapshots o join core.bookmakers b on b.id = o.bookmaker_id
            where o.match_id = :m and o.captured_at < :a and o.market = any(cast(:mk as core.market_code[]))
            order by b.name, o.market, o.line, o.selection, o.captured_at desc"""),
            {"m": match_id, "a": self.as_of, "mk": [m.value for m in markets]}).mappings().all()
        out = []
        for r in rows:
            if r["captured_at"] >= self.as_of:
                raise PointInTimeError(f"odds captured {r['captured_at']} not before as_of {self.as_of}")
            line = r["line"]
            out.append(OddsRow(r["bookmaker"], MarketCode(r["market"]),
                               float(line) if isinstance(line, (Decimal, float, int)) else None,
                               r["selection"], float(r["odds"]), r["captured_at"]))
        return out
