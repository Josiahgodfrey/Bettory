"""Sessions (ARCHITECTURE.md 12.2-12.3). Pure: no I/O, all times from the params file.

Every kick-off belongs to exactly one session, by its local time in `sessions.timezone` (EAT):

    NIGHT of date D   D 01:00 - D 12:59           report deadline D 01:00
    NOON  of date D   D 13:00 - D+1 00:59         report deadline D 13:00

    ready_by  = min(deadline, first kick-off - lead_time_hours)
    run_start = ready_by - run_buffer_minutes          (the moment the session is analysed: its as_of)

Session id format: "2026-11-15-NIGHT". The live planner and the backtest use the same calendar.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from app.domain.params import Params


class SessionKind(StrEnum):
    NIGHT = "NIGHT"
    NOON = "NOON"


@dataclass(frozen=True, order=True)
class SessionId:
    day: date
    kind: SessionKind

    def __str__(self) -> str:
        return f"{self.day.isoformat()}-{self.kind.value}"

    @classmethod
    def parse(cls, text: str) -> SessionId:
        day, _, kind = text.rpartition("-")
        return cls(date.fromisoformat(day), SessionKind(kind))


@dataclass(frozen=True)
class SessionTimes:
    id: SessionId
    start_utc: datetime  # first kick-off minute covered (inclusive)
    end_utc: datetime  # start of the next session (exclusive)
    deadline_utc: datetime  # the report must be ready before this


def _clock(text: str) -> time:
    h, m = (int(x) for x in text.split(":"))
    return time(h, m)


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _utc(dt: datetime) -> datetime:
    if dt.utcoffset() is None:
        raise ValueError(f"timezone-aware datetime required, got {dt!r}")
    return dt.astimezone(UTC)


class SessionCalendar:
    """The two daily sessions as configured. Raises if the windows do not cover the day exactly once."""

    def __init__(self, params: Params) -> None:
        s = params.sessions
        self.tz = ZoneInfo(s.timezone)
        self.lead = timedelta(hours=s.lead_time_hours)
        self.buffer = timedelta(minutes=s.run_buffer_minutes)
        self._night, self._noon = _clock(s.night.start), _clock(s.noon.start)
        if not _minutes(self._night) < _minutes(self._noon):
            raise ValueError("sessions: the night session must start earlier in the day than the noon session")
        for name, w, nxt in (("night", s.night, self._noon), ("noon", s.noon, self._night)):
            if (_minutes(_clock(w.end)) + 1) % 1440 != _minutes(nxt):
                raise ValueError(f"sessions.{name}: end {w.end} must be one minute before the next session start")
        self._deadline = {SessionKind.NIGHT: _clock(s.night.deadline), SessionKind.NOON: _clock(s.noon.deadline)}

    def _local(self, day: date, t: time) -> datetime:
        return datetime.combine(day, t, tzinfo=self.tz)

    def session_of(self, kickoff: datetime) -> SessionId:
        local = _utc(kickoff).astimezone(self.tz)
        t = local.time()
        if t < self._night:
            return SessionId(local.date() - timedelta(days=1), SessionKind.NOON)
        if t < self._noon:
            return SessionId(local.date(), SessionKind.NIGHT)
        return SessionId(local.date(), SessionKind.NOON)

    def times(self, sid: SessionId) -> SessionTimes:
        if sid.kind is SessionKind.NIGHT:
            start, end = self._local(sid.day, self._night), self._local(sid.day, self._noon)
        else:
            start, end = self._local(sid.day, self._noon), self._local(sid.day + timedelta(days=1), self._night)
        deadline = self._local(sid.day, self._deadline[sid.kind])
        if deadline > start:  # the report is due before the session opens: the previous day's clock time
            deadline -= timedelta(days=1)
        return SessionTimes(sid, _utc(start), _utc(end), _utc(deadline))

    def ready_by(self, sid: SessionId, first_kickoff: datetime) -> datetime:
        times = self.times(sid)
        first = _utc(first_kickoff)
        if not times.start_utc <= first < times.end_utc:
            raise ValueError(f"first kick-off {first} is not in session {sid}")
        return min(times.deadline_utc, first - self.lead)

    def run_start(self, sid: SessionId, first_kickoff: datetime) -> datetime:
        return self.ready_by(sid, first_kickoff) - self.buffer

    def between(self, first: date, last: date) -> list[SessionId]:
        """All sessions of the dates first..last (inclusive), in time order."""
        if last < first:
            raise ValueError("last date before first date")
        days = (first + timedelta(days=i) for i in range((last - first).days + 1))
        return [SessionId(d, k) for d in days for k in (SessionKind.NIGHT, SessionKind.NOON)]

    def group(self, kickoffs: Iterable[tuple[str, datetime]]) -> dict[SessionId, list[tuple[str, datetime]]]:
        """(key, kick-off) pairs grouped by session, sessions in time order, each sorted by kick-off."""
        out: dict[SessionId, list[tuple[str, datetime]]] = {}
        for key, ko in kickoffs:
            out.setdefault(self.session_of(ko), []).append((key, ko))
        return {sid: sorted(out[sid], key=lambda kv: (kv[1], kv[0])) for sid in sorted(out)}
