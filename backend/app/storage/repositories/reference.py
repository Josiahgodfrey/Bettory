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
    row_id: UUID = conn.execute(
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
    return row_id


def claim_team_source(conn: Connection, competition_code: str, source: SourceCode) -> bool:
    """True if `source` is (or now becomes) the only team creator for the competition."""
    owner = conn.execute(
        text("update core.competitions set team_source_id = coalesce(team_source_id, :s) "
             "where code = :c returning team_source_id"),
        {"s": SOURCE_IDS[source], "c": competition_code},
    ).scalar_one()
    return bool(owner == SOURCE_IDS[source])


def ensure_season(conn: Connection, competition_id: UUID, label: str) -> UUID:
    row_id: UUID = conn.execute(
        text("""
            insert into core.competition_seasons (competition_id, label) values (:c, :l)
            on conflict (competition_id, label) do update set label = excluded.label
            returning id"""),
        {"c": competition_id, "l": label},
    ).scalar_one()
    return row_id


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
    team_id: UUID = conn.execute(
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
             "values (:s, :t, :k, :n, cast(:ctx as jsonb)) "
             "on conflict (source_id, entity_type, provider_key) do nothing"),
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


# Words shared by many unrelated clubs: a match on these alone is not a hint of a duplicate.
_COMMON_WORDS = frozenset({"city", "united", "town", "county", "athletic", "albion", "rovers", "wanderers", "club",
                           "sporting", "real", "hotspur", "borussia", "vfb", "vfl", "tsg", "sv", "rb", "and"})


class SimilarTeamsExist(ValueError):
    """Creating a new team was refused: existing teams share a distinctive word with the name."""

    def __init__(self, name: str, similar: list[dict[str, Any]]) -> None:
        self.similar = similar
        listed = "; ".join(f"{t['name']} ({t['id']})" for t in similar)
        super().__init__(f"{name!r} looks like an existing team: {listed}. Link the review to one of them, or "
                         "confirm that it is a different club")


def similar_teams(conn: Connection, name: str, country: str) -> list[dict[str, Any]]:
    """Teams of the country with an alias containing a distinctive word (>= 4 letters) of `name`."""
    words = [w for w in normalize_team_name(name).split() if len(w) >= 4 and w not in _COMMON_WORDS]
    found: dict[UUID, dict[str, Any]] = {}
    for w in words:
        for t in search_teams(conn, w, country, 20):
            found[t["id"]] = t
    return sorted(found.values(), key=lambda t: t["name"])


def resolve_review_new_team(conn: Connection, review_id: UUID, name: str | None = None,
                            allow_similar: bool = False) -> UUID:
    """Resolve a review by creating the team it names (a club new to the database, e.g. promoted).
    Refused when the name already belongs to a team, or - unless allow_similar - when existing teams share a
    distinctive word with it. Returns the new team's id."""
    row = conn.execute(
        text("select q.provider_name, q.context from core.entity_review_queue q "
             "where q.id = :q and q.status = 'OPEN' and q.entity_type = 'TEAM'"), {"q": review_id},
    ).mappings().one()
    country = (row["context"] or {}).get("country")
    if not country:
        raise ValueError("the review has no country: link it to an existing team instead")
    team_name = (name or row["provider_name"]).strip()
    for candidate in {team_name, row["provider_name"]}:
        existing = team_by_alias(conn, country, normalize_team_name(candidate))
        if existing is not None:
            raise ValueError(f"{candidate!r} is already a name of team {existing}: link the review to it")
    similar = similar_teams(conn, team_name, country)
    if similar and not allow_similar:
        raise SimilarTeamsExist(team_name, similar)
    source = conn.execute(text("select s.code from core.entity_review_queue q join core.sources s "
                               "on s.id = q.source_id where q.id = :q"), {"q": review_id}).scalar_one()
    team_id = create_team(conn, team_name, country, normalize_team_name(team_name), SourceCode(source))
    resolve_review(conn, review_id, team_id)
    return team_id


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
