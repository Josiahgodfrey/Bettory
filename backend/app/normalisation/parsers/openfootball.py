"""openfootball football.json parser: {season}/{key}.json and {season}/{key}-full.json. Pure.

Phase 0 rules: minutes are strings ("90+4"); own goals listed under the benefiting team;
a plain list score [h, a] is a full-time score (seen only for 0-0, so HT 0-0 is implied only then);
`time` is local kick-off time in the competition's timezone.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any

from pydantic import ValidationError

from app.domain.enums import FixtureStatus, Side, SourceCode
from app.domain.models import Score, SourceGoal, SourceMatch
from app.domain.registry import CompetitionDef
from app.normalisation.parsers import ParseError, ParseResult

SOURCE = SourceCode.OPENFOOTBALL
# "20:00" (local, competition timezone) or "15:00 UTC+1" (explicit offset, used by -full files)
_TIME = re.compile(r"^(\d{2}):(\d{2})(?:\s*UTC([+-]\d{1,2}))?$")


def file_key(filename: str) -> tuple[str, bool]:
    """'en.1-full.json' -> ('en.1', True); 'en.1.json' -> ('en.1', False)."""
    stem = filename.removesuffix(".json")
    return (stem.removesuffix("-full"), stem.endswith("-full"))


def _pair(value: Any) -> Score | None:
    if isinstance(value, list) and len(value) == 2 and all(isinstance(v, int) for v in value):
        return Score(home=value[0], away=value[1])
    return None


def _scores(score: Any) -> tuple[Score | None, Score | None]:
    if isinstance(score, dict):
        return _pair(score.get("ft")), _pair(score.get("ht"))
    ft = _pair(score)
    if ft is not None:
        return ft, (Score(home=0, away=0) if ft.total == 0 else None)
    return None, None


def _kickoff(day: date, text: str, comp: CompetitionDef) -> datetime | None:
    t = _TIME.match(text.strip())
    if not t:
        return None
    local = time(int(t.group(1)), int(t.group(2)))
    tz = timezone(timedelta(hours=int(t.group(3)))) if t.group(3) else comp.tz
    return datetime.combine(day, local, tzinfo=tz).astimezone(UTC)


def _goals(match: dict[str, Any], full_file: bool, ft: Score) -> tuple[tuple[SourceGoal, ...], bool]:
    if "goals1" not in match and "goals2" not in match:
        # In -full files a 0-0 match is written without goal lists: the (empty) list is complete.
        return (), full_file and ft.total == 0
    goals: list[SourceGoal] = []
    for key, side in (("goals1", Side.HOME), ("goals2", Side.AWAY)):
        for goal in match.get(key) or []:
            goals.append(SourceGoal.from_label(
                side, str(goal["minute"]),
                own_goal=bool(goal.get("owngoal")), penalty=bool(goal.get("penalty")), player=goal.get("name"),
            ))
    return tuple(goals), True


def parse_season_file(raw: bytes, comp: CompetitionDef, season_label: str, full_file: bool) -> ParseResult:
    data = json.loads(raw)
    matches = data.get("matches")
    if matches is None:
        matches = [m for r in data.get("rounds", []) for m in r.get("matches", [])]
    if not isinstance(matches, list):
        raise ParseError("openfootball payload has no matches")

    result = ParseResult()
    for m in matches:
        day = date.fromisoformat(m["date"])
        kickoff = _kickoff(day, str(m.get("time", "")), comp)
        ft, ht = _scores(m.get("score"))
        status = FixtureStatus.FINISHED if ft is not None else FixtureStatus.SCHEDULED
        try:
            goals, has_minutes = _goals(m, full_file, ft) if ft is not None else ((), False)
            result.matches.append(SourceMatch(
                source=SOURCE,
                provider_match_key=f"{m['date']}|{m['team1']}|{m['team2']}",
                competition_code=comp.code,
                season_label=season_label,
                kickoff_date=kickoff.date() if kickoff else day,
                kickoff_utc=kickoff,
                home_name=m["team1"],
                away_name=m["team2"],
                status=status,
                ft=ft,
                ht=ht,
                goals=goals,
                has_goal_minutes=has_minutes,
                round=m.get("round"),
            ))
        except (ValidationError, ValueError) as exc:
            reason = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
            result.skipped[f"invalid_match:{reason}"] += 1
    return result
