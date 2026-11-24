"""Data health, the short version: what is loaded, what is missing, and what the last runs did.

    GET /data-health            coverage per competition-season + the latest ingestion runs
    GET /data-health/coverage   coverage only
    GET /data-health/runs       the latest runs only

Cross-check acceptance, quota and stored-backtest listings were removed in the lean-free pass: a single-source
(ESPN) pipeline has nothing to cross-check, and the backtest is read from `backtests/` by the Performance screen.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.api.deps import EngineDep, OwnerDep
from app.ingestion.health_view import snapshot

router = APIRouter(prefix="/data-health", tags=["data health"])


@router.get("")
def overview(_: OwnerDep, engine: EngineDep,
             runs: Annotated[int, Query(ge=1, le=200)] = 10) -> dict[str, Any]:
    """Coverage, the latest runs with their audits, and the open items."""
    with engine.connect() as conn:
        return snapshot(conn, runs)


@router.get("/coverage")
def coverage(_: OwnerDep, engine: EngineDep) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        result: list[dict[str, Any]] = snapshot(conn, 1)["coverage"]
        return result


@router.get("/runs")
def runs(_: OwnerDep, engine: EngineDep, limit: Annotated[int, Query(ge=1, le=200)] = 20) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        result: list[dict[str, Any]] = snapshot(conn, limit)["runs"]
        return result
