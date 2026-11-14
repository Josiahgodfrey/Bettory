"""Competitions, seasons, teams, aliases, provider mapping and the review queue."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from app.domain.enums import SOURCE_IDS, EntityType, MappingMethod, SourceCode
from app.domain.registry import CompetitionDef
from app.normalisation.names import normalize_team_name


def ensure_competition(conn: Connection, comp: CompetitionDef) -> UUID:
    return conn.execute(
        text("""
            insert into core.competitions (code, name, country, competition_type, level, timezone)
            values (:code, :name, :country, :type, :level, :tz)
            on conflict (code) do update set
              name = excluded.name, country = excluded.country, competition_type = excluded.competition_type,
              level = excluded.level, timezone = excluded.timezone
            returning id"""),
        {"code": comp.code, "name": comp.name, "country": comp.country, "type": comp.type.value,
         "level": comp.level, "tz": comp.timezone},
    ).scalar_one()


def claim_team_source(conn: Connection, competition_code: str, source: SourceCode) -> bool:
    """True if `source` is (or now becomes) the only team creator for the competition."""
    owner = conn.execute(
        text("update core.competitions set team_source_id = coalesce(team_source_id, :s) "
             "where code = :c returning team_source_id"),
        {"s": SOURCE_IDS[source], "c": competition_code},
    ).scalar_one()
    return bool(owner == SOURCE_IDS[source])


def ensure_season(conn: Connection, competition_id: UUID, label: str) -> UUID:
    return conn.execute(
        text("""
            insert into core.competition_seasons (competition_id, label) values (:c, :l)
            on conflict (competition_id, label) do update set label = excluded.label
            returning id"""),
        {"c": competition_id, "l": label},
    ).scalar_one()


def mapped_entity(conn: Connection, source: SourceCode, entity_type: EntityType, provider_key: str) -> UUID | None:
    return conn.execute(
        text("select entity_id from core.provider_entity_map "
             "where source_id = :s and entity_type = :t and provider_key = :k"),
        {"s": SOURCE_IDS[source], "t": entity_type.value, "k": provider_key},
    ).scalar_one_or_none()


def add_mapping(conn: Connection, source: SourceCode, entity_type: EntityType, provider_key: str,
                entity_id: UUID, method: MappingMethod) -> None:
    conn.execute(
        text("insert into core.provider_entity_map (source_id, entity_type, provider_key, entity_id, method) "
             "values (:s, :t, :k, :e, :m) on conflict (source_id, entity_type, provider_key) do nothing"),
        {"s": SOURCE_IDS[source], "t": entity_type.value, "k": provider_key, "e": entity_id, "m": method.value},
    )


def team_by_alias(conn: Connection, country: str, normalized: str) -> UUID | None:
    return conn.execute(
        text("select team_id from core.team_aliases where country = :c and normalized_alias = :n"),
        {"c": country, "n": normalized},
    ).scalar_one_or_none()


def create_team(conn: Connection, name: str, country: str, normalized: str, source: SourceCode) -> UUID:
    team_id = conn.execute(
        text("insert into core.teams (name, country) values (:n, :c) returning id"),
        {"n": name, "c": country},
    ).scalar_one()
    add_alias(conn, team_id, name, normalized, country, source)
    return team_id


def add_alias(conn: Connection, team_id: UUID, alias: str, normalized: str, country: str,
              source: SourceCode | None) -> None:
    conn.execute(
        text("insert into core.team_aliases (team_id, alias, normalized_alias, country, source_id) "
             "values (:t, :a, :n, :c, :s) on conflict (country, normalized_alias) do nothing"),
        {"t": team_id, "a": alias, "n": normalized, "c": country, "s": SOURCE_IDS[source] if source else None},
    )


def queue_for_review(conn: Connection, source: SourceCode, entity_type: EntityType, provider_key: str,
                     provider_name: str, context: dict[str, Any]) -> None:
    conn.execute(
        text("insert into core.entity_review_queue (source_id, entity_type, provider_key, provider_name, context) "
             "values (:s, :t, :k, :n, cast(:ctx as jsonb)) on conflict (source_id, entity_type, provider_key) do nothing"),
        {"s": SOURCE_IDS[source], "t": entity_type.value, "k": provider_key, "n": provider_name,
         "ctx": json.dumps(context, default=str)},
    )


def open_reviews(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(text(
        "select q.id, s.code as source, q.entity_type, q.provider_key, q.provider_name, q.context, q.created_at "
        "from core.entity_review_queue q join core.sources s on s.id = q.source_id "
        "where q.status = 'OPEN' order by q.created_at"))
    return [dict(r) for r in rows.mappings()]


def resolve_review(conn: Connection, review_id: UUID, team_id: UUID) -> None:
    row = conn.execute(
        text("select q.source_id, q.provider_key, q.provider_name, t.country, s.code "
             "from core.entity_review_queue q join core.teams t on t.id = :team "
             "join core.sources s on s.id = q.source_id where q.id = :q and q.status = 'OPEN'"),
        {"q": review_id, "team": team_id},
    ).mappings().one()
    source = SourceCode(row["code"])
    add_mapping(conn, source, EntityType.TEAM, row["provider_key"], team_id, MappingMethod.MANUAL)
    add_alias(conn, team_id, row["provider_name"], normalize_team_name(row["provider_name"]), row["country"], source)
    conn.execute(
        text("update core.entity_review_queue set status = 'RESOLVED', resolved_entity_id = :t, "
             "resolved_at = now() where id = :q"),
        {"t": team_id, "q": review_id},
    )


def search_teams(conn: Connection, query: str | None, country: str | None, limit: int = 50) -> list[dict[str, Any]]:
    """Teams whose name or any alias contains `query` (normalised), for resolving reviews."""
    needle = normalize_team_name(query) if query and query.strip() else None
    rows = conn.execute(text("""
        select t.id, t.name, t.country from core.teams t
        where (cast(:c as text) is null or t.country = :c)
          and (cast(:q as text) is null or exists (
                select 1 from core.team_aliases a
                where a.team_id = t.id and a.normalized_alias like '%' || :q || '%'))
        order by t.country, t.name limit :lim"""),
        {"c": country, "q": needle, "lim": limit})
    return [dict(r) for r in rows.mappings()]
