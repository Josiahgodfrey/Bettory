"""The texts of the Telegram messages (ARCHITECTURE.md 12.8). Pure. Summary and link only: never odds, never a
full report (the link opens the app, which requires login)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from zoneinfo import ZoneInfo


def _local(t: datetime, tz: ZoneInfo) -> str:
    return t.astimezone(tz).strftime("%H:%M")


def _session_name(session_id: str) -> str:
    day, _, kind = session_id.rpartition("-")
    d = date.fromisoformat(day)
    return f"{'Night' if kind == 'NIGHT' else 'Noon'} session {d.day} {d:%b}"


def report_ready(session_id: str, listed: int, first_kickoff: datetime, tz: ZoneInfo, app_url: str) -> str:
    if listed == 0:
        return f"{_session_name(session_id)} — 0 matches passed verification."
    icon = "🌙" if session_id.endswith("NIGHT") else "☀️"
    link = f"{app_url.rstrip('/')}/sessions/{session_id}"  # the Bets section of that session (frontend route)
    return (f"{icon} {_session_name(session_id)} — {listed} {'match' if listed == 1 else 'matches'} verified "
            f"(first kick-off {_local(first_kickoff, tz)} EAT). Open: {link}")


def no_fixtures(session_id: str) -> str:
    """A planned session without any fixture in the report leagues (ARCHITECTURE.md 12.3)."""
    icon = "🌙" if session_id.endswith("NIGHT") else "☀️"
    return f"{icon} {_session_name(session_id)} — no matches in the report leagues: nothing to analyse."


def withdrawn(home: str, away: str, kickoff: datetime, reasons: Sequence[str], tz: ZoneInfo) -> str:
    why = ", ".join(reasons[:4]) if reasons else "no longer passes the verification"
    return f"⚠️ Withdrawn: {home} vs {away} ({_local(kickoff, tz)} EAT) — reason: {why}. Do not bet it."


def failure(line: str) -> str:
    return f"❗ BETTORY recorder: {line[:300]}"


def login(at: datetime, tz: ZoneInfo) -> str:
    return f"🔐 New login to BETTORY at {at.astimezone(tz):%d %b %H:%M} EAT. Not you? Sign out all sessions."
