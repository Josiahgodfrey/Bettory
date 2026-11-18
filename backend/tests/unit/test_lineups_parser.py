"""API-Football /fixtures/lineups: one item per team; an empty response means not announced yet."""

from __future__ import annotations

import json

import pytest

from app.normalisation.parsers import ParseError
from app.normalisation.parsers.api_football import parse_lineups


def body(items: list[dict[str, object]]) -> bytes:
    return json.dumps({"errors": [], "results": len(items), "response": items}).encode()


def test_two_teams_with_starters_and_substitutes() -> None:
    team = {"team": {"id": 33, "name": "Home FC"}, "formation": "4-2-3-1",
            "startXI": [{"player": {"id": 1, "name": "Keeper", "number": 1, "pos": "G", "grid": "1:1"}}],
            "substitutes": [{"player": {"id": 2, "name": "Bench", "number": 12, "pos": "M", "grid": None}},
                            {"player": {"id": 3, "name": None}}]}
    away = {**team, "team": {"id": 34, "name": "Away FC"}, "formation": None}
    r = parse_lineups(body([team, away]), "1001")
    assert [(lu.team_key, lu.formation, lu.provider_match_key) for lu in r.lineups] == [
        ("33", "4-2-3-1", "1001"), ("34", None, "1001")]
    assert [(p.name, p.starter, p.position) for p in r.lineups[0].players] == [("Keeper", True, "G"),
                                                                             ("Bench", False, "M")]
    assert r.skipped["lineup_player_without_name"] == 2


def test_not_announced_and_errors() -> None:
    assert parse_lineups(body([]), "1001").lineups == []
    with pytest.raises(ParseError):
        parse_lineups(json.dumps({"errors": {"plan": "x"}, "response": []}).encode(), "1001")
