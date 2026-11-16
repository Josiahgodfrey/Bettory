"""Checks that run by themselves (docs/DATA-HEALTH.md): cross-check a competition-season and store the result.

Called after every ingestion run for each competition-season the run wrote to, and by hand
(`python -m app.cli crosscheck`). The stored latest result is what the decision layer reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Connection

from app.domain.quality_config import CrossCheckRules
from app.normalisation.crosscheck import CrossCheckReport, CrossCheckVerdict, cross_check, evaluate
from app.storage.repositories import health as health_repo
from app.storage.repositories import matches as matches_repo


@dataclass(frozen=True)
class StoredCrossCheck:
    id: UUID
    report: CrossCheckReport
    verdict: CrossCheckVerdict


def crosscheck_season(conn: Connection, competition_season_id: UUID, cutoff_minute: int, rules: CrossCheckRules,
                      run_id: UUID | None = None) -> StoredCrossCheck:
    ids = health_repo.season_match_ids(conn, competition_season_id)
    report = cross_check(((str(i), matches_repo.latest_records(conn, i)) for i in ids), cutoff_minute)
    verdict = evaluate(report, rules)
    row = health_repo.save_crosscheck(conn, competition_season_id, report, verdict, rules, run_id)
    return StoredCrossCheck(row, report, verdict)
