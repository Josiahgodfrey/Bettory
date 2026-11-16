"""Parsers against small slices of REAL Phase 0 payloads (football-data.co.uk: synthetic, see fixture name)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.domain.enums import FixtureStatus, MarketCode, SnapshotType, SourceCode
from app.domain.models import Score
from app.domain.registry import CompetitionDef, Registry, load_registry
from app.normalisation.names import normalize_team_name as norm
from app.normalisation.parsers import ParseError
from app.normalisation.parsers import api_football as af
from app.normalisation.parsers import football_data_co_uk as uk
from app.normalisation.parsers import football_data_org as fd
from app.normalisation.parsers import openfootball as of

FIX = Path(__file__).resolve().parents[1] / "fixtures"
REG = load_registry()
ENG1 = REG.get("ENG1")


def read(name: str) -> bytes:
    return (FIX / name).read_bytes()


def test_registry_loads_and_rejects_duplicates() -> None:
    assert ENG1.providers[SourceCode.OPENFOOTBALL] == "en.1"
    with pytest.raises(ValueError):
        Registry([ENG1, ENG1])


def test_openfootball_full_file() -> None:
    res = of.parse_season_file(read("openfootball_en.1-full_2025-26.json"), ENG1, "2025-26", full_file=True)
    assert len(res.matches) == 6 and not res.skipped
    first = res.matches[0]
    assert (first.home_name, first.away_name) == ("Liverpool", "Bournemouth")
    assert first.kickoff_utc == datetime(2025, 8, 15, 19, 0, tzinfo=UTC)          # "20:00 UTC+1"
    assert first.ft == Score(home=4, away=2) and first.ht == Score(home=1, away=0)
    assert all(m.goals_consistent() for m in res.matches)
    zero = [m for m in res.matches if m.ft and m.ft.total == 0]
    assert zero and all(m.has_goal_minutes and not m.goals for m in zero)        # 0-0 written without lists


def test_openfootball_plain_file_has_no_minutes_but_same_kickoff() -> None:
    full = of.parse_season_file(read("openfootball_en.1-full_2025-26.json"), ENG1, "2025-26", full_file=True)
    plain = of.parse_season_file(read("openfootball_en.1_2025-26.json"), ENG1, "2025-26", full_file=False)
    assert not any(m.has_goal_minutes for m in plain.matches)
    assert [m.kickoff_utc for m in plain.matches] == [m.kickoff_utc for m in full.matches]  # local tz == offset


def test_file_key() -> None:
    assert of.file_key("en.1-full.json") == ("en.1", True)
    assert of.file_key("de.1.json") == ("de.1", False)


def test_football_data_org_agrees_with_openfootball() -> None:
    res = fd.parse_matches(read("football_data_org_PL_2025.json"), REG)
    full = of.parse_season_file(read("openfootball_en.1-full_2025-26.json"), ENG1, "2025-26", full_file=True)
    by_pair = {(norm(m.home_name), norm(m.away_name)): m for m in full.matches}
    assert len(res.matches) == 6
    for m in res.matches:
        o = by_pair[(norm(m.home_name), norm(m.away_name))]
        assert (m.kickoff_utc, m.ft, m.ht) == (o.kickoff_utc, o.ft, o.ht)
        assert m.home_key and m.season_label == "2025-26" and not m.has_goal_minutes


def test_football_data_org_rejects_error_payload() -> None:
    with pytest.raises(ParseError):
        fd.parse_matches(b'{"message": "Your API token is invalid.", "errorCode": 400}', REG)


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
