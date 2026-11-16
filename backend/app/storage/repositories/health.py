"""Data health: stored cross-checks, run audits, coverage and open items (docs/DATA-HEALTH.md)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from app.domain.quality_config import CrossCheckRules
from app.normalisation.crosscheck import CrossCheckReport, CrossCheckVerdict

# --- cross-checks ------------------------------------------------------------------------------------------


def season_match_ids(conn: Connection, competition_season_id: UUID) -> list[UUID]:
    """Matches of one competition-season with at least one linked source record, by kick-off."""
    return list(conn.execute(text("""
        select m.id from core.matches m
        where m.competition_season_id = :s
          and exists (select 1 from core.match_source_records r where r.match_id = m.id)
        order by m.kickoff_utc, m.id"""), {"s": competition_season_id}).scalars())


def season_id(conn: Connection, competition_code: str, season_label: str) -> UUID | None:
    found: UUID | None = conn.execute(text("""
        select s.id from core.competition_seasons s join core.competitions c on c.id = s.competition_id
        where c.code = :c and s.label = :l"""), {"c": competition_code, "l": season_label}).scalar_one_or_none()
    return found


def save_crosscheck(conn: Connection, competition_season_id: UUID, report: CrossCheckReport,
                    verdict: CrossCheckVerdict, rules: CrossCheckRules, run_id: UUID | None) -> UUID:
    dis = [{"match": d.match_key, "kind": d.kind, "values": {s.value: v for s, v in d.values}}
           for d in report.disagreements[:rules.max_disagreements_stored]]
    row: UUID = conn.execute(text("""
        insert into core.crosscheck_results (competition_season_id, run_id, status, fifteen_verified, matches,
          multi_source, ft_compared, ft_agreed, ht_compared, ht_agreed, h15_compared, h15_agreed,
          h15_vs_ht_compared, h15_vs_ht_agreed, min_agreement, min_compared, failed_checks, disagreements)
        values (:s, :run, :st, :fv, :m, :ms, :ftc, :fta, :htc, :hta, :hc, :ha, :ic, :ia, :mina, :minc, :failed,
                cast(:dis as jsonb))
        returning id"""), {
        "s": competition_season_id, "run": run_id, "st": verdict.status.value, "fv": verdict.fifteen_verified,
        "m": report.matches, "ms": report.multi_source, "ftc": report.ft.compared, "fta": report.ft.agreed,
        "htc": report.ht.compared, "hta": report.ht.agreed, "hc": report.h15.compared, "ha": report.h15.agreed,
        "ic": report.h15_vs_ht.compared, "ia": report.h15_vs_ht.agreed, "mina": rules.min_agreement,
        "minc": rules.min_compared, "failed": list(verdict.failed), "dis": json.dumps(dis),
    }).scalar_one()
    return row


_CROSSCHECK_COLUMNS = """
    x.id, c.code as competition, s.label as season, x.competition_season_id, x.checked_at, x.run_id, x.status,
    x.fifteen_verified, x.matches, x.multi_source, x.ft_compared, x.ft_agreed, x.ht_compared, x.ht_agreed,
    x.h15_compared, x.h15_agreed, x.h15_vs_ht_compared, x.h15_vs_ht_agreed, x.min_agreement::float8 as min_agreement,
    x.min_compared, x.failed_checks, x.accepted_at, x.accepted_note"""


def latest_crosschecks(conn: Connection) -> list[dict[str, Any]]:
    """The current cross-check of every competition-season that has one."""
    return [dict(r) for r in conn.execute(text(f"""
        select distinct on (x.competition_season_id) {_CROSSCHECK_COLUMNS}
        from core.crosscheck_results x
        join core.competition_seasons s on s.id = x.competition_season_id
        join core.competitions c on c.id = s.competition_id
        order by x.competition_season_id, x.checked_at desc, x.id""")).mappings()]


def crosscheck(conn: Connection, crosscheck_id: UUID, with_disagreements: bool = True) -> dict[str, Any] | None:
    extra = ", x.disagreements" if with_disagreements else ""
    row = conn.execute(text(f"""
        select {_CROSSCHECK_COLUMNS}{extra} from core.crosscheck_results x
        join core.competition_seasons s on s.id = x.competition_season_id
        join core.competitions c on c.id = s.competition_id
        where x.id = :id"""), {"id": crosscheck_id}).mappings().one_or_none()
    return dict(row) if row else None


def accept_crosscheck(conn: Connection, crosscheck_id: UUID, note: str) -> None:
    """Owner reviewed a FAIL and accepts the data (the note says why). Only the season's LATEST check counts."""
    if not note.strip():
        raise ValueError("an acceptance needs a note explaining the review")
    done = conn.execute(text("""
        update core.crosscheck_results set accepted_at = now(), accepted_note = :n
        where id = :id and status = 'FAIL' and accepted_at is null"""), {"id": crosscheck_id, "n": note.strip()})
    if done.rowcount != 1:
        raise ValueError(f"cross-check {crosscheck_id} is not an unaccepted FAIL")


