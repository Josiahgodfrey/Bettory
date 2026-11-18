"""Load parsed records into the database (DATA-MODEL.md sections 6-8).

For each SourceMatch: resolve teams -> store the source record (append-only) -> link or create the
trusted match -> reconcile all sources -> write the trusted values, goals and quality issues.
Odds are stored only for matches that are already linked. Runs inside the caller's transaction.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import Connection

from app.domain.enums import EntityType, MappingMethod, SourceCode
from app.domain.models import SourceLineup, SourceMatch, SourceOdds
from app.domain.registry import CompetitionDef, Registry
from app.normalisation.names import normalize_team_name
from app.normalisation.parsers import ParseResult
from app.normalisation.reconcile import reconcile
from app.storage.repositories import matches as mrepo
from app.storage.repositories import reference as ref


@dataclass
class LoadStats:
    counts: Counter[str] = field(default_factory=Counter)
    seasons: set[UUID] = field(default_factory=set)  # competition-seasons whose matches were written

    def add(self, key: str, n: int = 1) -> None:
        self.counts[key] += n


class Loader:
    def __init__(self, conn: Connection, registry: Registry, cutoff_minute: int) -> None:
        """cutoff_minute: params settlement.cutoff_label_minute (a goal labelled <= it is inside 0-15)."""
        self.conn = conn
        self.registry = registry
        self.cutoff = cutoff_minute
        self._seasons: dict[tuple[str, str], UUID] = {}
        self._teams: dict[tuple[SourceCode, str], UUID | None] = {}

    # --- reference data -------------------------------------------------------------------

    def season_id(self, comp: CompetitionDef, label: str) -> UUID:
        key = (comp.code, label)
        if key not in self._seasons:
            comp_id = ref.ensure_competition(self.conn, comp)
            self._seasons[key] = ref.ensure_season(self.conn, comp_id, label)
        return self._seasons[key]

    def team_id(self, source: SourceCode, comp: CompetitionDef, name: str, provider_id: str | None,
                stats: LoadStats, context: Mapping[str, object]) -> UUID | None:
        key = f"id:{provider_id}" if provider_id else f"name:{comp.country}|{name.strip()}"
        if (source, key) in self._teams:
            return self._teams[(source, key)]

        team = ref.mapped_entity(self.conn, source, EntityType.TEAM, key)
        if team is None:
            normalized = normalize_team_name(name)
            team = ref.team_by_alias(self.conn, comp.country, normalized)
            if team is not None:
                ref.add_mapping(self.conn, source, EntityType.TEAM, key, team, MappingMethod.ALIAS)
                stats.add("teams_matched_by_alias")
            elif source in comp.team_sources and ref.claim_team_source(self.conn, comp.code, source):
                team = ref.create_team(self.conn, name.strip(), comp.country, normalized, source)
                ref.add_mapping(self.conn, source, EntityType.TEAM, key, team, MappingMethod.EXACT_ID)
                stats.add("teams_created")
            else:
                ref.queue_for_review(self.conn, source, EntityType.TEAM, key, name,
                                     {"competition": comp.code, "country": comp.country, **context})
                stats.add("teams_sent_to_review")
        self._teams[(source, key)] = team
        return team

    # --- matches --------------------------------------------------------------------------

    def load(self, parsed: ParseResult, payload_id: UUID, parser_version: str) -> LoadStats:
        stats = LoadStats()
        for reason, n in parsed.skipped.items():
            stats.add(f"skipped:{reason}", n)
        for m in parsed.matches:
            self._load_match(m, payload_id, parser_version, stats)
        for o in parsed.odds:
            self._load_odds(o, payload_id, stats)
        for lu in parsed.lineups:
            self._load_lineup(lu, payload_id, stats)
        return stats

    def _load_match(self, m: SourceMatch, payload_id: UUID, parser_version: str, stats: LoadStats) -> None:
        comp = self.registry.get(m.competition_code)
        season = self.season_id(comp, m.season_label)
        ctx = {"match": m.provider_match_key, "date": m.kickoff_date.isoformat()}
        home = self.team_id(m.source, comp, m.home_name, m.home_key, stats, ctx)
        away = self.team_id(m.source, comp, m.away_name, m.away_key, stats, ctx)
        record = mrepo.insert_source_record(self.conn, m, payload_id, parser_version, home, away)
        stats.add("source_records")
        if m.has_goal_minutes:
            stats.add("records_with_goal_minutes")
        if home is None or away is None:
            stats.add("records_waiting_for_team_review")
            return

        match_id = mrepo.find_match(self.conn, home, away, m.kickoff_date)
        if match_id is None:
            if m.kickoff_utc is None:
                stats.add("records_without_kickoff_not_creating_match")
                return
            match_id = mrepo.create_match(self.conn, season, m, home, away)
            stats.add("matches_created")
        mrepo.link_source_record(self.conn, record, match_id)
        stats.seasons.add(season)

        trusted = reconcile(mrepo.latest_records(self.conn, match_id), self.cutoff)
        if mrepo.apply_trusted(self.conn, match_id, trusted, home, away):
            stats.add("matches_updated")
        stats.add(f"reconciliation:{trusted.reconciliation.value}")
        stats.add(f"data_status:{trusted.fifteen.data_status.value}")
        for issue in trusted.issues:
            mrepo.record_issue(self.conn, issue, "match", match_id)
            stats.add(f"issue:{issue.code}")

    def _load_odds(self, o: SourceOdds, payload_id: UUID, stats: LoadStats) -> None:
        match_id = mrepo.match_for_provider_key(self.conn, o.source, o.provider_match_key)
        if match_id is None:
            stats.add("odds_without_linked_match")
            return
        bookmaker = mrepo.ensure_bookmaker(self.conn, o.bookmaker)
        mrepo.insert_odds(self.conn, o, match_id, bookmaker, payload_id)
        stats.add(f"odds:{o.market.value}")

    def _load_lineup(self, lu: SourceLineup, payload_id: UUID, stats: LoadStats) -> None:
        match_id = mrepo.match_for_provider_key(self.conn, lu.source, lu.provider_match_key)
        if match_id is None:
            stats.add("lineups_without_linked_match")
            return
        team = ref.mapped_entity(self.conn, lu.source, EntityType.TEAM, f"id:{lu.team_key}")  # as team_id() maps it
        if team is None or not mrepo.team_plays(self.conn, match_id, team):
            stats.add("lineups_team_not_in_match")
            return
        mrepo.insert_lineup(self.conn, match_id, team, lu, payload_id)
        stats.add("lineups")
