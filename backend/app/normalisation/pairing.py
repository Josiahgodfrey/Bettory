"""Link a source's team ids to BETTORY teams through MATCHES both sides already hold (docs/ESPN.md 4). Pure.

Not fuzzy name matching (names.py stays exact): a team is identified by a fixture of the same competition in which
the OTHER team is already known (linked earlier, or by its exact normalised alias), on the same days, with the
same score when both sides have one. When neither team is known nothing is inferred - kick-off and score alone are
not proof, because the stored matches of a day may be incomplete. Anything ambiguous returns None and the team
goes to the review queue as before. A wrong link could not hide: the loader then reconciles the source's FT / HT /
15' values against the stored match, and any disagreement is a CONFLICT (excluded from statistics).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from app.domain.models import SourceMatch

ONE_KNOWN_WINDOW = timedelta(hours=36)  # the known team plays once in that time: the date is the evidence


@dataclass(frozen=True)
class StoredMatch:
    match_id: UUID
    home_id: UUID
    away_id: UUID
    kickoff_utc: datetime
    ft: tuple[int, int] | None


def pair(m: SourceMatch, home: UUID | None, away: UUID | None,
         stored: Sequence[StoredMatch]) -> tuple[UUID, UUID] | None:
    """(home team, away team) of the stored match that `m` is, or None when that is not certain.

    `home` / `away`: the teams the source's ids are already linked to (None = not linked)."""
    if home is not None and away is not None:
        return home, away
    if (home is None and away is None) or m.kickoff_utc is None:
        return None
    ft = (m.ft.home, m.ft.away) if m.ft is not None else None
    found = [s for s in stored
             if abs(s.kickoff_utc - m.kickoff_utc) <= ONE_KNOWN_WINDOW
             and (ft is None or s.ft is None or s.ft == ft)
             and (s.home_id == home if home is not None else s.away_id == away)]
    if len(found) != 1:
        return None
    s = found[0]
    if s.home_id == s.away_id:
        return None
    return s.home_id, s.away_id
