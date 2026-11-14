"""Every status in BETTORY has a fixed list of allowed values.

Enums marked `# db: core.<type>` mirror PostgreSQL enums in supabase/migrations and are kept in
sync by tests/unit/test_enum_parity.py. Any value outside these lists is rejected.
"""

from enum import StrEnum

# ---------------------------------------------------------------------------
# Mirrored database enums
# ---------------------------------------------------------------------------


class SourceKind(StrEnum):  # db: core.source_kind
    API = "API"
    OPEN_DATA = "OPEN_DATA"
    CSV = "CSV"
    MANUAL = "MANUAL"


class CompetitionType(StrEnum):  # db: core.competition_type
    LEAGUE = "LEAGUE"
    CUP = "CUP"
    CONTINENTAL = "CONTINENTAL"
    FRIENDLY = "FRIENDLY"  # stored, but always excluded from statistics


class Gender(StrEnum):  # db: core.gender
    MEN = "MEN"
    WOMEN = "WOMEN"


class AgeGroup(StrEnum):  # db: core.age_group
    SENIOR = "SENIOR"
    U23 = "U23"
    U21 = "U21"
    U20 = "U20"
    U19 = "U19"
    U18 = "U18"
    U17 = "U17"
    OTHER = "OTHER"


class FixtureStatus(StrEnum):  # db: core.fixture_status
    SCHEDULED = "SCHEDULED"
    LIVE = "LIVE"
    FINISHED = "FINISHED"
    POSTPONED = "POSTPONED"
    CANCELLED = "CANCELLED"
    ABANDONED = "ABANDONED"
    AWARDED = "AWARDED"


class DataStatus(StrEnum):  # db: core.data_status
    """Completeness of a match's 15' data (DATA-MODEL.md section 7)."""

    PENDING = "PENDING"  # not played yet
    COMPLETE = "COMPLETE"  # full goal timeline
    DERIVED = "DERIVED"  # recovered from first-goal minute + HT score
    PARTIAL_DRAW_UNKNOWN = "PARTIAL_DRAW_UNKNOWN"  # UNDER 0.5 known, DRAW unknown
    INCOMPLETE = "INCOMPLETE"  # excluded; never assumed 0-0


class ReconciliationStatus(StrEnum):  # db: core.reconciliation_status
    SINGLE_SOURCE = "SINGLE_SOURCE"
    AGREED = "AGREED"
    CONFLICT = "CONFLICT"  # excluded from statistics until resolved
    MANUAL = "MANUAL"


class Category(StrEnum):  # db: core.history_category
    """Team history view."""

    GENERAL = "GENERAL"
    HOME = "HOME"
    AWAY = "AWAY"


class EntityType(StrEnum):  # db: core.entity_type
    COMPETITION = "COMPETITION"
    TEAM = "TEAM"


class MappingMethod(StrEnum):  # db: core.mapping_method
    EXACT_ID = "EXACT_ID"
    ALIAS = "ALIAS"
    MANUAL = "MANUAL"


class ReviewStatus(StrEnum):  # db: core.review_status
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"


class Side(StrEnum):  # db: core.side
    HOME = "HOME"
    AWAY = "AWAY"


class MarketCode(StrEnum):  # db: core.market_code
    FT_1X2 = "FT_1X2"
    FT_TOTAL = "FT_TOTAL"
    HT_TOTAL = "HT_TOTAL"
    HT_ASIAN_TOTAL = "HT_ASIAN_TOTAL"
    FIRST15_RESULT = "FIRST15_RESULT"  # e.g. API-Football "1x2 - 15 minutes"
    FIRST15_GOAL = "FIRST15_GOAL"  # e.g. API-Football "Goal in 1-15 minutes"
    OTHER = "OTHER"


class SnapshotType(StrEnum):  # db: core.snapshot_type
    OPENING = "OPENING"
    ANALYSIS = "ANALYSIS"
    BET_TIME = "BET_TIME"
    CLOSING = "CLOSING"


class Severity(StrEnum):  # db: core.severity
    INFO = "INFO"
    WARN = "WARN"
    BLOCKING = "BLOCKING"


class RunStatus(StrEnum):  # db: core.run_status
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


# Python enum -> PostgreSQL enum type it mirrors (used by the parity test).
DB_ENUMS: dict[type[StrEnum], str] = {
    SourceKind: "core.source_kind",
    CompetitionType: "core.competition_type",
    Gender: "core.gender",
    AgeGroup: "core.age_group",
    FixtureStatus: "core.fixture_status",
    DataStatus: "core.data_status",
    ReconciliationStatus: "core.reconciliation_status",
    Category: "core.history_category",
    EntityType: "core.entity_type",
    MappingMethod: "core.mapping_method",
    ReviewStatus: "core.review_status",
    Side: "core.side",
    MarketCode: "core.market_code",
    SnapshotType: "core.snapshot_type",
    Severity: "core.severity",
    RunStatus: "core.run_status",
}


# ---------------------------------------------------------------------------
# Source codes (match core.sources seed rows)
# ---------------------------------------------------------------------------


class SourceCode(StrEnum):
    API_FOOTBALL = "API_FOOTBALL"
    FOOTBALL_DATA_ORG = "FOOTBALL_DATA_ORG"
    OPENFOOTBALL = "OPENFOOTBALL"
    FOOTBALL_DATA_CO_UK = "FOOTBALL_DATA_CO_UK"
    MANUAL = "MANUAL"


SOURCE_IDS: dict[SourceCode, int] = {
    SourceCode.API_FOOTBALL: 1,
    SourceCode.FOOTBALL_DATA_ORG: 2,
    SourceCode.OPENFOOTBALL: 3,
    SourceCode.FOOTBALL_DATA_CO_UK: 4,
    SourceCode.MANUAL: 9,
}


# ---------------------------------------------------------------------------
# Application enums (tables in later migrations: calc / output / ops)
# ---------------------------------------------------------------------------


class SessionName(StrEnum):
    NIGHT = "NIGHT"  # kick-offs 01:00-12:59 EAT
    NOON = "NOON"  # kick-offs 13:00-00:59 EAT


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
