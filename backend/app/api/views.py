"""What the frontend screens show (docs/API.md): sessions, the session report, fixtures, one match's analysis.

The published report of a session IS its frozen record (paper.predictions, live params version): the owner bets
on exactly what the forward test judges. Before the session's cut-off (as_of) the same shape is computed now as a
PREVIEW (not stored, may change). The full analysis of a match is recomputed at the stored as_of with the same
code (deterministic: the replay check proves it) and compared with the frozen row.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import Connection

from app.analysis.builder import AnalysisContext
from app.analysis.session import evaluate_session
from app.domain.enums import DataStatus, Decision, FixtureStatus
from app.domain.params import Params
from app.domain.registry import Registry
from app.domain.sessions import SessionCalendar, SessionId
from app.paper.bet import paper_bet
from app.paper.freeze import Provenance, checks_json, prediction_row
from app.paper.odds import SEALED_MARKETS, seen_odds
from app.settlement.settle import result_15
from app.storage.point_in_time import PointInTimeReader
from app.storage.repositories import frontend as fe_repo
from app.storage.repositories import paper as paper_repo

MAX_RANGE_DAYS = 31
LINEUP_NOT_CHECKED = "NOT_CHECKED"  # the lineup re-check (ARCHITECTURE.md 12.7) is not built yet


# --- JSON ------------------------------------------------------------------------------------------------------


def jsonable(obj: Any) -> Any:
    """Dataclasses, enums, times, ids, mappings and numbers as plain JSON values (NaN / infinity -> null)."""
    if obj is None or isinstance(obj, bool | str | int):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime | date | time):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Mapping):
        return {str(jsonable(k)): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple | set | frozenset):
        return [jsonable(x) for x in obj]
    return str(obj)


# --- fixtures --------------------------------------------------------------------------------------------------


def result_of(f: Mapping[str, Any]) -> dict[str, Any] | None:
    """The 15' result of a fixture row as the settlement reads it (None while unknown)."""
    h15 = (f["h15_home"], f["h15_away"]) if f["h15_home"] is not None else None
    try:
        r = result_15(FixtureStatus(f["status"]), DataStatus(f["data_status"]), h15, f["goal_before_15"])
    except ValueError:
        return None
    if r.under05 is None and r.draw is None:
        return None
    return {"under05": r.under05, "draw": r.draw}


def fixture_json(f: Mapping[str, Any], cal: SessionCalendar) -> dict[str, Any]:
    out = jsonable(dict(f))
    out["session_id"] = str(cal.session_of(f["kickoff_utc"]))
    out["result_15"] = result_of(f)
    return dict(out)


# --- sessions --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionInfo:
    sid: SessionId
    fixtures: list[dict[str, Any]]  # every fixture of the report competitions in the session window
    as_of: datetime | None  # the cut-off (None: no fixtures)
    ready_by: datetime | None
    report_state: str  # NO_FIXTURES / PLANNED / AWAITING_FREEZE / PUBLISHED / MISSED
    phase: str  # UPCOMING / IN_PROGRESS / ENDED
    frozen: int
    listed: int
    paper_bets: int


_ANALYSED = {s.value for s in paper_repo.IN_SESSION}


def _info(cal: SessionCalendar, sid: SessionId, fixtures: list[dict[str, Any]], counts: Mapping[str, Any] | None,
          now: datetime) -> SessionInfo:
    times = cal.times(sid)
    eligible = [f for f in fixtures if f["status"] in _ANALYSED]
    frozen = int(counts["frozen"]) if counts else 0
    if not eligible:
        return SessionInfo(sid, fixtures, None, None, "NO_FIXTURES", "ENDED" if now >= times.end_utc else "UPCOMING",
                           frozen, 0, 0)
    first = eligible[0]["kickoff_utc"]
    as_of, ready_by = cal.run_start(sid, first), cal.ready_by(sid, first)
    if counts and counts.get("as_of") is not None:
        as_of = counts["as_of"]
    if frozen:
        state = "PUBLISHED"
    elif now < as_of:
        state = "PLANNED"
    elif now < times.end_utc:
        state = "AWAITING_FREEZE"
    else:
        state = "MISSED"
    phase = "UPCOMING" if now < first else "IN_PROGRESS" if now < times.end_utc else "ENDED"
    return SessionInfo(sid, fixtures, as_of, ready_by, state, phase, frozen,
                       int(counts["listed"]) if counts else 0, int(counts["paper_bets"]) if counts else 0)


