"""Source parsers: provider format -> BETTORY standard format (SourceMatch / SourceOdds). Pure."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from app.domain.models import SourceMatch, SourceOdds


class ParseError(ValueError):
    """The payload does not have the structure the parser's contract requires."""


@dataclass
class ParseResult:
    matches: list[SourceMatch] = field(default_factory=list)
    odds: list[SourceOdds] = field(default_factory=list)
    skipped: Counter[str] = field(default_factory=Counter)  # reason -> count
