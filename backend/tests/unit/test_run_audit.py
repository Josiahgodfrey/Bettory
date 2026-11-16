"""Run audit rules: expected vs delivered, with the thresholds of config/data_quality.yaml."""

from app.domain.quality_config import load_quality_config
from app.ingestion.audit import AuditStatus, audit_run

CFG = load_quality_config()


def codes(stats: dict[str, int], error: str | None = None) -> tuple[AuditStatus, set[str]]:
    status, findings = audit_run(stats, error, CFG)
    return status, {f.code for f in findings}


def test_clean_run_is_ok() -> None:
    assert codes({"payloads": 3, "source_records": 380, "reconciliation:AGREED": 380}) == (AuditStatus.OK, set())


def test_failed_run_is_an_error() -> None:
    status, found = codes({"payloads": 1}, "QuotaError('no quota')")
    assert status is AuditStatus.ERROR and "RUN_FAILED" in found


def test_problems_that_need_attention_are_warnings() -> None:
    status, found = codes({"payloads": 2, "source_records": 100, "reconciliation:CONFLICT": 2,
                           "teams_sent_to_review": 1, "fixtures_requested_for_minutes": 10,
                           "records_with_goal_minutes": 7, "crosscheck:FAIL": 1})
    assert status is AuditStatus.WARNING
    assert {"SOURCE_CONFLICTS", "TEAMS_TO_REVIEW", "MINUTES_MISSING", "CROSSCHECK_FAILED"} <= found
    assert codes({"source_records": 0})[1] == {"NO_PAYLOADS"}


def test_skipped_records_warn_only_above_the_configured_share() -> None:
    few = {"payloads": 1, "source_records": 1000, "skipped:invalid_match:x": 1}
    many = {"payloads": 1, "source_records": 100, "skipped:invalid_match:x": 10}
    assert codes(few) == (AuditStatus.OK, {"SKIPPED_RECORDS"})             # INFO: 0.1 %
    assert codes(many) == (AuditStatus.WARNING, {"SKIPPED_RECORDS"})       # 9.1 % > 2 %


def test_information_does_not_change_the_status() -> None:
    status, found = codes({"payloads": 5, "source_records": 4, "stopped_at_request_budget": 1,
                           "fixtures_still_without_minutes": 200, "rate_limit_retries": 1,
                           "fixtures_requested_for_minutes": 4, "records_with_goal_minutes": 4,
                           "quota_limit": 100, "quota_remaining_after": 3, "issue:HT_MISSING": 2})
    assert status is AuditStatus.OK
    assert found == {"BUDGET_REACHED", "RATE_LIMIT_RETRIES", "QUOTA_LOW", "DATA_ISSUES"}
