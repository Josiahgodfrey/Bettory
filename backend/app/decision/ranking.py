"""Session ranking, the 50-match cap and the one-way re-check rule (ARCHITECTURE.md 12.5, 12.7, 8.3). Pure.

Ranking of QUALIFIED verdicts (only they can enter a report):
    1. grade A before B
    2. edge: verdicts with a checked bookmaker price (EV) first, ordered by EV; then the others by P(Under 0.5)
       (an EV measured against a real price is stronger evidence than a probability alone)
    3. smaller model spread (more agreement first)
    4. larger complete sample
    5. fixture id (deterministic order for equal keys)
The cap (sessions.max_matches_per_session) is a maximum, never a target: QUALIFIED verdicts beyond it become
QUALIFIED_OVER_CAP; nothing is ever added to reach it.

Re-check (lineups, ~60 min before kick-off): a listed match can only be withdrawn, never added.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from app.decision.layers import Verdict
from app.domain.enums import Decision, Grade
from app.domain.params import Params

_GRADE_ORDER = {Grade.A: 0, Grade.B: 1}


def _key(v: Verdict) -> tuple[int, int, float, float, int, str]:
    if v.grade not in _GRADE_ORDER or v.p_under05 is None:
        raise ValueError(f"verdict {v.fixture_id} is QUALIFIED without grade / probability")
    has_ev = v.ev is not None
    edge = v.ev if v.ev is not None else v.p_under05
    # one-model mode (params 1.6+) has no spread: treat it as perfect agreement
    spread = v.model_spread_pts if v.model_spread_pts is not None else 0.0
    return (_GRADE_ORDER[v.grade], 0 if has_ev else 1, -edge, spread, -(v.complete_sample or 0),
            v.fixture_id)


@dataclass(frozen=True)
class SessionReport:
    listed: tuple[Verdict, ...]  # QUALIFIED, ranked, at most the cap
    over_cap: tuple[Verdict, ...]  # QUALIFIED_OVER_CAP (stored, not shown in the report)
    not_qualified: tuple[Verdict, ...]  # SKIP / INSUFFICIENT_DATA / ERROR (full analysis view only)


def rank_and_cap(verdicts: Sequence[Verdict], params: Params) -> SessionReport:
    ids = [v.fixture_id for v in verdicts]
    if len(ids) != len(set(ids)):
        raise ValueError("a fixture appears twice in one session")
    qualified = sorted((v for v in verdicts if v.decision is Decision.QUALIFIED), key=_key)
    cap = params.sessions.max_matches_per_session
    over = tuple(replace(v, decision=Decision.QUALIFIED_OVER_CAP) for v in qualified[cap:])
    rest = tuple(v for v in verdicts if v.decision is not Decision.QUALIFIED)
    return SessionReport(tuple(qualified[:cap]), over, rest)


def recheck(previous: Decision, new: Verdict) -> Decision:
    """Decision after a re-check of an already published verdict.

    QUALIFIED          -> stays QUALIFIED only if the new verdict still qualifies, otherwise WITHDRAWN
    QUALIFIED_OVER_CAP -> stays over cap, or SKIP if it no longer qualifies (it was never listed)
    anything else      -> unchanged: a match is never added after publication (no SKIP -> QUALIFIED)
    """
    still_ok = new.decision is Decision.QUALIFIED
    if previous is Decision.QUALIFIED:
        return Decision.QUALIFIED if still_ok else Decision.WITHDRAWN
    if previous is Decision.QUALIFIED_OVER_CAP:
        return Decision.QUALIFIED_OVER_CAP if still_ok else Decision.SKIP
    return previous
