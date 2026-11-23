"""Params 1.6 with the shipped recorder config (owner decision 2026-11-23): probabilities only, no odds.

Both sessions of a day run by themselves: planned at 07:00 / 19:00 EAT, analysed and sealed exactly 2 hours before
their window opens (Noon 11:00, Night 23:00 the evening before), not twice, and without one odds request.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine

from app.domain.sessions import SessionCalendar, SessionId, SessionKind
from app.paper.cycle import run_cycle
from app.paper.plan import plan_time
from app.storage.repositories import paper as paper_repo
from tests.integration.conftest import ADMIN_URL
from tests.integration.test_paper_capture import DAY, FIXTURES, FakeClient, engine  # noqa: F401
from tests.integration.test_paper_cycle import SHIPPED, inputs, runs_at
from tests.support import LIVE

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")
CFG = SHIPPED.model_copy(update={"history": SHIPPED.history.model_copy(update={"enabled": False})})
CAL = SessionCalendar(LIVE)
NOON = SessionId(DAY, SessionKind.NOON)                       # fixtures 9901 (15:00 EAT) and 9902 (19:00 EAT)
NIGHT = SessionId(DAY + timedelta(days=1), SessionKind.NIGHT)  # fixture 9904 (02:00 EAT on DAY + 1)


def odds_requests() -> list[dict[str, object]]:
    return [c for c in FakeClient.calls if "fixture" in c]


def test_shipped_config_has_no_odds_steps() -> None:
    assert not CFG.capture.enabled and not CFG.closing.enabled and not CFG.recheck.with_odds
    assert LIVE.probability is not None and LIVE.params_version == "1.6"


@pytest.mark.parametrize("sid,hour", [(NOON, 11), (NIGHT, 23)])
def test_analysis_is_sealed_two_hours_before_the_window(sid: SessionId, hour: int) -> None:
    t = CAL.times(sid)
    for first in (t.start_utc, t.start_utc + timedelta(hours=6)):        # wherever the first kick-off falls
        as_of = CAL.run_start(sid, first)
        assert as_of == t.start_utc - timedelta(hours=2) and as_of.astimezone(CAL.tz).hour == hour
        assert plan_time(CAL, CFG, sid) < as_of < first                    # planned before, finished before kick-off


def test_both_sessions_run_automatically_without_odds(engine: Engine, tmp_path: Path) -> None:  # noqa: F811
    x = replace(inputs(engine, tmp_path, cfg=CFG), params=LIVE)
    for sid, n in ((NOON, 2), (NIGHT, 1)):
        plan_at = plan_time(CAL, CFG, sid)
        first = min(ko for _, lg, ko in FIXTURES if lg == "990" and CAL.session_of(ko) == sid)
        as_of = CAL.run_start(sid, first)

        r = run_cycle(x, now=plan_at + timedelta(minutes=1))                # 1. plan: the fixtures are listed
        runs_at(engine, plan_at + timedelta(minutes=1))
        assert any(f"{sid} plan:" in ln for ln in r.lines), r.lines

        before = run_cycle(x, now=as_of - timedelta(minutes=5))             # 2. nothing sealed before as_of
        with engine.connect() as c:
            assert paper_repo.frozen(c, str(sid)) == []
        assert not [ln for ln in before.lines if "freeze" in ln]

        done = run_cycle(x, now=as_of + timedelta(minutes=1))               # 3. analysed and sealed at as_of
        assert done.errors == 0 and any(f"{sid} freeze FROZEN: new {n}" in ln for ln in done.lines), done.lines
        assert not [ln for ln in done.lines if "no capture" in ln]
        with engine.connect() as c:
            rows = paper_repo.frozen(c, str(sid))
        assert len(rows) == n and all(r.params_version == "1.6" and r.as_of == as_of for r in rows)
        for row in rows:                                                    # the reason is kept with every game
            assert row.decision in ("QUALIFIED", "SKIP", "INSUFFICIENT_DATA", "ERROR")
            assert row.decision == "QUALIFIED" or row.failed_checks                # a failed game keeps its reason
            for p in (row.p_under05, row.p_draw):
                assert p is None or 0.0 <= p <= 1.0
            if row.p_under05 is not None and row.p_draw is not None:
                assert row.p_under05 <= row.p_draw

        again = run_cycle(x, now=as_of + timedelta(minutes=6))              # 4. a repeated cycle adds nothing
        assert not [ln for ln in again.lines if "freeze" in ln]
        with engine.connect() as c:
            assert len(paper_repo.frozen(c, str(sid))) == n

    assert odds_requests() == []                                            # not one odds request in either session
