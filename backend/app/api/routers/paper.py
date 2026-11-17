"""Paper betting, the Gate B forward test, and its recorder (docs/API.md step 2, docs/PAPER-BETTING.md).

    GET  /paper/report?params=                  the paper report: integrity, Gate B, forecasts, paper bets, e-values
    GET  /paper/predictions?session=&params=    frozen predictions (newest first, at most `limit`)
    GET  /paper/chain                           hash chain check of the frozen record
    POST /paper/replay/{session}                recompute a frozen session and compare
    GET  /paper/status                          recorder: last runs, today's requests, what is due now
    POST /paper/cycle?dry_run=                  run one recorder cycle now (dry run: answered at once)
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status

from app.api.deps import (
    ClockDep,
    EngineDep,
    OwnerDep,
    ParamsDep,
    RawStoreFactoryDep,
    RegistryDep,
    SettingsDep,
)
from app.api.recorder import (
    MANUAL,
    cycle_inputs,
    paper_summary,
    recorder_status,
    report_json,
    run_manual_cycle,
)
from app.api.responses import ChainOut, CycleOut, PaperPredictionOut, RecorderStatusOut, ReplayOut
from app.api.routers.sessions import parse_session
from app.api.views import entry_json, jsonable
from app.domain.params import Params, load_params, params_path
from app.domain.sessions import SessionCalendar
from app.paper.cycle import run_cycle
from app.paper.freeze import replay_session
from app.storage.repositories import frontend as fe_repo
from app.storage.repositories import paper as paper_repo

router = APIRouter(prefix="/paper", tags=["paper betting"])


def _params_for(version: str, live: Params) -> Params:
    if version == live.params_version:
        return live
    try:
        return load_params(params_path(version))
    except (OSError, ValueError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"params version {version} not found") from exc


@router.get("/report")
def report(_: OwnerDep, engine: EngineDep, params: ParamsDep, version: Annotated[str | None, Query(alias="params")]
           = None) -> dict[str, Any]:
    """`params`: one params version (default: every frozen version together)."""
    return report_json(paper_summary(engine, params, version))


@router.get("/predictions", response_model=list[PaperPredictionOut])
def predictions(_: OwnerDep, engine: EngineDep, params: ParamsDep, session: str | None = None,
                version: Annotated[str | None, Query(alias="params")] = None,
                limit: Annotated[int, Query(ge=1, le=1000)] = 200) -> list[dict[str, Any]]:
    if session is not None:
        parse_session(session)
    cal = SessionCalendar(params)
    with engine.connect() as conn:
        rows = paper_repo.frozen(conn, session, version)[::-1][:limit]
        fixtures = fe_repo.fixtures_by_ids(conn, [r.match_id for r in rows])
    return [{**entry_json(asdict(r), fixtures.get(r.match_id), cal), "session_id": r.session_id,
             "row_hash": r.row_hash} for r in rows]


@router.get("/chain", response_model=ChainOut)
def chain(_: OwnerDep, engine: EngineDep) -> dict[str, Any]:
    with engine.connect() as conn:
        c = paper_repo.verify_chain(conn)
    return {"rows": c.rows, "intact": c.intact, "head_pos": c.head_pos, "head_hash": c.head_hash,
            "problems": [{"chain_pos": pos, "problem": p} for pos, p in c.problems]}


@router.post("/replay/{session_id}", response_model=ReplayOut)
def replay(session_id: str, _: OwnerDep, engine: EngineDep, params: ParamsDep,
           registry: RegistryDep) -> dict[str, Any]:
    parse_session(session_id)
    with engine.connect() as conn:
        versions = sorted({r.params_version for r in paper_repo.frozen(conn, session_id)})
    if not versions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"nothing frozen in {session_id}")
    out = []
    for v in versions:
        rr = replay_session(engine, _params_for(v, params), registry, session_id)
        out.append({"params_version": v, "compared": rr.compared, "identical": rr.identical,
                    "differences": jsonable(rr.differences)})
    return {"session_id": session_id, "identical": all(r["identical"] for r in out), "replays": out}


@router.get("/status", response_model=RecorderStatusOut)
def recorder(_: OwnerDep, engine: EngineDep, params: ParamsDep, registry: RegistryDep, settings: SettingsDep,
             store: RawStoreFactoryDep, now: ClockDep) -> dict[str, Any]:
    return recorder_status(cycle_inputs(engine, registry, params, settings, store=store), now)


@router.post("/cycle", status_code=status.HTTP_202_ACCEPTED, response_model=CycleOut)
def cycle(_: OwnerDep, engine: EngineDep, params: ParamsDep, registry: RegistryDep, settings: SettingsDep,
          store: RawStoreFactoryDep, tasks: BackgroundTasks, now: ClockDep,
          dry_run: bool = False) -> dict[str, Any]:
    """A real cycle runs in the background (a capture can take a minute): follow it with GET /paper/status."""
    x = cycle_inputs(engine, registry, params, settings, store=store)
    if dry_run:
        r = run_cycle(x, now=now, dry_run=True)
        return {"dry_run": True, "busy": r.busy, "lines": r.lines}
    if MANUAL.running:
        raise HTTPException(status.HTTP_409_CONFLICT, "a cycle started from the API is still running")
    tasks.add_task(run_manual_cycle, x)
    return {"dry_run": False, "started": True}
