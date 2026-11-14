"""football-data.org v4 parser: /competitions/{code}/matches?season=YYYY. Pure. No goal minutes (free tier)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.domain.enums import FixtureStatus, SourceCode
from app.domain.models import Score, SourceMatch
from app.domain.registry import Registry, season_label
from app.normalisation.parsers import ParseError, ParseResult

SOURCE = SourceCode.FOOTBALL_DATA_ORG

STATUS = {
    "SCHEDULED": FixtureStatus.SCHEDULED, "TIMED": FixtureStatus.SCHEDULED,
    "IN_PLAY": FixtureStatus.LIVE, "PAUSED": FixtureStatus.LIVE, "LIVE": FixtureStatus.LIVE,
    "FINISHED": FixtureStatus.FINISHED,
    "POSTPONED": FixtureStatus.POSTPONED, "SUSPENDED": FixtureStatus.POSTPONED,
    "CANCELLED": FixtureStatus.CANCELLED, "AWARDED": FixtureStatus.AWARDED,
}


def _score(part: dict[str, Any] | None) -> Score | None:
    if not part or part.get("home") is None or part.get("away") is None:
        return None
    return Score(home=part["home"], away=part["away"])


def parse_matches(raw: bytes, registry: Registry) -> ParseResult:
    data = json.loads(raw)
    if "matches" not in data or "competition" not in data:
        raise ParseError(f"football-data.org payload without matches: {data.get('message', '')}")
    comp = registry.by_provider(SOURCE, data["competition"]["code"])
    result = ParseResult()
    if comp is None:
        result.skipped["unregistered_competition"] += len(data["matches"])
        return result
    season_year = (data.get("filters") or {}).get("season")
    if season_year is None:
        raise ParseError("football-data.org payload without filters.season")
    label = season_label(int(season_year), comp.season_format)

    for m in data["matches"]:
        status = STATUS.get(m["status"])
        if status is None:
            result.skipped[f"unknown_status_{m['status']}"] += 1
            continue
        score = m.get("score") or {}
        ft = _score(score.get("fullTime"))
        if status is FixtureStatus.FINISHED and score.get("duration", "REGULAR") != "REGULAR":
            ft = _score(score.get("regularTime"))
            if ft is None:
                result.skipped["extra_time_without_regular_score"] += 1
                continue
        kickoff = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).astimezone(UTC)
        finished = status is FixtureStatus.FINISHED
        try:
            result.matches.append(SourceMatch(
                source=SOURCE,
                provider_match_key=str(m["id"]),
                competition_code=comp.code,
                season_label=label,
                kickoff_date=kickoff.date(),
                kickoff_utc=kickoff,
                home_name=m["homeTeam"]["name"],
                away_name=m["awayTeam"]["name"],
                home_key=str(m["homeTeam"]["id"]),
                away_key=str(m["awayTeam"]["id"]),
                status=status,
                ft=ft if finished else None,
                ht=_score(score.get("halfTime")) if finished else None,
                has_goal_minutes=False,
                round=f"Matchday {m['matchday']}" if m.get("matchday") else m.get("stage"),
            ))
        except ValidationError as exc:
            result.skipped[f"invalid_match:{exc.errors()[0]['msg']}"] += 1
    return result