def session_infos(conn: Connection, params: Params, competitions: Sequence[str], first: date, last: date,
                  now: datetime) -> list[SessionInfo]:
    if last < first or (last - first).days >= MAX_RANGE_DAYS:
        raise ValueError(f"the range must be 1 .. {MAX_RANGE_DAYS} days, last date not before the first")
    cal = SessionCalendar(params)
    sids = cal.between(first, last)
    rows = fe_repo.fixtures_between(conn, cal.times(sids[0]).start_utc, cal.times(sids[-1]).end_utc, competitions)
    by_sid: dict[SessionId, list[dict[str, Any]]] = {s: [] for s in sids}
    for r in rows:
        by_sid.setdefault(cal.session_of(r["kickoff_utc"]), []).append(r)
    counts = fe_repo.frozen_counts(conn, [str(s) for s in sids], params.params_version)
    return [_info(cal, s, by_sid[s], counts.get(str(s)), now) for s in sids]


def session_info(conn: Connection, params: Params, competitions: Sequence[str], sid: SessionId,
                 now: datetime) -> SessionInfo:
    cal = SessionCalendar(params)
    times = cal.times(sid)
    rows = fe_repo.fixtures_between(conn, times.start_utc, times.end_utc, competitions)
    counts = fe_repo.frozen_counts(conn, [str(sid)], params.params_version)
    return _info(cal, sid, rows, counts.get(str(sid)), now)


def info_json(i: SessionInfo, cal: SessionCalendar) -> dict[str, Any]:
    t = cal.times(i.sid)
    return {"id": str(i.sid), "date": i.sid.day.isoformat(), "kind": i.sid.kind.value,
            "start_utc": jsonable(t.start_utc), "end_utc": jsonable(t.end_utc),
            "deadline_utc": jsonable(t.deadline_utc), "ready_by_utc": jsonable(i.ready_by),
            "as_of_utc": jsonable(i.as_of),
            "first_kickoff_utc": jsonable(next((f["kickoff_utc"] for f in i.fixtures
                                                if f["status"] in _ANALYSED), None)),
            "report_state": i.report_state, "phase": i.phase, "fixtures": len(i.fixtures),
            "frozen": i.frozen, "listed": i.listed, "paper_bets": i.paper_bets}


def _entry(p: Mapping[str, Any], f: Mapping[str, Any] | None, cal: SessionCalendar) -> dict[str, Any]:
    keep = ("decision", "grade", "failed_checks", "error", "p_under05", "p_draw", "p_model_a", "p_model_b2",
            "p_market", "p_market_draw", "p_league_00", "p_league_draw", "min_odds_under05", "min_odds_draw",
            "fair_under05", "fair_draw", "model_spread_pts", "complete_sample", "checks", "odds_seen",
            "best_under05_odds", "best_under05_book", "best_draw_odds", "best_draw_book", "params_version",
            "formula_version", "as_of", "recorded_at", "chain_pos")
    out: dict[str, Any] = {k: jsonable(p.get(k)) for k in keep if k in p}
    out["match_id"] = jsonable(p["match_id"])
    out["paper_bet"] = ({"market": p["bet_market"], "odds": p["bet_odds"], "bookmaker": p["bet_book"],
                         "ev": p["bet_ev"], "stake": p["bet_stake"]} if p.get("bet_market") else None)
    out["lineup_status"] = LINEUP_NOT_CHECKED
    out["fixture"] = fixture_json(f, cal) if f is not None else None
    return out


@dataclass(frozen=True)
class SessionEntries:
    info: SessionInfo
    source: str  # FROZEN / PREVIEW / NONE
    as_of: datetime | None
    entries: list[dict[str, Any]]  # one per analysed fixture (frozen, or computed now for a preview)


