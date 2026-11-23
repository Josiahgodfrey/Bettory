"""ESPN soccer feed parsers: scoreboard, team schedule, match summary (app/ingestion/espn.py). Pure.

Rules (checked on real payloads 2026-11-23, docs/ESPN.md):
- Goals come from the SUMMARY's `keyEvents` with `scoringPlay`; the scoreboard's goal list is not used (it was
  wrong once in 881 matches where the summary was right). The summary is complete only if every goal has a minute
  and a team of the match, and the goal count equals the full-time score - otherwise the match is stored with
  its scores but WITHOUT goal minutes (never a guessed timeline).
- Own goals: `team` is the benefiting team (as BETTORY stores them). Shoot-out kicks are not goals.
- Minutes: clock.displayValue "13'" / "45'+2'"; half-time score from the competitors' first `linescores` entry.
- Season: the payload's season year with the competition's season format (2025 -> "2025-26" or "2025").
- Matches decided after extra time (cups) are skipped: their full-time score is not the 90-minute score.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.domain.enums import FixtureStatus, Side, SourceCode
from app.domain.models import Score, SourceGoal, SourceMatch
from app.domain.registry import CompetitionDef, season_label
from app.normalisation.parsers import ParseError, ParseResult

SOURCE = SourceCode.ESPN
_CLOCK = re.compile(r"^(\d{1,3})'(?:\s*\+\s*(\d{1,2})')?$")
FINISHED_NAMES = {"STATUS_FULL_TIME", "STATUS_FINAL"}
EXTRA_TIME_NAMES = {"STATUS_FINAL_AET", "STATUS_FINAL_PEN", "STATUS_FINAL_ET"}
STATUS_BY_NAME = {
    "STATUS_SCHEDULED": FixtureStatus.SCHEDULED,
    "STATUS_POSTPONED": FixtureStatus.POSTPONED,
    "STATUS_DELAYED": FixtureStatus.POSTPONED,
    "STATUS_SUSPENDED": FixtureStatus.POSTPONED,
    "STATUS_CANCELED": FixtureStatus.CANCELLED,
    "STATUS_CANCELLED": FixtureStatus.CANCELLED,
    "STATUS_ABANDONED": FixtureStatus.ABANDONED,
    "STATUS_FORFEIT": FixtureStatus.AWARDED,
}


@dataclass(frozen=True)
class ScheduleEvent:
    """One match of a team schedule: enough to decide whether its summary is needed."""

    event_id: str
    kickoff_utc: datetime
    home_id: str
    away_id: str
    completed: bool


def _json(raw: bytes) -> dict[str, Any]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ParseError("ESPN payload is not a JSON object")
    return data


def _kickoff(value: str) -> datetime:
    """'2025-08-24T15:30Z' / '2025-08-24T15:30:00Z' -> aware UTC datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _status(st: dict[str, Any]) -> FixtureStatus | None:
    """None: skip (extra time, or a state BETTORY does not store)."""
    t = st.get("type") or {}
    name = str(t.get("name") or "")
    if name in EXTRA_TIME_NAMES:
        return None
    if name in FINISHED_NAMES:
        return FixtureStatus.FINISHED
    if name in STATUS_BY_NAME:
        return STATUS_BY_NAME[name]
    state = t.get("state")
    if state == "in":
        return FixtureStatus.LIVE
    if state == "pre":
        return FixtureStatus.SCHEDULED
    return None


