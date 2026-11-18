"""Session planning (ARCHITECTURE.md 12.4) and the timeline of a session's recorder steps.

    plan_time       when a session's fixtures are loaded: the Noon session at plan.noon_at on its own day, the Night
                    session at plan.night_at the evening before (EAT, config/paper.yaml)
    session_dates   the UTC dates a session covers (the fixtures listing is one request per UTC date)
    timeline        plan, capture, cut-off (as_of), report ready, deadline, first kick-off: what the recorder does
                    when, for one session (the Recorder screen and `paper-schedule`)

The plan itself is the recorder cycle's first step (app/paper/cycle.py): a session whose fixtures were never loaded
would otherwise never be captured or frozen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from app.domain.sessions import SessionCalendar, SessionId, SessionKind
from app.paper.config import PaperConfig


def plan_job(sid: SessionId) -> str:
    """Run name of a session's plan (its requests count in the recorder's daily bound: prefix paper_)."""
    return f"paper_plan:{sid}"


def plan_time(cal: SessionCalendar, cfg: PaperConfig, sid: SessionId) -> datetime:
    if sid.kind is SessionKind.NOON:
        local = datetime.combine(sid.day, cfg.plan.noon_time, cal.tz)
    else:
        local = datetime.combine(sid.day - timedelta(days=1), cfg.plan.night_time, cal.tz)
    return local.astimezone(UTC)


def session_dates(cal: SessionCalendar, sid: SessionId) -> list[date]:
    t = cal.times(sid)
    last = (t.end_utc - timedelta(microseconds=1)).astimezone(UTC).date()
    first = t.start_utc.astimezone(UTC).date()
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


@dataclass(frozen=True)
class Timeline:
    session_id: str
    plan_at: datetime
    deadline: datetime
    start: datetime
    end: datetime
    first_kickoff: datetime | None  # None: no fixture known (yet)
    capture_from: datetime | None
    as_of: datetime | None  # the data cut-off: the freeze seals the predictions then
    ready_by: datetime | None


def timeline(cal: SessionCalendar, cfg: PaperConfig, sid: SessionId, first_kickoff: datetime | None) -> Timeline:
    t = cal.times(sid)
    as_of = cal.run_start(sid, first_kickoff) if first_kickoff else None
    return Timeline(
        session_id=str(sid), plan_at=plan_time(cal, cfg, sid), deadline=t.deadline_utc, start=t.start_utc,
        end=t.end_utc, first_kickoff=first_kickoff,
        capture_from=as_of - cfg.capture.lead if as_of else None, as_of=as_of,
        ready_by=cal.ready_by(sid, first_kickoff) if first_kickoff else None)
