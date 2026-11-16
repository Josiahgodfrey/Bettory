"""Per-source match records, trusted matches, goals, odds and data-quality issues."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from app.domain.enums import SOURCE_IDS, FixtureStatus, Side, SourceCode
from app.domain.models import Score, SourceGoal, SourceMatch, SourceOdds
from app.normalisation.reconcile import Issue, TrustedMatch

_SOURCE_BY_ID = {v: k for k, v in SOURCE_IDS.items()}


def _goals_json(goals: tuple[SourceGoal, ...]) -> str:
    return json.dumps([g.model_dump(mode="json") for g in goals])


def insert_source_record(conn: Connection, m: SourceMatch, payload_id: UUID, parser_version: str,
                         home_team_id: UUID | None, away_team_id: UUID | None) -> UUID:
    inserted: UUID | None = conn.execute(
        text("""
            insert into core.match_source_records (source_id, provider_match_key, payload_id,
              provider_competition_key, kickoff_utc, kickoff_date, home_name, away_name, home_team_id,
              away_team_id, status, ft_home, ft_away, ht_home, ht_away, goals, has_goal_minutes, round,
              parser_version)
            values (:s, :k, :p, :ck, :ko, :kd, :hn, :an, :ht, :at, :st, :fh, :fa, :hh, :ha,
                    cast(:g as jsonb), :hm, :r, :v)
            on conflict (source_id, provider_match_key, payload_id) do nothing
            returning id"""),
        {"s": SOURCE_IDS[m.source], "k": m.provider_match_key, "p": payload_id, "ck": m.competition_code,
         "ko": m.kickoff_utc, "kd": m.kickoff_date, "hn": m.home_name, "an": m.away_name,
         "ht": home_team_id, "at": away_team_id, "st": m.status.value,
         "fh": m.ft.home if m.ft else None, "fa": m.ft.away if m.ft else None,
         "hh": m.ht.home if m.ht else None, "ha": m.ht.away if m.ht else None,
         "g": _goals_json(m.goals), "hm": m.has_goal_minutes, "r": m.round, "v": parser_version},
    ).scalar_one_or_none()
    if inserted is not None:
        return inserted
    # Same payload parsed again (append-only table: keep the original row).
    row_id: UUID = conn.execute(
        text("select id from core.match_source_records where source_id = :s and provider_match_key = :k "
             "and payload_id = :p"),
        {"s": SOURCE_IDS[m.source], "k": m.provider_match_key, "p": payload_id},
    ).scalar_one()
    return row_id


def link_source_record(conn: Connection, record_id: UUID, match_id: UUID) -> None:
    conn.execute(text("update core.match_source_records set match_id = :m where id = :r and match_id is null"),
                 {"m": match_id, "r": record_id})


def find_match(conn: Connection, home_id: UUID, away_id: UUID, day: date) -> UUID | None:
    """Same pairing within +/- 1 day (time zones and reschedules)."""
    rows = conn.execute(
        text("select id from core.matches where home_team_id = :h and away_team_id = :a "
             "and kickoff_date between :d0 and :d1 order by abs(kickoff_date - cast(:d as date))"),
        {"h": home_id, "a": away_id, "d0": day - timedelta(days=1), "d1": day + timedelta(days=1), "d": day},
    ).scalars().all()
    return rows[0] if rows else None


def create_match(conn: Connection, season_id: UUID, m: SourceMatch, home_id: UUID, away_id: UUID) -> UUID:
    row_id: UUID = conn.execute(
        text("insert into core.matches (competition_season_id, kickoff_utc, home_team_id, away_team_id, status) "
             "values (:s, :ko, :h, :a, 'SCHEDULED') returning id"),
        {"s": season_id, "ko": m.kickoff_utc, "h": home_id, "a": away_id},
    ).scalar_one()
    return row_id


_RECORDS_SQL = """
    select r.*, c.code as comp_code, cs.label as season_label
    from core.match_source_records r
    join core.matches m on m.id = r.match_id
    join core.competition_seasons cs on cs.id = m.competition_season_id
    join core.competitions c on c.id = cs.competition_id
    where r.match_id = :m
    order by r.source_id, r.parsed_at desc, r.id"""


def _score_of(r: Any, side: str) -> Score | None:
    h, a = r[f"{side}_home"], r[f"{side}_away"]
    return Score(home=h, away=a) if h is not None else None


def latest_records(conn: Connection, match_id: UUID) -> list[SourceMatch]:
    """One version per source for one match, rebuilt into the standard format.

    Scores/status come from the source's latest record. Goal minutes come from the source's latest
    record that has them - but only if that record's FT and HT equal the latest scores. A newer record
    without minutes therefore never erases earlier minutes, and stale minutes are never mixed in.
    """
    rows = conn.execute(text(_RECORDS_SQL), {"m": match_id}).mappings().all()
    by_source: dict[int, list[Any]] = {}
    for r in rows:
        by_source.setdefault(r["source_id"], []).append(r)

    out = []
    for source_id, recs in by_source.items():
        latest = recs[0]
        with_minutes = next((r for r in recs if r["has_goal_minutes"]), None)
        use_minutes = (with_minutes is not None
                       and _score_of(with_minutes, "ft") == _score_of(latest, "ft")
                       and _score_of(with_minutes, "ht") == _score_of(latest, "ht"))
        goals_row = with_minutes if use_minutes and with_minutes is not None else latest
        out.append(SourceMatch(
            source=_SOURCE_BY_ID[source_id], provider_match_key=latest["provider_match_key"],
            competition_code=latest["comp_code"], season_label=latest["season_label"],
            kickoff_date=latest["kickoff_date"], kickoff_utc=latest["kickoff_utc"],
            home_name=latest["home_name"], away_name=latest["away_name"],
            status=FixtureStatus(latest["status"]), ft=_score_of(latest, "ft"), ht=_score_of(latest, "ht"),
            goals=tuple(SourceGoal(**g) for g in goals_row["goals"]) if use_minutes else (),
            has_goal_minutes=use_minutes, round=latest["round"],
        ))
    return out


def apply_trusted(conn: Connection, match_id: UUID, t: TrustedMatch, home_id: UUID, away_id: UUID) -> bool:
    """Write the reconciled values. Returns True when the row changed."""
    first = t.fifteen.first_goal
    values: dict[str, Any] = {
        "status": t.status.value, "kickoff_utc": t.kickoff_utc, "round": t.round,
        "ft_home": t.ft.home if t.ft else None, "ft_away": t.ft.away if t.ft else None,
        "ht_home": t.ht.home if t.ht else None, "ht_away": t.ht.away if t.ht else None,
        "h15_home": t.fifteen.h15.home if t.fifteen.h15 else None,
        "h15_away": t.fifteen.h15.away if t.fifteen.h15 else None,
        "goal_before_15": t.fifteen.goal_before_15,
        "first_goal_minute": first.minute if first else None,
        "first_goal_extra": first.extra_minute if first else None,
        "first_goal_team_id": (home_id if first.side is Side.HOME else away_id) if first else None,
        "data_status": t.fifteen.data_status.value, "reconciliation_status": t.reconciliation.value,
        "scores_source_id": SOURCE_IDS[t.scores_source] if t.scores_source else None,
        "goals_source_id": SOURCE_IDS[t.goals_source] if t.goals_source else None,
    }
    if values["kickoff_utc"] is None:
        del values["kickoff_utc"]
    sets = ", ".join(f"{k} = :{k}" for k in values)
    diff = " or ".join(f"{k} is distinct from :{k}" for k in values)
    changed = conn.execute(text(f"update core.matches set {sets} where id = :id and ({diff}) returning id"),
                           {**values, "id": match_id}).scalar_one_or_none() is not None
    if changed:
        conn.execute(text("delete from core.match_goals where match_id = :m"), {"m": match_id})
        source_id = SOURCE_IDS[t.goals_source] if t.goals_source else None
        for i, g in enumerate(sorted(t.goals, key=lambda g: g.sort_key), start=1):
            conn.execute(text(
                "insert into core.match_goals (match_id, sequence, team_id, minute, extra_minute, is_own_goal, "
                "is_penalty, player_name, source_id) values (:m, :s, :t, :mi, :x, :og, :p, :pl, :src)"),
                {"m": match_id, "s": i, "t": home_id if g.side is Side.HOME else away_id, "mi": g.minute,
                 "x": g.extra_minute, "og": g.own_goal, "p": g.penalty, "pl": g.player, "src": source_id})
    return changed


def record_issue(conn: Connection, issue: Issue, entity_type: str, entity_id: UUID | None,
                 source: SourceCode | None = None) -> None:
    conn.execute(text("""
        insert into core.data_quality_issues (entity_type, entity_id, source_id, issue_code, severity, detail)
        select :et, :eid, :s, :c, :sev, cast(:d as jsonb)
        where not exists (select 1 from core.data_quality_issues
                          where entity_id is not distinct from :eid and issue_code = :c and status = 'OPEN')"""),
        {"et": entity_type, "eid": entity_id, "s": SOURCE_IDS[source] if source else None, "c": issue.code,
         "sev": issue.severity.value, "d": json.dumps(issue.detail, default=str)})


def match_for_provider_key(conn: Connection, source: SourceCode, key: str) -> UUID | None:
    return conn.execute(
        text("select match_id from core.match_source_records where source_id = :s and provider_match_key = :k "
             "and match_id is not null order by parsed_at desc limit 1"),
        {"s": SOURCE_IDS[source], "k": key},
    ).scalar_one_or_none()


def ensure_bookmaker(conn: Connection, name: str) -> UUID:
    row_id: UUID = conn.execute(
        text("insert into core.bookmakers (name) values (:n) on conflict (name) do update set name = excluded.name "
             "returning id"), {"n": name}).scalar_one()
    return row_id


def insert_odds(conn: Connection, o: SourceOdds, match_id: UUID, bookmaker_id: UUID, payload_id: UUID) -> None:
    conn.execute(text("""
        insert into core.odds_snapshots (match_id, bookmaker_id, source_id, payload_id, market, provider_market_name,
          selection, line, odds, snapshot_type, captured_at)
        values (:m, :b, :s, :p, :mk, :pm, :sel, :l, :o, :st, :c)"""),
        {"m": match_id, "b": bookmaker_id, "s": SOURCE_IDS[o.source], "p": payload_id, "mk": o.market.value,
         "pm": o.provider_market_name, "sel": o.selection, "l": o.line, "o": o.odds,
         "st": o.snapshot_type.value, "c": o.captured_at})


def provider_keys_with_minutes(conn: Connection, source: SourceCode, keys: list[str]) -> set[str]:
    """Provider match keys for which `source` already delivered a goal list (backfill resumes after them)."""
    if not keys:
        return set()
    return set(conn.execute(text(
        "select distinct provider_match_key from core.match_source_records "
        "where source_id = :s and has_goal_minutes and provider_match_key = any(:k)"),
        {"s": SOURCE_IDS[source], "k": keys}).scalars())


def season_match_ids(conn: Connection, competition_code: str, season_label: str) -> list[UUID]:
    """Matches of one competition-season that have at least one linked source record, by kick-off."""
    return list(conn.execute(text("""
        select m.id from core.matches m
        join core.competition_seasons s on s.id = m.competition_season_id
        join core.competitions c on c.id = s.competition_id
        where c.code = :c and s.label = :l
          and exists (select 1 from core.match_source_records r where r.match_id = m.id)
        order by m.kickoff_utc, m.id"""), {"c": competition_code, "l": season_label}).scalars())
