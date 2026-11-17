"""The odds check and the owner's real bets (docs/API.md step 3).

    POST /matches/{id}/quotes        one bookmaker's 15' prices -> decision card (prices stored, append-only)
    POST /bets                       record a placed bet (rule breaches are flagged, never refused)
    GET  /bets?session=&from=&to=    placed bets with their settlement (newest first)
    GET  /bets/{id}                  one bet
    POST /bets/{id}/cancel           cancel a bet recorded by mistake (it stays visible as CANCELLED)
    GET  /performance?from=&to=      ROI with interval, hit rate vs break-even, by market / competition / grade / rules
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.betting import (
    BetInput,
    BetRefused,
    ClosingContext,
    bet_json,
    performance,
    quote_card,
    record_bet,
)
from app.api.deps import ClockDep, EngineDep, OwnerDep, ParamsDep, RegistryDep
from app.api.responses import BetOut, PerformanceOut, QuoteCardOut
from app.api.routers.sessions import parse_session
from app.api.schemas import BetIn, CancelBet, QuoteIn
from app.backtest.config import load_backtest_config
from app.calculation.ev import Bet
from app.domain.betting_config import load_betting_config
from app.domain.params import Params
from app.domain.sessions import SessionCalendar
from app.storage.repositories import betting as bet_repo
from app.storage.repositories import paper as paper_repo

router = APIRouter(tags=["bets"])


def _closing(conn: Any, rows: list[dict[str, Any]], params: Params, now: datetime) -> ClosingContext:
    return ClosingContext(paper_repo.closing_odds(conn, sorted({r["match_id"] for r in rows})), params, now)


def _not_found(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, what)


def _local_range(params: Params, first: date | None, last: date | None) -> tuple[datetime | None, datetime | None]:
    """EAT dates first .. last (inclusive) as UTC bounds of placed_at."""
    tz = SessionCalendar(params).tz
    lo = datetime.combine(first, time(0), tzinfo=tz) if first else None
    hi = datetime.combine(last + timedelta(days=1), time(0), tzinfo=tz) if last else None
    return lo, hi


@router.post("/matches/{match_id}/quotes", response_model=QuoteCardOut)
def quotes(match_id: UUID, body: QuoteIn, _: OwnerDep, engine: EngineDep, params: ParamsDep, registry: RegistryDep,
           now: ClockDep) -> dict[str, Any]:
    entered: dict[Bet, dict[str, float]] = {}
    if body.under05 is not None:
        entered[Bet.UNDER05] = {"YES": body.under05.yes, "NO": body.under05.no}
    if body.draw is not None:
        entered[Bet.DRAW] = {"HOME": body.draw.home, "DRAW": body.draw.draw, "AWAY": body.draw.away}
    with engine.begin() as conn:
        try:
            return quote_card(conn, params, registry, match_id, entered, body.bookmaker.strip(), now,
                              store=body.store)
        except LookupError as exc:
            raise _not_found(str(exc)) from exc


@router.post("/bets", status_code=status.HTTP_201_CREATED, response_model=BetOut)
def place(body: BetIn, _: OwnerDep, engine: EngineDep, params: ParamsDep, now: ClockDep) -> dict[str, Any]:
    b = BetInput(body.match_id, Bet(body.market), body.odds, body.stake, body.bookmaker.strip(), body.placed_at,
                 body.note)
    with engine.begin() as conn:
        try:
            bet_id = record_bet(conn, params, load_betting_config(), b, now)
        except LookupError as exc:
            raise _not_found(str(exc)) from exc
        except BetRefused as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        row = bet_repo.bet(conn, bet_id)
    assert row is not None
    return bet_json(row)


@router.get("/bets", response_model=list[BetOut])
def list_bets(_: OwnerDep, engine: EngineDep, params: ParamsDep, now: ClockDep, session: str | None = None,
              first: Annotated[date | None, Query(alias="from")] = None,
              last: Annotated[date | None, Query(alias="to")] = None,
              include_cancelled: bool = True) -> list[dict[str, Any]]:
    if session is not None:
        parse_session(session)
    lo, hi = _local_range(params, first, last)
    with engine.connect() as conn:
        rows = bet_repo.bets(conn, session_id=session, placed_from=lo, placed_to=hi,
                             include_cancelled=include_cancelled)
        ctx = _closing(conn, rows, params, now)
    return [bet_json(r, ctx) for r in rows]


@router.get("/bets/{bet_id}", response_model=BetOut)
def one_bet(bet_id: UUID, _: OwnerDep, engine: EngineDep, params: ParamsDep, now: ClockDep) -> dict[str, Any]:
    with engine.connect() as conn:
        row = bet_repo.bet(conn, bet_id)
        if row is None:
            raise _not_found(f"unknown bet {bet_id}")
        return bet_json(row, _closing(conn, [row], params, now))


@router.post("/bets/{bet_id}/cancel", response_model=BetOut)
def cancel(bet_id: UUID, body: CancelBet, _: OwnerDep, engine: EngineDep) -> dict[str, Any]:
    with engine.begin() as conn:
        if bet_repo.bet(conn, bet_id) is None:
            raise _not_found(f"unknown bet {bet_id}")
        try:
            bet_repo.cancel_bet(conn, bet_id, body.reason.strip())
        except bet_repo.AlreadyCancelled as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        row = bet_repo.bet(conn, bet_id)
    assert row is not None
    return bet_json(row)


@router.get("/performance", response_model=PerformanceOut)
def perf(_: OwnerDep, engine: EngineDep, params: ParamsDep, now: ClockDep,
         first: Annotated[date | None, Query(alias="from")] = None,
         last: Annotated[date | None, Query(alias="to")] = None) -> dict[str, Any]:
    lo, hi = _local_range(params, first, last)
    with engine.connect() as conn:
        rows = bet_repo.bets(conn, placed_from=lo, placed_to=hi)
        ctx = _closing(conn, rows, params, now)
    return performance(rows, load_betting_config(), load_backtest_config(), ctx)