def session_entries(conn: Connection, params: Params, registry: Registry, competitions: Sequence[str],
                    sid: SessionId, now: datetime, *, preview: bool) -> SessionEntries:
    cal = SessionCalendar(params)
    info = session_info(conn, params, competitions, sid, now)
    fixtures = {f["match_id"]: f for f in info.fixtures}
    if info.frozen:
        rows = paper_repo.frozen(conn, str(sid), params.params_version)
        entries = [_entry(asdict(r), fixtures.get(r.match_id), cal) for r in rows]
        entries.sort(key=lambda e: (e["fixture"] or {}).get("kickoff_utc") or "")
        return SessionEntries(info, "FROZEN", info.as_of, entries)
    eligible = [f for f in info.fixtures if f["status"] in _ANALYSED]
    if not preview or not eligible or info.report_state not in ("PLANNED", "AWAITING_FREEZE"):
        return SessionEntries(info, "NONE", info.as_of, [])
    verdicts = evaluate_session(conn, params, registry, now, [f["match_id"] for f in eligible])
    reader = PointInTimeReader(conn, now)
    entries = []
    for f, sv in zip(eligible, verdicts, strict=True):
        odds = reader.odds(f["match_id"], SEALED_MARKETS)
        sf = paper_repo.SessionFixture(f["match_id"], f["competition_code"], f["kickoff_utc"],
                                       FixtureStatus(f["status"]))
        bet = paper_bet(sv.analysis, sv.decision, odds, params, 1.0)
        row = prediction_row(sv, sf, sid, now, params, Provenance(), seen_odds(odds), bet)
        entries.append(_entry(asdict(row), f, cal))
    return SessionEntries(info, "PREVIEW", now, entries)


def report_json(se: SessionEntries, cal: SessionCalendar, *, listed_only: bool) -> dict[str, Any]:
    entries = [e for e in se.entries if e["decision"] == Decision.QUALIFIED.value] if listed_only else se.entries
    out = {"session": info_json(se.info, cal), "source": se.source, "as_of_utc": jsonable(se.as_of),
           "entries": entries}
    if listed_only:
        by_decision: dict[str, int] = {}
        for e in se.entries:
            by_decision[e["decision"]] = by_decision.get(e["decision"], 0) + 1
        out["decisions"] = by_decision
        out["how_to_bet"] = ("Bet a listed match only if the bookmaker's odds are at or above the minimum odds. "
                             "Choose one market per match: the one where offered odds / minimum odds is higher. "
                             "Never bet both.")
    else:
        analysed = {e["match_id"] for e in se.entries}
        out["not_analysed"] = [fixture_json(f, cal) for f in se.info.fixtures if str(f["match_id"]) not in analysed]
    return out


# --- one match -------------------------------------------------------------------------------------------------


def match_analysis(conn: Connection, params: Params, registry: Registry, match_id: UUID, session: str | None,
                   now: datetime) -> dict[str, Any]:
    """The full analysis at the frozen as_of of the fixture's session (or now, as a preview)."""
    cal = SessionCalendar(params)
    f = fe_repo.match(conn, match_id)
    if f is None:
        raise LookupError(f"unknown match {match_id}")
    sid = str(cal.session_of(f["kickoff_utc"])) if session is None else session
    frozen = [r for r in paper_repo.frozen(conn, sid, params.params_version) if r.match_id == match_id]
    as_of, mode = (frozen[0].as_of, "FROZEN") if frozen else (now, "PREVIEW")
    analysis, verdict = AnalysisContext(conn, params, registry, as_of).evaluate(match_id)
    out: dict[str, Any] = {"match_id": str(match_id), "session_id": sid, "mode": mode, "as_of_utc": jsonable(as_of),
                           "params_version": params.params_version, "formula_version": params.formula_version,
                           "fixture": fixture_json(f, cal), "verdict": jsonable(verdict),
                           "checks": list(checks_json(verdict)), "analysis": jsonable(analysis)}
    if frozen:
        r = frozen[0]
        same = all(_close(a, b) for a, b in ((r.p_under05, verdict.p_under05), (r.p_draw, verdict.p_draw),
                                             (r.min_odds_under05, verdict.min_odds_under05),
                                             (r.min_odds_draw, verdict.min_odds_draw)))
        out["frozen"] = _entry(asdict(r), f, cal)
        out["matches_frozen"] = same  # False: data known before as_of arrived or changed after the freeze
    return out


def _close(a: float | None, b: float | None) -> bool:
    return (a is None and b is None) or (a is not None and b is not None and abs(a - b) <= 1e-9)


def day_window(cal: SessionCalendar, day: date) -> tuple[datetime, datetime]:
    """UTC bounds of one local (EAT) calendar day."""
    start = datetime.combine(day, time(0), tzinfo=cal.tz)
    return start, start + timedelta(days=1)
