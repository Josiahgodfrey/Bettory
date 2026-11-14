"""Every status in BETTORY has a fixed list of allowed values.

These enums mirror the PostgreSQL enums defined in the Supabase migrations.
Any value outside these lists is rejected (Architecture 11).
"""

from enum import StrEnum


class Category(StrEnum):
    """Team history view."""

    GENERAL = "GENERAL"
    HOME = "HOME"
    AWAY = "AWAY"


class CompetitionType(StrEnum):
    LEAGUE = "LEAGUE"
    CUP = "CUP"
    CONTINENTAL = "CONTINENTAL"
    FRIENDLY = "FRIENDLY"  # stored, but always excluded from statistics


class FixtureStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    LIVE = "LIVE"
    FINISHED = "FINISHED"
    POSTPONED = "POSTPONED"
    ABANDONED = "ABANDONED"


class DataStatus(StrEnum):
    """Completeness of a historical match's 15' data (Data doc 13.3)."""

    COMPLETE = "COMPLETE"  # full goal timeline
    DERIVED = "DERIVED"  # recovered from first-goal minute + HT score
    PARTIAL_DRAW_UNKNOWN = "PARTIAL_DRAW_UNKNOWN"  # UNDER 0.5 known, DRAW unknown
    INCOMPLETE = "INCOMPLETE"  # excluded; never assumed 0-0


class SessionName(StrEnum):
    NIGHT = "NIGHT"  # kick-offs 01:00-12:59 EAT
    NOON = "NOON"  # kick-offs 13:00-00:59 EAT


class Market(StrEnum):
    FIRST15_TOTAL = "FIRST15_TOTAL"
    FIRST15_RESULT = "FIRST15_RESULT"
    HT_TOTAL_0_5 = "HT_TOTAL_0.5"
    HT_TOTAL_1_5 = "HT_TOTAL_1.5"
    HT_ASIAN_TOTAL_1_0 = "HT_ASIAN_TOTAL_1.0"
    FT_TOTAL_2_5 = "FT_TOTAL_2.5"
    FT_1X2 = "FT_1X2"


class SnapshotType(StrEnum):
    OPENING = "OPENING"
    ANALYSIS = "ANALYSIS"
    BET_TIME = "BET_TIME"
    CLOSING = "CLOSING"


class EntryMethod(StrEnum):
    MANUAL = "MANUAL"
    API = "API"
    CSV_IMPORT = "CSV_IMPORT"


class DevigMethod(StrEnum):
    POWER = "POWER"
    MULTIPLICATIVE = "MULTIPLICATIVE"
    SHIN = "SHIN"


class MarketConsistency(StrEnum):
    OK = "OK"
    MARKET_INCONSISTENT = "MARKET_INCONSISTENT"
    UNAVAILABLE = "UNAVAILABLE"


class Grade(StrEnum):
    A = "A"
    B = "B"
    SKIP = "SKIP"


class Decision(StrEnum):
    QUALIFIED = "QUALIFIED"
    QUALIFIED_OVER_CAP = "QUALIFIED_OVER_CAP"
    SKIP = "SKIP"
    WITHDRAWN = "WITHDRAWN"  # only reachable from QUALIFIED (one-way)
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    ERROR = "ERROR"  # fail closed: never becomes a bet


class BetOutcome(StrEnum):
    PENDING = "PENDING"
    WIN = "WIN"
    LOSE = "LOSE"
    VOID = "VOID"


class JobStatus(StrEnum):
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    LATE = "LATE"
    FAILED = "FAILED"
