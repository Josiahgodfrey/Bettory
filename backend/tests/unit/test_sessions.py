"""Sessions: membership, deadlines and run times, checked against the tables in ARCHITECTURE.md 12.2-12.3."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.domain.sessions import SessionCalendar, SessionId, SessionKind
from tests.support import PARAMS, params

CAL = SessionCalendar(PARAMS)
EAT = ZoneInfo("Africa/Nairobi")
D = date(2026, 11, 15)


def eat(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=EAT)


@pytest.mark.parametrize(("local", "expected"), [
    (eat(D, 1, 0), SessionId(D, SessionKind.NIGHT)),
    (eat(D, 12, 59), SessionId(D, SessionKind.NIGHT)),
    (eat(D, 13, 0), SessionId(D, SessionKind.NOON)),
    (eat(D, 23, 30), SessionId(D, SessionKind.NOON)),
    (eat(D + timedelta(days=1), 0, 59), SessionId(D, SessionKind.NOON)),   # after midnight: still D's noon
    (eat(D + timedelta(days=1), 1, 0), SessionId(D + timedelta(days=1), SessionKind.NIGHT)),
])
def test_session_membership(local: datetime, expected: SessionId) -> None:
    assert CAL.session_of(local) == expected
    assert CAL.session_of(local.astimezone(UTC)) == expected                    # same instant in UTC


def test_session_windows_in_utc() -> None:
    night = CAL.times(SessionId(D, SessionKind.NIGHT))
    assert night.start_utc == datetime(2026, 11, 14, 22, tzinfo=UTC)            # D 01:00 EAT
    assert night.end_utc == datetime(2026, 11, 15, 10, tzinfo=UTC)
    noon = CAL.times(SessionId(D, SessionKind.NOON))
    assert (noon.start_utc, noon.end_utc) == (night.end_utc, datetime(2026, 11, 15, 22, tzinfo=UTC))
    assert night.deadline_utc == night.start_utc and noon.deadline_utc == noon.start_utc


@pytest.mark.parametrize(("kind", "first", "ready", "run"), [
    # ARCHITECTURE.md 12.3 tables (EAT)
    (SessionKind.NIGHT, eat(D, 1, 0), eat(D - timedelta(days=1), 23, 0), eat(D - timedelta(days=1), 22, 15)),
    (SessionKind.NIGHT, eat(D, 2, 30), eat(D, 0, 30), eat(D - timedelta(days=1), 23, 45)),
    (SessionKind.NIGHT, eat(D, 5, 0), eat(D, 1, 0), eat(D, 0, 15)),
    (SessionKind.NOON, eat(D, 13, 0), eat(D, 11, 0), eat(D, 10, 15)),
    (SessionKind.NOON, eat(D, 14, 30), eat(D, 12, 30), eat(D, 11, 45)),
    (SessionKind.NOON, eat(D, 21, 0), eat(D, 13, 0), eat(D, 12, 15)),
])
def test_ready_by_and_run_start(kind: SessionKind, first: datetime, ready: datetime, run: datetime) -> None:
    sid = SessionId(D, kind)
    assert CAL.ready_by(sid, first) == ready and CAL.run_start(sid, first) == run
    assert CAL.run_start(sid, first) < first                                     # always before every kick-off


def test_kickoff_outside_the_session_is_rejected() -> None:
    with pytest.raises(ValueError, match="not in session"):
        CAL.ready_by(SessionId(D, SessionKind.NIGHT), eat(D, 13, 0))
    with pytest.raises(ValueError, match="timezone-aware"):
        CAL.session_of(datetime(2026, 11, 15, 12))


def test_id_round_trip_grouping_and_range() -> None:
    sid = SessionId(D, SessionKind.NOON)
    assert str(sid) == "2026-11-15-NOON" and SessionId.parse(str(sid)) == sid
    groups = CAL.group([("b", eat(D, 15)), ("a", eat(D, 9)), ("c", eat(D, 14))])
    assert [str(s) for s in groups] == ["2026-11-15-NIGHT", "2026-11-15-NOON"]
    assert [k for k, _ in groups[sid]] == ["c", "b"]
    assert len(CAL.between(D, D + timedelta(days=2))) == 6


def test_windows_must_cover_the_day_exactly() -> None:
    bad = params(sessions__night={"start": "01:00", "end": "11:59", "deadline": "01:00"})
    with pytest.raises(ValueError, match="one minute before"):
        SessionCalendar(bad)