def blocking_crosschecks(conn: Connection, competition_season_ids: Sequence[UUID]) -> list[dict[str, Any]]:
    """Seasons among the given ones whose LATEST cross-check is an unaccepted FAIL."""
    if not competition_season_ids:
        return []
    return [dict(r) for r in conn.execute(text("""
        select * from (
          select distinct on (x.competition_season_id) x.competition_season_id, s.label as season, x.status,
                 x.failed_checks, x.accepted_at, x.checked_at
          from core.crosscheck_results x join core.competition_seasons s on s.id = x.competition_season_id
          where x.competition_season_id = any(:ids)
          order by x.competition_season_id, x.checked_at desc, x.id) latest
        where status = 'FAIL' and accepted_at is null"""), {"ids": list(competition_season_ids)}).mappings()]


# --- runs --------------------------------------------------------------------------------------------------


def set_run_audit(conn: Connection, run_id: UUID, status: str, findings: list[dict[str, str]]) -> None:
    conn.execute(text("update raw.ingestion_runs set audit_status = :s, audit = cast(:a as jsonb) where id = :id"),
                 {"s": status, "a": json.dumps(findings), "id": run_id})


def recent_runs(conn: Connection, limit: int) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text("""
        select r.id, s.code as source, r.job_name, r.status, r.started_at, r.finished_at, r.requests_used,
               r.items_stored, r.error, r.stats, r.audit_status, r.audit
        from raw.ingestion_runs r join core.sources s on s.id = r.source_id
        order by r.started_at desc, r.id limit :lim"""), {"lim": limit}).mappings()]


# --- coverage and open items -------------------------------------------------------------------------------


def coverage(conn: Connection) -> list[dict[str, Any]]:
    """Per competition-season: finished matches, 15' data status, conflicts, sources and goal-minute sources."""
    return [dict(r) for r in conn.execute(text("""
        with finished as (
          select m.* from core.matches m where m.status = 'FINISHED'
        ), per_source as (
          select m.competition_season_id, so.code,
                 count(distinct m.id) as matches,
                 count(distinct m.id) filter (where r.has_goal_minutes) as with_minutes
          from finished m
          join core.match_source_records r on r.match_id = m.id
          join core.sources so on so.id = r.source_id
          group by 1, 2
        )
        select c.code as competition, s.label as season, s.id as competition_season_id,
               count(m.id) as finished,
               count(m.id) filter (where m.data_status = 'COMPLETE') as complete,
               count(m.id) filter (where m.data_status = 'DERIVED') as derived,
               count(m.id) filter (where m.data_status = 'PARTIAL_DRAW_UNKNOWN') as partial,
               count(m.id) filter (where m.data_status = 'INCOMPLETE') as incomplete,
               count(m.id) filter (where m.reconciliation_status = 'CONFLICT') as conflicts,
               count(m.id) filter (where m.ht_home is not null) as with_ht,
               coalesce((select jsonb_object_agg(p.code, jsonb_build_object('matches', p.matches,
                                                 'with_minutes', p.with_minutes))
                         from per_source p where p.competition_season_id = s.id), '{}'::jsonb) as sources
        from core.competition_seasons s
        join core.competitions c on c.id = s.competition_id
        join finished m on m.competition_season_id = s.id
        group by c.code, s.label, s.id
        order by c.code, s.label""")).mappings()]


def open_items(conn: Connection) -> dict[str, Any]:
    reviews = conn.execute(text("select count(*) from core.entity_review_queue where status = 'OPEN'")).scalar_one()
    issues = conn.execute(text("""
        select issue_code, severity::text as severity, count(*) as n from core.data_quality_issues
        where status = 'OPEN' group by 1, 2 order by 3 desc, 1""")).mappings().all()
    return {"open_reviews": int(reviews), "open_issues": [dict(r) for r in issues]}
