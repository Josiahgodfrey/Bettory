"""Response models of the frontend endpoints (docs/API.md): the typed contract the frontend is built against.

FastAPI validates every response against these models (unknown fields are an error), so a change in what an
endpoint returns fails the tests instead of silently breaking the frontend. The OpenAPI schema generated from them
is exported to docs/api/openapi.json (`python -m app.cli openapi`) for generating TypeScript types.

Three responses stay open JSON objects on purpose: the full match analysis (every model's internals), the settings
(the whole params file) and the paper report (its own documented structure, docs/PAPER-BETTING.md 7).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- shared pieces ---------------------------------------------------------------------------------------------


class Result15Out(Out):
    under05: bool | None
    draw: bool | None


class FixtureOut(Out):
    match_id: UUID
    competition_code: str
    competition_name: str
    competition_country: str
    season: str
    round: str | None
    kickoff_utc: datetime
    status: str
    data_status: str
    home_team_id: UUID
    home_team: str
    away_team_id: UUID
    away_team: str
    ft_home: int | None
    ft_away: int | None
    ht_home: int | None
    ht_away: int | None
    h15_home: int | None
    h15_away: int | None
    goal_before_15: bool | None
    first_goal_minute: int | None
    reconciliation_status: str
    updated_at: datetime
    session_id: str
    result_15: Result15Out | None


class CheckOut(Out):
    layer: int
    layer_name: str
    code: str
    passed: bool | None  # None: informational
    value: str
    threshold: str
    detail: str
    blocking: bool


class OddsSeenOut(Out):
    bookmaker: str
    market: str
    line: float | None
    selection: str
    odds: float
    captured_at: str


class PaperBetOut(Out):
    market: str
    odds: float
    bookmaker: str
    ev: float
    stake: float


class RecheckOut(Out):
    status: str  # CONFIRMED / LINEUPS_PENDING / WITHDRAWN
    checked_at: datetime
    decision: str
    failed_checks: list[str]
    lineups_home: bool
    lineups_away: bool
    p_under05: float | None
    p_draw: float | None
    min_odds_under05: float | None
    min_odds_draw: float | None
    attempts: int


class ReportEntryOut(Out):
    match_id: UUID
    decision: str
    grade: str
    failed_checks: list[str]
    error: str | None
    p_under05: float | None
    p_draw: float | None
    p_model_a: float | None
    p_model_b2: float | None
    p_market: float | None
    p_market_draw: float | None
    p_league_00: float | None
    p_league_draw: float | None
    min_odds_under05: float | None
    min_odds_draw: float | None
    fair_under05: float | None
    fair_draw: float | None
    model_spread_pts: float | None
    complete_sample: int | None
    checks: list[CheckOut]
    odds_seen: list[OddsSeenOut]
    best_under05_odds: float | None
    best_under05_book: str | None
    best_draw_odds: float | None
    best_draw_book: str | None
    params_version: str
    formula_version: str | None = None
    as_of: datetime
    recorded_at: datetime | None = None  # null in a preview
    chain_pos: int | None = None
    paper_bet: PaperBetOut | None
    lineup_status: str  # NOT_CHECKED / LINEUPS_PENDING / CONFIRMED / WITHDRAWN
    recheck: RecheckOut | None
    fixture: FixtureOut | None


# --- sessions --------------------------------------------------------------------------------------------------


class SessionOut(Out):
    id: str
    date: date
    kind: str
    start_utc: datetime
    end_utc: datetime
    deadline_utc: datetime
    ready_by_utc: datetime | None
    as_of_utc: datetime | None
    first_kickoff_utc: datetime | None
    report_state: str  # NO_FIXTURES / PLANNED / AWAITING_FREEZE / PUBLISHED / MISSED
    phase: str  # UPCOMING / IN_PROGRESS / ENDED
    fixtures: int
    frozen: int
    listed: int
    paper_bets: int
    withdrawn: int


class SessionReportOut(Out):
    session: SessionOut
    source: str  # FROZEN / PREVIEW / NONE
    as_of_utc: datetime | None
    entries: list[ReportEntryOut]
    decisions: dict[str, int]
    how_to_bet: str


class SessionFixturesOut(Out):
    session: SessionOut
    source: str
    as_of_utc: datetime | None
    entries: list[ReportEntryOut]
    not_analysed: list[FixtureOut]


# --- matches ---------------------------------------------------------------------------------------------------


class CalendarFixtureOut(FixtureOut):
    decision: str | None


class CalendarOut(Out):
    date: date
    timezone: str
    fixtures: list[CalendarFixtureOut]


class GoalOut(Out):
    sequence: int
    minute: int
    extra_minute: int | None
    team: str
    team_id: UUID
    is_own_goal: bool
    is_penalty: bool
    player_name: str | None
    source: str


class MatchSourceOut(Out):
    source: str
    provider_match_key: str
    status: str


class MatchPredictionOut(Out):
    session_id: str
    params_version: str
    as_of: datetime
    recorded_at: datetime
    chain_pos: int
    decision: str


class LineupPlayerOut(Out):
    name: str
    number: int | None
    position: str | None
    grid: str | None
    starter: bool


class LineupOut(Out):
    team_id: UUID
    team: str
    home: bool
    formation: str | None
    players: list[LineupPlayerOut]
    source: str
    fetched_at: datetime


class MatchOut(FixtureOut):
    lineups: list[LineupOut]
    goals: list[GoalOut]
    sources: list[MatchSourceOut]
    predictions: list[MatchPredictionOut]


class OddsRowOut(Out):
    bookmaker: str
    is_users_bookmaker: bool
    market: str
    provider_market_name: str
    line: float | None
    selection: str
    odds: float
    snapshot_type: str
    source: str
    captured_at: datetime


class MatchOddsOut(Out):
    match_id: UUID
    odds: list[OddsRowOut]


# --- system ----------------------------------------------------------------------------------------------------


class MeOut(Out):
    user_id: str
    aal: str
    local_bypass: bool


class BookmakerOut(Out):
    id: UUID
    name: str
    first15_cutoff: str
    own_goals_count: bool
    is_users_bookmaker: bool
    last_odds_at: datetime | None


class AlertOut(Out):
    level: str  # BLOCKING / WARN
    code: str
    message: str
    session: str | None = None
    run_id: str | None = None


class DashboardPaperOut(Out):
    frozen: int
    last_freeze_at: datetime | None
    kicked_off_unsettled: int
    chain_intact: bool


class DashboardOut(Out):
    now_utc: datetime
    timezone: str
    today: date
    next_session: SessionOut | None
    sessions: list[SessionOut]
    alerts: list[AlertOut]
    paper: DashboardPaperOut


# --- paper betting and recorder --------------------------------------------------------------------------------


class PaperPredictionOut(ReportEntryOut):
    session_id: str
    row_hash: str


class ChainProblemOut(Out):
    chain_pos: int
    problem: str


class ChainOut(Out):
    rows: int
    intact: bool
    head_pos: int | None
    head_hash: str | None
    problems: list[ChainProblemOut]


class ReplayItemOut(Out):
    params_version: str
    compared: int
    identical: bool
    differences: list[dict[str, Any]]


class ReplayOut(Out):
    session_id: str
    identical: bool
    replays: list[ReplayItemOut]


class RunMarkOut(Out):
    id: UUID
    job_name: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    requests_used: int | None
    error: str | None


class ManualCycleOut(Out):
    started_at: datetime | None
    finished_at: datetime | None
    running: bool
    error: str | None
    lines: list[str]
    errors: int


class RecorderStatusOut(Out):
    now_utc: datetime
    competitions: list[str]
    api_football_plan: str
    requests_today: int
    requests_daily_bound: int
    last_capture: RunMarkOut | None
    last_results: RunMarkOut | None
    last_freeze_at: datetime | None
    last_settlement_at: datetime | None
    frozen: int
    kicked_off_unsettled: int
    due_now: list[str]
    cycle_running: bool
    manual_cycle: ManualCycleOut | None
    schedule_every_minutes: int
    telegram: TelegramStatusOut


class CycleOut(Out):
    dry_run: bool
    busy: bool | None = None
    lines: list[str] | None = None
    started: bool | None = None


# --- odds check, bets, performance -----------------------------------------------------------------------------


class QuoteCardOut(Out):
    match_id: UUID
    session_id: str
    mode: str  # FROZEN / PREVIEW
    as_of_utc: datetime
    bookmaker: str
    fixture: FixtureOut
    listed: bool | None
    report_decision: str | None
    decision: str
    advice: str  # BET / NO_BET / WAIT
    reason: str
    selected_market: str | None
    selected_odds: float | None
    ev: float | None
    p_under05: float | None
    p_draw: float | None
    min_odds_under05: float | None
    min_odds_draw: float | None
    checks: list[CheckOut]
    stored_prices: int


class BetClvOut(Out):
    fair_closing_p: float  # mean de-vigged closing probability of the selection
    fair_closing_odds: float
    books: int  # bookmakers with a complete closing market
    book_closing_odds: float | None  # the bet's own bookmaker's closing price, if known
    clv_fair: float  # taken odds x fair closing P - 1 (> 0: beat the closing line)
    clv_book: float | None


class BetOut(Out):
    id: UUID
    recorded_at: datetime
    match_id: UUID
    session_id: str
    prediction_id: UUID | None
    market: str
    odds: float
    stake: float
    currency: str
    bookmaker: str
    placed_at: datetime
    min_odds: float | None
    rule_breaches: list[str]
    note: str | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    competition_code: str
    competition_name: str
    home_team: str
    away_team: str
    kickoff_utc: datetime
    status: str
    ft_home: int | None
    ft_away: int | None
    grade: str | None
    decision: str | None
    p_under05: float | None
    p_draw: float | None
    state: str  # PENDING / WIN / LOSE / VOID / CANCELLED
    profit: float | None
    result_15: Result15Out | None
    follows_rules: bool
    clv: BetClvOut | None  # once kicked off, with a usable closing line


class RoiIntervalOut(Out):
    low: float
    high: float
    sessions: int
    reliable: bool


class RoiOut(Out):
    settled: int
    wins: int
    voids: int
    pending: int
    staked: float
    profit: float
    roi: float | None
    hit_rate: float | None
    break_even: float | None
    roi_interval: RoiIntervalOut | None
    clv_n: int
    clv_mean: float | None
    clv_beat: float | None


class CurvePointOut(Out):
    bet_id: UUID
    kickoff_utc: datetime
    profit: float
    cumulative: float


class PerformanceOut(Out):
    currency: str
    bets: int
    cancelled: int
    overall: RoiOut
    by_market: dict[str, RoiOut]
    by_competition: dict[str, RoiOut]
    by_grade: dict[str, RoiOut]
    by_rules: dict[str, RoiOut]
    profit_curve: list[CurvePointOut]
    confidence: float


# --- notifications ---------------------------------------------------------------------------------------------


class NotificationOut(Out):
    id: UUID
    event_key: str
    kind: str  # REPORT_READY / NO_MATCHES / WITHDRAWN / FAILURE / LOGIN / TEST
    message: str
    created_at: datetime
    status: str  # PENDING / SENT / FAILED / EXPIRED
    attempts: int
    sent_at: datetime | None
    last_error: str | None


class NotificationsOut(Out):
    configured: bool
    counts: dict[str, int]
    items: list[NotificationOut]


class TestNotificationOut(Out):
    sent: int
    failed: int


class TelegramStatusOut(Out):
    configured: bool
    pending: int
    failed: int
