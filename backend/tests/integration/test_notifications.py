"""Telegram notifications (ARCHITECTURE.md 12.8): the outbox sends each event once (retries, failure, expiry); the
recorder cycle queues the report / no-matches message, failures (once an hour) and withdrawals; /me queues one
login alert per login session; the endpoints. A fake sender stands in for Telegram."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, text

from app.api.deps import Owner, require_owner, settings_dep
from app.core.settings import Settings
from app.notifications import outbox
from app.notifications.telegram import SendResult
from app.paper import cycle as cycle_mod
from app.paper.cycle import run_cycle
from app.paper.recheck import Rechecked, RecheckRun
from tests.integration.conftest import ADMIN_URL
from tests.integration.test_api_frontend import Clock, api
from tests.integration.test_paper_capture import AS_OF, capture
from tests.integration.test_paper_capture import engine as capture_engine  # noqa: F401 - fake API world
from tests.integration.test_paper_cycle import inputs, runs_at

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")


class FakeTelegram:
    def __init__(self, ok: bool = True) -> None:
        self.ok, self.sent = ok, []

    def __call__(self, message: str) -> SendResult:
        self.sent.append(message)
        return SendResult(True) if self.ok else SendResult(False, "HTTP 400: Bad Request")


def rows(engine: Engine) -> list[Any]:
    with engine.connect() as c:
        return c.execute(text("select kind, status, attempts, message from ops.notifications "
                              "order by created_at")).all()


def test_outbox_once_retries_and_expiry(capture_engine: Engine) -> None:  # noqa: F811
    engine = capture_engine
    assert outbox.notify(engine, "report:x", "REPORT_READY", "hello")
    assert not outbox.notify(engine, "report:x", "REPORT_READY", "hello again")    # same event: ignored
    assert outbox.deliver(engine, None).waiting == 1           # not configured: waits
    tg = FakeTelegram()
    d = outbox.deliver(engine, tg)
    assert (d.sent, tg.sent) == (1, ["hello"]) and rows(engine)[0][:2] == ("REPORT_READY", "SENT")
    assert outbox.deliver(engine, tg).sent == 0               # sent once

    outbox.notify(engine, "fail:y", "FAILURE", "boom")
    bad = FakeTelegram(ok=False)
    for _ in range(3):
        outbox.deliver(engine, bad)
    assert rows(engine)[1][:3] == ("FAILURE", "FAILED", 3) and len(bad.sent) == 3
    outbox.notify(engine, "fail:z", "FAILURE", "late")
    with engine.begin() as c:
        c.execute(text("update ops.notifications set created_at = created_at - interval '25 hours' "
                       "where event_key = 'fail:z'"))
    assert outbox.deliver(engine, tg).expired == 1
    with engine.connect() as c:
        assert c.execute(text("select status from ops.notifications where event_key = 'fail:z'")).scalar() == "EXPIRED"


def test_cycle_queues_report_failures_and_withdrawals(capture_engine: Engine, tmp_path: Path,  # noqa: F811
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    engine = capture_engine
    capture(engine, tmp_path, 2)                                           # fixtures known, not a recorder run
    runs_at(engine, AS_OF - timedelta(days=1))
    tg = FakeTelegram()
    x = inputs(engine, tmp_path)
    x = type(x)(**{**x.__dict__, "send": tg,
                   "settings": Settings(api_football_key=SecretStr(""), app_url="https://app.example")})

    window = AS_OF - x.cfg.capture.lead
    first = run_cycle(x, now=window + timedelta(minutes=1))                # capture due, no key: a failure
    assert any(ln.startswith("! ") for ln in first.lines)
    run_cycle(x, now=window + timedelta(minutes=6))                        # same failure, same hour: once
    assert [r[0] for r in rows(engine)].count("FAILURE") == 1 and tg.sent[0].startswith("❗ BETTORY recorder")

    run_cycle(x, now=AS_OF + timedelta(minutes=1))                         # freeze: nothing qualifies
    kinds = [r[0] for r in rows(engine)]
    assert "NO_MATCHES" in kinds and any("0 matches passed verification" in m for m in tg.sent)

    with engine.connect() as c:
        mid, ko = c.execute(text("select id, kickoff_utc from core.matches order by kickoff_utc limit 1")).one()
    fake = RecheckRun(due=1, results=[Rechecked("p-1", str(mid), "s", ko, "WITHDRAWN", ("CONTEXT_X",))])
    monkeypatch.setattr(cycle_mod, "run_rechecks", lambda *a, **k: fake)
    run_cycle(x, now=AS_OF + timedelta(minutes=7))
    run_cycle(x, now=AS_OF + timedelta(minutes=12))                        # the same withdrawal: queued once
    assert [r[0] for r in rows(engine)].count("WITHDRAWN") == 1
    assert any(m.startswith("⚠️ Withdrawn: Club") and "CONTEXT_X" in m for m in tg.sent)


def test_login_alert_and_endpoints(capture_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    engine = capture_engine
    clock = Clock()
    clock.now = datetime.now(UTC)
    c: TestClient = api(engine, clock)
    app: Any = c.app
    app.dependency_overrides[require_owner] = lambda: Owner(user_id="o", aal="aal2", session_id="sess-1")
    app.dependency_overrides[settings_dep] = lambda: Settings(telegram_bot_token=SecretStr(""), telegram_chat_id="")
    assert c.get("/me").status_code == 200 and c.get("/me").status_code == 200
    listing = c.get("/notifications").json()
    assert listing["configured"] is False and [i["kind"] for i in listing["items"]] == ["LOGIN"]
    assert listing["items"][0]["status"] == "PENDING" and listing["counts"] == {"PENDING": 1}
    assert c.post("/notifications/test").status_code == 409

    tg = FakeTelegram()
    monkeypatch.setattr(outbox, "sender", lambda settings: tg)
    app.dependency_overrides[settings_dep] = lambda: Settings(telegram_bot_token=SecretStr("t"), telegram_chat_id="1")
    assert c.post("/notifications/test").json() == {"sent": 2, "failed": 0}      # the waiting login alert too
    assert any("New login" in m for m in tg.sent) and any("test message" in m for m in tg.sent)
