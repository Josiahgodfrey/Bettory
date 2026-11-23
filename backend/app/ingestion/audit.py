"""Run audit: what a finished ingestion run should have delivered vs what it did. Pure.

Every run gets OK / WARNING / ERROR and a list of findings (docs/DATA-HEALTH.md 3). The audit never changes
data; it makes problems visible where they would otherwise hide in the statistics.

    ERROR    the run failed
    WARNING  something needs attention: source conflicts, team names to review, too many skipped records,
             goal minutes requested but not delivered, a failed or impossible cross-check, no payload at all
    INFO     worth knowing, nothing wrong: request budget reached (run again), rate-limit retries, data
             issues recorded per match, quota nearly used up
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.domain.quality_config import QualityConfig


class Level(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class AuditStatus(StrEnum):
    OK = "OK"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True)
class Finding:
    level: Level
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"level": self.level.value, "code": self.code, "message": self.message}


def _prefixed(stats: Mapping[str, int], prefix: str) -> dict[str, int]:
    return {k[len(prefix):]: v for k, v in stats.items() if k.startswith(prefix) and v}


def audit_run(stats: Mapping[str, int], error: str | None, cfg: QualityConfig) -> tuple[AuditStatus, list[Finding]]:
    f: list[Finding] = []
    s = dict(stats)
    if error is not None:
        f.append(Finding(Level.ERROR, "RUN_FAILED", error[:500]))
    elif not s.get("payloads"):
        f.append(Finding(Level.WARNING, "NO_PAYLOADS", "the run stored no payload: nothing was fetched"))

    if conflicts := s.get("reconciliation:CONFLICT", 0):
        f.append(Finding(Level.WARNING, "SOURCE_CONFLICTS",
                         f"{conflicts} matches where sources disagree: excluded from statistics until resolved"))
    review = s.get("teams_sent_to_review", 0) + s.get("records_waiting_for_team_review", 0)
    if review:
        f.append(Finding(Level.WARNING, "TEAMS_TO_REVIEW",
                         f"{s.get('teams_sent_to_review', 0)} team names sent to review, "
                         f"{s.get('records_waiting_for_team_review', 0)} records waiting for them"))

    skipped = _prefixed(s, "skipped:")
    if skipped:
        total = sum(skipped.values())
        share = total / (total + s.get("source_records", 0))
        level = Level.WARNING if share > cfg.run_audit.max_skipped_share else Level.INFO
        detail = ", ".join(f"{k} {v}" for k, v in sorted(skipped.items()))
        f.append(Finding(level, "SKIPPED_RECORDS", f"{total} records skipped ({share:.1%}): {detail}"))

    issues = _prefixed(s, "issue:")
    if issues:
        f.append(Finding(Level.INFO, "DATA_ISSUES",
                         "recorded per match: " + ", ".join(f"{k} {v}" for k, v in sorted(issues.items()))))

    requested = s.get("fixtures_requested_for_minutes", 0)
    delivered = s.get("records_with_goal_minutes", 0)
    if requested and delivered < requested:
        f.append(Finding(Level.WARNING, "MINUTES_MISSING",
                         f"goal minutes requested for {requested} fixtures, delivered for {delivered}"))
    if s.get("stopped_at_request_budget"):
        f.append(Finding(Level.INFO, "BUDGET_REACHED",
                         f"request budget reached: {s.get('fixtures_still_without_minutes', 0)} fixtures still "
                         "without goal minutes - run again (resumes where it stopped)"))
    espn_errors = {k: s.get(k, 0) for k in ("scoreboard_errors", "schedule_errors", "summary_errors") if s.get(k)}
    if espn_errors:
        f.append(Finding(Level.WARNING, "PAGES_FAILED", "pages that could not be fetched (skipped, the next run tries "
                         "again): " + ", ".join(f"{k.removesuffix('_errors')} {v}" for k, v in espn_errors.items())))
    if teams := s.get("teams_without_espn_link", 0):
        f.append(Finding(Level.INFO, "TEAMS_NOT_LINKED", f"{teams} fixture teams without an ESPN id yet: their "
                         "histories were not loaded (linked by a later run, or through the review queue)"))
    if retries := s.get("rate_limit_retries", 0):
        f.append(Finding(Level.INFO, "RATE_LIMIT_RETRIES", f"{retries} requests retried after the per-minute limit"))

    if failed := s.get("crosscheck:FAIL", 0):
        f.append(Finding(Level.WARNING, "CROSSCHECK_FAILED",
                         f"{failed} competition-seasons below the source-agreement threshold: their matches "
                         "cannot qualify until reviewed (python -m app.cli crosscheck ... / accept)"))
    if s.get("crosscheck_errors"):
        f.append(Finding(Level.WARNING, "CROSSCHECK_ERROR", "the cross-check after the run could not be completed"))

    left = s.get("quota_remaining_after")
    if left is not None and "quota_limit" in s and left <= cfg.api_quota.reserve_requests:
        f.append(Finding(Level.INFO, "QUOTA_LOW", f"{left} API requests left today (limit {s['quota_limit']})"))

    levels = {x.level for x in f}
    status = (AuditStatus.ERROR if Level.ERROR in levels else
              AuditStatus.WARNING if Level.WARNING in levels else AuditStatus.OK)
    return status, f
