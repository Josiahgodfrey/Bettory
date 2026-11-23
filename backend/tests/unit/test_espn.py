"""ESPN parsers (real payloads, trimmed) and team pairing (docs/ESPN.md)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.domain.enums import FixtureStatus, Side, SourceCode
from app.domain.models import Score, SourceMatch
from app.domain.registry import load_registry
from app.normalisation.pairing import StoredMatch, pair
from app.normalisation.parsers import ParseError
from app.normalisation.parsers import espn as p

FIX = Path(__file__).resolve().parents[1] / "fixtures"
REG = load_registry()


def raw(name: str) -> bytes:
    return (FIX / name).read_bytes()


def test_summary_goals_with_own_goal_and_half_time() -> None:
    """Fulham 1-1 Manchester United (2025-08-24): own goal by Fulham's Muniz 58', credited to United."""
    r = p.parse_summary(raw("espn_summary_eng1_owngoal.json"), REG.get("ENG1"))
    (m,) = r.matches
    assert (m.source, m.provider_match_key, m.season_label) == (SourceCode.ESPN, m.provider_match_key, "2025-26")
    assert (m.home_name, m.away_name, m.home_key, m.away_key) == ("Fulham", "Manchester United", "370", "360")
    assert m.status is FixtureStatus.FINISHED and m.ft == Score(home=1, away=1) and m.ht == Score(home=0, away=0)
    assert m.has_goal_minutes and m.goals_consistent()
    own, fulham = m.goals
    assert (own.side, own.minute, own.own_goal, own.player) == (Side.AWAY, 58, True, "Rodrigo Muniz")
    assert (fulham.side, fulham.minute, fulham.own_goal) == (Side.HOME, 73, False)
    assert m.kickoff_utc == datetime(2025, 8, 24, 15, 30, tzinfo=UTC)
    assert not r.skipped


def test_summary_with_a_missing_goal_keeps_the_score_but_no_minutes() -> None:
    """Blau-Weiss Linz 0-2 Ried (2025-08-23): ESPN lists one of the two goals - never a guessed timeline."""
    r = p.parse_summary(raw("espn_summary_aut1_goal_missing.json"), REG.get("AUT1"))
    (m,) = r.matches
    assert m.ft == Score(home=0, away=2)
    assert not m.has_goal_minutes and m.goals == ()
    assert r.skipped["goals_incomplete"] == 1


def test_summary_goal_without_minute_or_unknown_team_is_incomplete() -> None:
    data = json.loads(raw("espn_summary_eng1_owngoal.json"))
    goal = next(e for e in data["keyEvents"] if e.get("scoringPlay"))
    goal["clock"] = {"displayValue": ""}
    (m,) = p.parse_summary(json.dumps(data).encode(), REG.get("ENG1")).matches
    assert not m.has_goal_minutes
    goal["clock"] = {"displayValue": "58'"}
    goal["team"] = {"id": "999999"}
    (m,) = p.parse_summary(json.dumps(data).encode(), REG.get("ENG1")).matches
    assert not m.has_goal_minutes


def test_summary_extra_time_and_shootout() -> None:
    data = json.loads(raw("espn_summary_eng1_owngoal.json"))
    data["header"]["competitions"][0]["status"]["type"]["name"] = "STATUS_FINAL_PEN"
    r = p.parse_summary(json.dumps(data).encode(), REG.get("ENG1"))
    assert not r.matches and r.skipped["status_not_stored"] == 1
    data = json.loads(raw("espn_summary_eng1_owngoal.json"))
    data["keyEvents"].append({"scoringPlay": True, "shootout": True, "team": {"id": "370"},
                              "clock": {"displayValue": "120'"}, "type": {"text": "Goal"}})
    (m,) = p.parse_summary(json.dumps(data).encode(), REG.get("ENG1")).matches
    assert m.has_goal_minutes and len(m.goals) == 2  # the shoot-out kick is not a goal


def test_stoppage_time_minutes() -> None:
    assert p.minute_label("45'+2'") == "45+2"
    assert p.minute_label("90' + 5'") == "90+5"
    assert p.minute_label("13'") == "13"
    assert p.minute_label("HT") is None and p.minute_label("") is None


def test_summary_without_header_is_a_parse_error() -> None:
    with pytest.raises(ParseError):
        p.parse_summary(b'{"keyEvents": []}', REG.get("ENG1"))


def test_scoreboard_gives_fixtures_and_scores_never_minutes() -> None:
    r = p.parse_scoreboard(raw("espn_scoreboard_eng1_20261122.json"), REG.get("ENG1"))
    assert len(r.matches) == 2
    liv = next(m for m in r.matches if m.home_name == "Liverpool")
    assert liv.ft == Score(home=4, away=0) and liv.season_label == "2026-27"
    assert all(not m.has_goal_minutes and m.goals == () for m in r.matches)


def test_scoreboard_scheduled_and_postponed() -> None:
    data = json.loads(raw("espn_scoreboard_eng1_20261122.json"))
    a, b = data["events"]
    a["competitions"][0]["status"] = {"type": {"name": "STATUS_SCHEDULED", "state": "pre", "completed": False}}
    b["competitions"][0]["status"] = {"type": {"name": "STATUS_POSTPONED", "state": "post", "completed": False}}
    m1, m2 = p.parse_scoreboard(json.dumps(data).encode(), REG.get("ENG1")).matches
    assert (m1.status, m1.ft) == (FixtureStatus.SCHEDULED, None)
    assert (m2.status, m2.ft) == (FixtureStatus.POSTPONED, None)


def test_schedule_newest_first_and_season_year() -> None:
    events = p.parse_schedule(raw("espn_schedule_eng1_liverpool.json"))
    assert len(events) == 11
    assert events == sorted(events, key=lambda e: e.kickoff_utc, reverse=True)
    assert all(e.completed for e in events) and all("364" in (e.home_id, e.away_id) for e in events)
    assert p.schedule_season_year(raw("espn_schedule_eng1_liverpool.json")) == 2026


def test_calendar_season_label() -> None:
    data = json.loads(raw("espn_scoreboard_eng1_20261122.json"))
    for e in data["events"]:
        e["season"] = {"year": 2026}
    r = p.parse_scoreboard(json.dumps(data).encode(), REG.get("BRA1"))
    assert {m.season_label for m in r.matches} == {"2026"}


# --- pairing ----------------------------------------------------------------------------------------------

KO = datetime(2026, 11, 22, 16, 30, tzinfo=UTC)


def source_match(ft: tuple[int, int] | None = (4, 0)) -> SourceMatch:
    return SourceMatch(source=SourceCode.ESPN, provider_match_key="1", competition_code="ENG1", season_label="2026-27",
                       kickoff_date=KO.date(), kickoff_utc=KO, home_name="Liverpool", away_name="Man Utd",
                       home_key="364", away_key="360",
                       status=FixtureStatus.FINISHED if ft else FixtureStatus.SCHEDULED,
                       ft=Score(home=ft[0], away=ft[1]) if ft else None, has_goal_minutes=False)


def test_pairing_links_the_opponent_of_a_known_team() -> None:
    liv, utd, other = uuid4(), uuid4(), uuid4()
    stored = [StoredMatch(uuid4(), liv, utd, KO + timedelta(minutes=30), (4, 0)),
              StoredMatch(uuid4(), other, liv, KO - timedelta(days=7), (1, 1))]
    assert pair(source_match(), liv, None, stored) == (liv, utd)
    assert pair(source_match(), None, utd, stored) == (liv, utd)
    assert pair(source_match(ft=None), liv, None, stored) == (liv, utd)  # upcoming fixture: no score to compare


def test_pairing_refuses_when_unsure() -> None:
    liv, utd = uuid4(), uuid4()
    stored = [StoredMatch(uuid4(), liv, utd, KO, (4, 0))]
    assert pair(source_match(), None, None, stored) is None                        # neither team known
    assert pair(source_match(ft=(1, 0)), liv, None, stored) is None                # score differs
    assert pair(source_match(), None, liv, stored) is None                         # known team on the other side
    far = [StoredMatch(uuid4(), liv, utd, KO + timedelta(days=3), (4, 0))]
    assert pair(source_match(), liv, None, far) is None                            # not the same days
    two = [*stored, StoredMatch(uuid4(), liv, uuid4(), KO + timedelta(hours=20), None)]
    assert pair(source_match(), liv, None, two) is None                            # ambiguous
