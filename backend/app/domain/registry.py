"""Competition registry (config/competitions.yaml) and season-label helpers. Pure: no I/O except load()."""

from __future__ import annotations

from enum import StrEnum
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from pydantic import Field, field_validator

from app.domain.enums import CompetitionType, SourceCode
from app.domain.models import Strict

REGISTRY_FILE = Path(__file__).resolve().parents[2] / "config" / "competitions.yaml"


class SeasonFormat(StrEnum):
    SPLIT = "SPLIT"  # Aug-May: "2025-26"
    CALENDAR = "CALENDAR"  # Jan-Dec: "2025"


class CompetitionDef(Strict):
    code: str = Field(pattern=r"^[A-Z]{3}\d$")
    name: str
    country: str
    type: CompetitionType
    level: int | None = Field(default=None, ge=1, le=10)
    timezone: str
    season_format: SeasonFormat
    team_sources: tuple[SourceCode, ...]
    providers: dict[SourceCode, str]

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str) -> str:
        ZoneInfo(value)  # raises for unknown zones
        return value

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


class Registry:
    def __init__(self, competitions: list[CompetitionDef]) -> None:
        codes = [c.code for c in competitions]
        if len(codes) != len(set(codes)):
            raise ValueError("duplicate competition codes in registry")
        self._by_code = {c.code: c for c in competitions}
        self._by_provider: dict[tuple[SourceCode, str], CompetitionDef] = {}
        for comp in competitions:
            for source, key in comp.providers.items():
                if (source, key) in self._by_provider:
                    raise ValueError(f"provider key {source}:{key} used twice in registry")
                self._by_provider[(source, key)] = comp

    @property
    def competitions(self) -> list[CompetitionDef]:
        return list(self._by_code.values())

    def get(self, code: str) -> CompetitionDef:
        return self._by_code[code]

    def by_provider(self, source: SourceCode, key: str) -> CompetitionDef | None:
        return self._by_provider.get((source, key))


@cache
def load_registry(path: Path = REGISTRY_FILE) -> Registry:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Registry([CompetitionDef(**c) for c in data["competitions"]])


# --- season labels -------------------------------------------------------------------------


def season_label(start_year: int, fmt: SeasonFormat) -> str:
    """2025 -> '2025-26' (SPLIT) or '2025' (CALENDAR)."""
    if fmt is SeasonFormat.SPLIT:
        return f"{start_year}-{(start_year + 1) % 100:02d}"
    return str(start_year)


def season_start_year(label: str) -> int:
    return int(label[:4])


def fdcouk_season_code(label: str) -> str:
    """'2025-26' -> '2526' (football-data.co.uk folder name)."""
    start = season_start_year(label)
    return f"{start % 100:02d}{(start + 1) % 100:02d}"
