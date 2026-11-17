"""API-Football v3 parsers: /fixtures (by date, by id with events) and /odds. Pure."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.domain.enums import FixtureStatus, MarketCode, Side, SnapshotType, SourceCode
from app.domain.models import LineupPlayer, Score, SourceGoal, SourceLineup, SourceMatch, SourceOdds
from app.domain.registry import Registry, season_label
from app.normalisation.parsers import ParseError, ParseResult

SOURCE = SourceCode.API_FOOTBALL

STATUS = {
    "TBD": FixtureStatus.SCHEDULED, "NS": FixtureStatus.SCHEDULED,
    "1H": FixtureStatus.LIVE, "HT": FixtureStatus.LIVE, "2H": FixtureStatus.LIVE, "ET": FixtureStatus.LIVE,
    "BT": FixtureStatus.LIVE, "P": FixtureStatus.LIVE, "LIVE": FixtureStatus.LIVE, "INT": FixtureStatus.LIVE,
    "SUSP": FixtureStatus.POSTPONED, "PST": FixtureStatus.POSTPONED,
    "FT": FixtureStatus.FINISHED, "AET": FixtureStatus.FINISHED, "PEN": FixtureStatus.FINISHED,
    "CANC": FixtureStatus.CANCELLED, "ABD": FixtureStatus.ABANDONED,
    "AWD": FixtureStatus.AWARDED, "WO": FixtureStatus.AWARDED,
}
EXTRA_TIME = {"AET", "PEN"}

# provider bet name -> (market, has a line in the value such as "Over 1.5")
BETS: dict[str, tuple[MarketCode, bool]] = {
    "Match Winner": (MarketCode.FT_1X2, False),
    "Goals Over/Under": (MarketCode.FT_TOTAL, True),
    "Goals Over/Under First Half": (MarketCode.HT_TOTAL, True),
    "1x2 - 15 minutes": (MarketCode.FIRST15_RESULT, False),
    "Goal in 1-15 minutes": (MarketCode.FIRST15_GOAL, False),
}
_LINE_VALUE = re.compile(r"^(Over|Under)\s+(\d+(?:\.\d+)?)$")
_PLAIN_VALUE = {"Home": "HOME", "Draw": "DRAW", "Away": "AWAY", "Yes": "YES", "No": "NO"}


def _body(raw: bytes) -> list[dict[str, Any]]:
    data = json.loads(raw)
    if data.get("errors"):
        raise ParseError(f"API-Football returned errors: {data['errors']}")
    response = data.get("response")
    if not isinstance(response, list):
        raise ParseError("API-Football payload has no response list")
    return response


def _score(part: dict[str, Any] | None) -> Score | None:
    if not part or part.get("home") is None or part.get("away") is None:
        return None
    return Score(home=part["home"], away=part["away"])


def _goals(item: dict[str, Any], status_short: str) -> tuple[tuple[SourceGoal, ...], bool]:
    """Returns (goals, has_goal_minutes). Regular time only (extra-time goals dropped)."""
    if "events" not in item:
        return (), False
    home_id = item["teams"]["home"]["id"]
    goals: list[SourceGoal] = []
    for ev in item["events"]:
        if ev.get("type") != "Goal" or ev.get("detail") == "Missed Penalty":
            continue
        elapsed = (ev.get("time") or {}).get("elapsed")
        if not isinstance(elapsed, int):
            return (), False
        if status_short in EXTRA_TIME and elapsed > 90:
            continue
        goals.append(SourceGoal(
            side=Side.HOME if ev["team"]["id"] == home_id else Side.AWAY,
            minute=elapsed,
            extra_minute=(ev.get("time") or {}).get("extra") or None,
            own_goal=ev.get("detail") == "Own Goal",
            penalty=ev.get("detail") == "Penalty",
            player=(ev.get("player") or {}).get("name"),
        ))
    return tuple(goals), True


def parse_fixtures(raw: bytes, registry: Registry) -> ParseResult:
    result = ParseResult()
    for item in _body(raw):
        comp = registry.by_provider(SOURCE, str(item["league"]["id"]))
        if comp is None:
            result.skipped["unregistered_competition"] += 1
            continue
        short = item["fixture"]["status"]["short"]
        status = STATUS.get(short)
        if status is None:
            result.skipped[f"unknown_status_{short}"] += 1
            continue
        kickoff = datetime.fromisoformat(item["fixture"]["date"]).astimezone(UTC)
        ft = _score(item["score"].get("fulltime")) or (
            _score(item.get("goals")) if status is FixtureStatus.FINISHED and short not in EXTRA_TIME else None)
        goals, has_minutes = _goals(item, short) if status is FixtureStatus.FINISHED else ((), False)
        try:
            result.matches.append(SourceMatch(
                source=SOURCE,
                provider_match_key=str(item["fixture"]["id"]),
                competition_code=comp.code,
                season_label=season_label(int(item["league"]["season"]), comp.season_format),
                kickoff_date=kickoff.date(),
                kickoff_utc=kickoff,
                home_name=item["teams"]["home"]["name"],
                away_name=item["teams"]["away"]["name"],
                home_key=str(item["teams"]["home"]["id"]),
                away_key=str(item["teams"]["away"]["id"]),
                status=status,
                ft=ft if status is FixtureStatus.FINISHED else None,
                ht=_score(item["score"].get("halftime")) if status is FixtureStatus.FINISHED else None,
                goals=goals,
                has_goal_minutes=has_minutes,
                round=item["league"].get("round"),
            ))
        except ValidationError as exc:
            result.skipped[f"invalid_match:{exc.errors()[0]['msg']}"] += 1
    return result


def parse_odds(raw: bytes, registry: Registry) -> ParseResult:
    result = ParseResult()
    for item in _body(raw):
        if registry.by_provider(SOURCE, str(item["league"]["id"])) is None:
            result.skipped["unregistered_competition"] += 1
            continue
        captured = datetime.fromisoformat(item["update"]).astimezone(UTC)
        for bookmaker in item["bookmakers"]:
            for bet in bookmaker["bets"]:
                spec = BETS.get(bet["name"])
                if spec is None:
                    continue
                market, has_line = spec
                for value in bet["values"]:
                    text = str(value["value"]).strip()
                    line: float | None = None
                    if has_line:
                        m = _LINE_VALUE.match(text)
                        if not m:
                            result.skipped["unparsed_selection"] += 1
                            continue
                        selection, line = m.group(1).upper(), float(m.group(2))
                    elif text in _PLAIN_VALUE:
                        selection = _PLAIN_VALUE[text]
                    else:
                        result.skipped["unparsed_selection"] += 1
                        continue
                    try:
                        result.odds.append(SourceOdds(
                            source=SOURCE, provider_match_key=str(item["fixture"]["id"]),
                            bookmaker=bookmaker["name"], market=market, provider_market_name=bet["name"],
                            selection=selection, line=line, odds=float(value["odd"]),
                            snapshot_type=SnapshotType.ANALYSIS, captured_at=captured,
                        ))
                    except (ValidationError, ValueError):
                        result.skipped["invalid_odds"] += 1
    return result


def parse_lineups(raw: bytes, fixture_key: str) -> ParseResult:
    """/fixtures/lineups?fixture=<id>: one item per team (an empty response: not announced yet)."""
    result = ParseResult()
    for item in _body(raw):
        players = []
        for part, starter in (("startXI", True), ("substitutes", False)):
            for entry in item.get(part) or []:
                pl = entry.get("player") or {}
                if not pl.get("name"):
                    result.skipped["lineup_player_without_name"] += 1
                    continue
                players.append(LineupPlayer(name=pl["name"], number=pl.get("number"), position=pl.get("pos"),
                                            grid=pl.get("grid"), starter=starter))
        team = item.get("team") or {}
        try:
            result.lineups.append(SourceLineup(source=SOURCE, provider_match_key=fixture_key,
                                               team_key=str(team["id"]), team_name=team["name"],
                                               formation=item.get("formation"), players=tuple(players)))
        except (KeyError, ValidationError):
            result.skipped["invalid_lineup"] += 1
    return result
