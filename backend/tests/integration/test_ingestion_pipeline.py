"""End-to-end ingestion against a fresh database, using stored real payloads (no network)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, text

from app.domain.enums import SourceCode
from app.domain.registry import load_registry
from app.ingestion.base import Fetched
from app.ingestion.pipeline import Job
from app.normalisation.parsers import football_data_co_uk as p_uk
from app.normalisation.parsers import football_data_org as p_fd
from app.normalisation.parsers import openfootball as p_of
from app.storage.raw_store import LocalRawStore
from tests.integration.conftest import ADMIN_URL

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")
FIX = Path(__file__).resolve().parents[1] / "fixtures"
REG = load_registry()
ENG1 = REG.get("ENG1")


@pytest.fixture
def engine(fresh_db_url: str) -> Iterator[Engine]:
    eng = create_engine(fresh_db_url.replace("postgresql://", "postgresql+psycopg://"))
    yield eng
    eng.dispose()


def run(engine: Engine, tmp_path: Path, source: SourceCode, fixture: str, parse, reparse: bool = False):  # type: ignore[no-untyped-def]
    job = Job(engine, LocalRawStore(tmp_path), REG, source, f"test:{fixture}", reparse)
    job.process(Fetched(endpoint=fixture, params={}, http_status=200, body=(FIX / fixture).read_bytes(),
                        ext=fixture.rsplit(".", 1)[1]), parse)
    return job.finish(requests_used=0)


def q(engine: Engine, sql: str) -> list[tuple]:  # type: ignore[type-arg]
    with engine.connect() as c:
        return [tuple(r) for r in c.execute(text(sql))]


def test_full_pipeline(engine: Engine, tmp_path: Path) -> None:
    full = lambda raw: p_of.parse_season_file(raw, ENG1, "2025-26", True)     # noqa: E731
    plain = lambda raw: p_of.parse_season_file(raw, ENG1, "2025-26", False)   # noqa: E731
    fdorg = lambda raw: p_fd.parse_matches(raw, REG)                          # noqa: E731

    # 1. openfootball -full: creates teams + matches with goal minutes
    s1 = run(engine, tmp_path, SourceCode.OPENFOOTBALL, "openfootball_en.1-full_2025-26.json", full)
    assert s1["matches_created"] == 6 and s1["teams_created"] == 12 and s1["data_status:COMPLETE"] == 6
    assert any(tmp_path.rglob("*.json.gz"))                                   # raw file stored

    # 2. football-data.org: matches existing teams by alias, agrees on every match
    s2 = run(engine, tmp_path, SourceCode.FOOTBALL_DATA_ORG, "football_data_org_PL_2025.json", fdorg)
    assert s2["teams_matched_by_alias"] == 12 and s2["reconciliation:AGREED"] == 6 and "teams_created" not in s2

    # 3. openfootball plain file (no minutes) must NOT erase the goal minutes
    s3 = run(engine, tmp_path, SourceCode.OPENFOOTBALL, "openfootball_en.1_2025-26.json", plain)
    assert s3["data_status:COMPLETE"] == 6

    # 4. same payload again: skipped, nothing changes
    s4 = run(engine, tmp_path, SourceCode.FOOTBALL_DATA_ORG, "football_data_org_PL_2025.json", fdorg)
    assert s4["payloads_unchanged_skipped"] == 1 and "source_records" not in s4

    # 5. football-data.co.uk short names: no duplicates - unknown names wait for review
    s5 = run(engine, tmp_path, SourceCode.FOOTBALL_DATA_CO_UK, "football_data_co_uk_E0_2526_SYNTHETIC.csv",
             lambda raw: p_uk.parse_csv(raw, REG, "2025-26"))
    assert "teams_created" not in s5 and s5["teams_sent_to_review"] >= 1

    assert q(engine, "select count(*) from core.teams") == [(12,)]
    assert q(engine, "select count(*) from core.matches") == [(6,)]
    assert q(engine, "select count(*) from core.matches where data_status = 'COMPLETE' "
                     "and reconciliation_status = 'AGREED'") == [(6,)]
    assert q(engine, "select h15_home, h15_away, goal_before_15 from core.matches m join core.teams t "
                     "on t.id = m.home_team_id where t.name = 'Liverpool'") == [(0, 0, False)]
    assert q(engine, "select count(*) from core.match_goals") == [(14,)]
    assert q(engine, "select count(*) from core.entity_review_queue where status = 'OPEN'")[0][0] >= 1
    assert q(engine, "select status from raw.ingestion_runs order by started_at") == [("SUCCEEDED",)] * 5