def _int(value: Any) -> int | None:
    if isinstance(value, dict):
        value = value.get("value", value.get("displayValue"))
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _sides(competitors: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    sides = {c.get("homeAway"): c for c in competitors}
    if set(sides) != {"home", "away"}:
        raise ParseError("ESPN match without one home and one away competitor")
    return sides  # type: ignore[return-value]


def _team_id(c: dict[str, Any]) -> str:
    return str((c.get("team") or {}).get("id") or c.get("id"))


def _scores(sides: dict[str, dict[str, Any]]) -> tuple[Score | None, Score | None]:
    h, a = _int(sides["home"].get("score")), _int(sides["away"].get("score"))
    ft = Score(home=h, away=a) if h is not None and a is not None else None
    hl, al = sides["home"].get("linescores") or [], sides["away"].get("linescores") or []
    ht = None
    if hl and al:
        hh = _int(hl[0].get("displayValue", hl[0].get("value")))
        ah = _int(al[0].get("displayValue", al[0].get("value")))
        if hh is not None and ah is not None:
            ht = Score(home=hh, away=ah)
    return ft, ht


def minute_label(display: str) -> str | None:
    """"45'+2'" -> "45+2"; "13'" -> "13"; anything else -> None."""
    m = _CLOCK.match((display or "").strip())
    if not m:
        return None
    return m.group(1) + (f"+{m.group(2)}" if m.group(2) else "")


def _summary_goals(events: list[dict[str, Any]], home_id: str, away_id: str) -> tuple[SourceGoal, ...] | None:
    """The goal list, or None when any goal lacks a usable minute or team."""
    goals: list[SourceGoal] = []
    for e in events:
        if not e.get("scoringPlay") or e.get("shootout"):
            continue
        team = str((e.get("team") or {}).get("id"))
        side = Side.HOME if team == home_id else Side.AWAY if team == away_id else None
        label = minute_label(str((e.get("clock") or {}).get("displayValue") or ""))
        if side is None or label is None:
            return None
        etype = e.get("type") or {}
        text = f"{etype.get('type', '')} {etype.get('text', '')}".lower()
        participants = e.get("participants") or []
        player = ((participants[0].get("athlete") or {}).get("displayName")) if participants else None
        try:
            goals.append(SourceGoal.from_label(side, label, own_goal="own" in text, penalty="penalty" in text,
                                               player=player))
        except (ValidationError, ValueError):
            return None
    return tuple(sorted(goals, key=lambda g: g.sort_key))


def _season(data: dict[str, Any], event: dict[str, Any] | None, comp: CompetitionDef) -> str:
    for holder in (event or {}, data.get("header") or {}, *(data.get("leagues") or [])):
        year = _int((holder.get("season") or {}).get("year"))
        if year:
            return season_label(year, comp.season_format)
    raise ParseError("ESPN payload without a season year")


def _match(comp: CompetitionDef, season: str, event_id: str, competition: dict[str, Any], date_value: str,
           status: FixtureStatus, goals: tuple[SourceGoal, ...] | None) -> SourceMatch:
    sides = _sides(competition.get("competitors") or [])
    kickoff = _kickoff(date_value)
    ft, ht = _scores(sides) if status is FixtureStatus.FINISHED else (None, None)
    complete = (status is FixtureStatus.FINISHED and ft is not None and goals is not None
                and len(goals) == ft.total)
    return SourceMatch(
        source=SOURCE, provider_match_key=event_id, competition_code=comp.code, season_label=season,
        kickoff_date=kickoff.date(), kickoff_utc=kickoff,
        home_name=str((sides["home"].get("team") or {}).get("displayName") or ""),
        away_name=str((sides["away"].get("team") or {}).get("displayName") or ""),
        home_key=_team_id(sides["home"]), away_key=_team_id(sides["away"]),
        status=status, ft=ft, ht=ht, goals=goals if complete and goals else (),
        has_goal_minutes=complete,
    )


def parse_summary(raw: bytes, comp: CompetitionDef) -> ParseResult:
    """One match with its goal minutes (when complete)."""
    data = _json(raw)
    header = data.get("header") or {}
    comps = header.get("competitions") or []
    if not comps or not header.get("id"):
        raise ParseError("ESPN summary without header.competitions")
    c = comps[0]
    result = ParseResult()
    status = _status(c.get("status") or {})
    if status is None:
        result.skipped["status_not_stored"] += 1
        return result
    try:
        sides = _sides(c.get("competitors") or [])
        goals = None
        if status is FixtureStatus.FINISHED and isinstance(data.get("keyEvents"), list):
            goals = _summary_goals(data["keyEvents"], _team_id(sides["home"]), _team_id(sides["away"]))
        m = _match(comp, _season(data, None, comp), str(header["id"]), c, str(c.get("date")), status, goals)
    except (ValidationError, ValueError) as exc:
        reason = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
        result.skipped[f"invalid_match:{reason}"] += 1
        return result
    if status is FixtureStatus.FINISHED and not m.has_goal_minutes:
        result.skipped["goals_incomplete"] += 1  # stored, but without goal minutes
    result.matches.append(m)
    return result


def parse_scoreboard(raw: bytes, comp: CompetitionDef) -> ParseResult:
    """A league's matches of one day: fixtures, kick-offs, scores - never goal minutes (see module doc)."""
    data = _json(raw)
    events = data.get("events")
    if not isinstance(events, list):
        raise ParseError("ESPN scoreboard without events")
    result = ParseResult()
    for e in events:
        c = (e.get("competitions") or [{}])[0]
        status = _status(c.get("status") or e.get("status") or {})
        if status is None:
            result.skipped["status_not_stored"] += 1
            continue
        try:
            result.matches.append(_match(comp, _season(data, e, comp), str(e["id"]), c, str(e.get("date")),
                                         status, None))
        except (ValidationError, ValueError, KeyError) as exc:
            reason = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
            result.skipped[f"invalid_match:{reason}"] += 1
    return result


def parse_schedule(raw: bytes) -> list[ScheduleEvent]:
    """A team's league matches (newest first). Matches without two teams or a date are left out."""
    data = _json(raw)
    out: list[ScheduleEvent] = []
    for e in data.get("events") or []:
        c = (e.get("competitions") or [{}])[0]
        try:
            sides = _sides(c.get("competitors") or [])
            st = (c.get("status") or {}).get("type") or {}
            out.append(ScheduleEvent(event_id=str(e["id"]), kickoff_utc=_kickoff(str(e.get("date") or c["date"])),
                                     home_id=_team_id(sides["home"]), away_id=_team_id(sides["away"]),
                                     completed=bool(st.get("completed")) and st.get("name") in FINISHED_NAMES))
        except (KeyError, ValueError):
            continue
    return sorted(out, key=lambda s: s.kickoff_utc, reverse=True)


def schedule_season_year(raw: bytes) -> int | None:
    """The season year of a team schedule: its events' own season (the payload's top-level season is ESPN's
    current season even when an older one was asked for)."""
    data = _json(raw)
    for e in data.get("events") or []:
        year = _int((e.get("season") or {}).get("year"))
        if year:
            return year
    return _int((data.get("season") or {}).get("year"))
