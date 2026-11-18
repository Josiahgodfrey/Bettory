"""Backend for the whole-application (end-to-end) tests: the real API on a throwaway database.

    TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres \\
        venv/Scripts/python -m tests.e2e.server --port 8100

Started by Playwright (frontend/playwright.config.ts). What it does:
1. isolates the process from every real service exactly like the test suite (tests/conftest.py: no Supabase, no
   Telegram connection possible, no API-Football / football-data.org key, local raw store);
2. creates a fresh database on the local test server (never Supabase: TEST_DATABASE_URL must be a local host),
   applies every migration and seeds the synthetic league TST1 (tests/integration/test_backtest_runner.py);
3. schedules two fixtures about 40 and 50 minutes from now and **seals** their session with the real freeze, so the
   report is published while the matches have not started — the moment the owner bets;
4. serves the real FastAPI app with the test league's registry and params, the owner already authenticated.

The real clock is used throughout (a bet's placed_at must not be later than the database clock). Databases from
earlier runs (`bettory_e2e_*`) are dropped at start.
"""

from __future__ import annotations

import argparse
import atexit
import os
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

os.environ.setdefault("TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/postgres")
import tests.conftest  # noqa: F401 - isolation from the real services, before the app is imported

os.environ["ENVIRONMENT"] = "local"
os.environ["CORS_ORIGINS"] = '["http://127.0.0.1:5190","http://localhost:5190"]'

import psycopg
import uvicorn
from sqlalchemy import create_engine

from app.api.deps import (
    Owner,
    engine_dep,
    params_dep,
    raw_store_factory_dep,
    registry_dep,
    report_competitions_dep,
    require_owner,
)
from app.core.settings import get_settings
from app.domain.sessions import SessionCalendar
from app.main import create_app
from app.paper.freeze import Provenance, freeze_session
from app.storage.raw_store import LocalRawStore
from tests.integration.test_backtest_runner import REGISTRY, seed_league
from tests.integration.test_paper_freeze import schedule
from tests.support import PARAMS

ADMIN_URL = os.environ["TEST_DATABASE_URL"]
MIGRATIONS = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
PREFIX = "bettory_e2e_"


def _admin() -> psycopg.Connection:
    host = urlparse(ADMIN_URL).hostname
    if host not in ("127.0.0.1", "localhost"):
        raise SystemExit(f"TEST_DATABASE_URL must point to a local throwaway server, not {host}")
    return psycopg.connect(ADMIN_URL, autocommit=True)


def create_database() -> tuple[str, str]:
    name = f"{PREFIX}{uuid.uuid4().hex[:8]}"
    with _admin() as admin:
        for (old,) in admin.execute("select datname from pg_database where datname like %s", (f"{PREFIX}%",)):
            admin.execute(f'drop database "{old}" with (force)')
        admin.execute(f'create database "{name}"')
    url = ADMIN_URL.rsplit("/", 1)[0] + f"/{name}"
    with psycopg.connect(url, autocommit=True) as conn:
        for sql_file in sorted(MIGRATIONS.glob("*.sql")):
            conn.execute(sql_file.read_text(encoding="utf-8"))
    return name, url


def drop_database(name: str) -> None:
    try:
        with _admin() as admin:
            admin.execute(f'drop database if exists "{name}" with (force)')
    except Exception:  # best effort at exit; the next run drops it anyway
        pass


def sealed_session(url: str) -> str:
    """Two fixtures ~40 and ~50 minutes ahead, frozen now: a published session whose matches have not started."""
    cal = SessionCalendar(PARAMS)
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    for start in (40, 30, 20):  # keep both kick-offs in one session (a session boundary may fall in between)
        kos = [now + timedelta(minutes=start), now + timedelta(minutes=start + 10)]
        sid = cal.session_of(kos[0])
        if cal.session_of(kos[1]) == sid and cal.run_start(sid, kos[0]) <= now:
            break
    else:
        raise SystemExit("could not place two fixtures in one already cut-off session")
    schedule(url, kos)
    engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://"))
    r = freeze_session(engine, PARAMS, REGISTRY, sid, now=datetime.now(UTC),
                       provenance=Provenance("tuning_e2e.yaml", "e" * 64, "e2e"))
    engine.dispose()
    if r.status != "FROZEN" or r.frozen != 2:
        raise SystemExit(f"freeze failed: {r}")
    return str(sid)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8100)
    port = ap.parse_args().port

    get_settings.cache_clear()
    s = get_settings()
    assert not s.telegram_bot_token.get_secret_value() and not s.api_football_key.get_secret_value()

    name, url = create_database()
    atexit.register(drop_database, name)
    seed_league(url)
    sid = sealed_session(url)

    engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://"))
    raw = Path(tempfile.mkdtemp(prefix="bettory-e2e-raw-"))
    app = create_app()
    app.dependency_overrides[engine_dep] = lambda: engine
    app.dependency_overrides[registry_dep] = lambda: REGISTRY
    app.dependency_overrides[params_dep] = lambda: PARAMS
    app.dependency_overrides[report_competitions_dep] = lambda: ("TST1",)
    app.dependency_overrides[require_owner] = lambda: Owner(user_id="e2e", aal="aal2")
    app.dependency_overrides[raw_store_factory_dep] = lambda: (lambda: LocalRawStore(raw))
    print(f"e2e backend: database {name}, sealed session {sid}, http://127.0.0.1:{port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
