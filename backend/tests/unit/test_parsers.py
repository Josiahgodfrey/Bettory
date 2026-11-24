"""Parsers against small slices of REAL Phase 0 payloads (football-data.co.uk: synthetic, see fixture name)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.domain.enums import FixtureStatus, MarketCode, SnapshotType, SourceCode
from app.domain.models import Score
from app.domain.registry import CompetitionDef, Registry, load_registry
from app.normalisation.parsers import ParseError
from app.normalisation.parsers import api_football as af
from app.normalisation.parsers import football_data_co_uk as uk

FIX = Path(__file__).resolve().parents[1] / "fixtures"
REG = load_registry()
ENG1 = REG.get("ENG1")


def read(name: str) -> bytes:
    return (FIX / name).read_bytes()


def test_registry_loads_and_rejects_duplicates() -> None:
    assert ENG1.providers[SourceCode.ESPN] == "eng.1"
    with pytest.raises(ValueError):
        Registry([ENG1, ENG1])


def _registry_with(af_league: str) -> Registry:
    test_comp = CompetitionDef(code="TST1", name="Test", country="Testland", type="LEAGUE", timezone="UTC",
                               season_format="CALENDAR", fifteen_minute_market=True,
                               team_sources=(SourceCode.API_FOOTBALL,),
                               providers={SourceCode.API_FOOTBALL: af_league})
    return Registry([test_comp])


def test_api_football_fixture_with_events() -> None:
    assert af.parse_fixtures(read("api_football_fixture_events.json"), REG).skipped["unregistered_competition"] == 1
    res = af.parse_fixtures(read("api_football_fixture_events.json"), _registry_with("722"))
    (m,) = res.matches
    assert m.status is FixtureStatus.FINISHED and m.has_goal_minutes and m.ft == Score(home=0, away=5)
    assert m.goals_consistent() and m.home_key and m.season_label == "2026"
    assert [g.minute for g in m.goals] == [17, 38, 43, 45, 88] and m.goals[0].penalty


def test_api_football_rejects_error_payload() -> None:
    with pytest.raises(ParseError):
        af.parse_fixtures(b'{"errors": {"plan": "Free plans do not have access"}, "response": []}', REG)


def test_api_football_odds_markets() -> None:
    res = af.parse_odds(read("api_football_odds.json"), _registry_with("71"))
    first15 = [o for o in res.odds if o.market is MarketCode.FIRST15_RESULT]
    assert {o.selection for o in first15} == {"HOME", "DRAW", "AWAY"}
    ht = [o for o in res.odds if o.market is MarketCode.HT_TOTAL]
    assert ht and all(o.selection in ("OVER", "UNDER") and o.line for o in ht)
    assert all(o.snapshot_type is SnapshotType.ANALYSIS and o.odds > 1.0 for o in res.odds)


def test_football_data_co_uk_synthetic_csv() -> None:
    res = uk.parse_csv(read("football_data_co_uk_E0_2526_SYNTHETIC.csv"), REG, "2025-26")
    assert len(res.matches) == 6 and not res.skipped
    first = res.matches[0]
    assert first.kickoff_utc == datetime(2025, 8, 15, 19, 0, tzinfo=UTC)   # 20:00 UK (BST)
    assert len(res.odds) == 30 and all(o.snapshot_type is SnapshotType.CLOSING for o in res.odds)
    assert {o.bookmaker for o in res.odds} == {"Pinnacle"}


def test_football_data_co_uk_missing_columns_rejected() -> None:
    with pytest.raises(ParseError):
        uk.parse_csv(b"Div,Date,HomeTeam\nE0,15/08/2025,Liverpool\n", REG, "2025-26")
