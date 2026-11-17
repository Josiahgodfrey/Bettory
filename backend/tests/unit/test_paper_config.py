"""The recorder config (config/paper.yaml) and the worker's interval job."""

from __future__ import annotations

from pathlib import Path

import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from pydantic import ValidationError

from app.jobs.scheduler import IntervalJob, _guarded
from app.paper.config import PaperConfig, load_paper_config


def test_shipped_config_is_valid_and_fits_the_quota_split() -> None:
    cfg = load_paper_config()
    assert cfg.capture.lead_minutes > cfg.schedule.every_minutes
    assert cfg.daily_max_requests.for_plan("free") == 25                      # owner decision 2026-11-17
    assert cfg.capture.max_requests.for_plan("free") <= cfg.daily_max_requests.for_plan("free")
    assert cfg.capture.max_requests.for_plan("pro") == cfg.capture.max_requests.pro


def test_capture_window_must_be_longer_than_the_cycle(tmp_path: Path) -> None:
    raw = load_paper_config().model_dump()
    raw["schedule"]["every_minutes"] = raw["capture"]["lead_minutes"]
    with pytest.raises(ValidationError, match="lead_minutes"):
        PaperConfig.model_validate(raw)
    raw = load_paper_config().model_dump()
    raw["unknown"] = 1
    with pytest.raises(ValidationError):
        PaperConfig.model_validate(raw)


def test_interval_job_failure_does_not_stop_the_scheduler() -> None:
    calls: list[int] = []

    def boom() -> None:
        calls.append(1)
        raise RuntimeError("x")

    _guarded(IntervalJob("j", "job", boom, 5))                                # logged, not raised
    assert calls == [1]
    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(_guarded, "interval", minutes=5, args=[IntervalJob("j", "job", boom, 5)])
    assert len(sched.get_jobs()) == 1


def test_closing_window_must_be_longer_than_the_cycle() -> None:
    raw = load_paper_config().model_dump()
    raw["closing"]["minutes_before"] = raw["schedule"]["every_minutes"]
    with pytest.raises(ValidationError, match="closing.minutes_before"):
        PaperConfig.model_validate(raw)
