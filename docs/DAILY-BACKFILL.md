# BETTORY — Automatic Daily Backfill

> Collects historical goal minutes from API-Football every day, inside the free plan's quota, without anyone
> running commands. Purpose: enough seasons with 15' results for the Gate B backtest
> ([BACKTEST-REPORT.md](BACKTEST-REPORT.md)). Built 2026-11-16 (branch `daily-backfill`).

---

## 1. What it does each day

Configured in [config/backfill.yaml](../backend/config/backfill.yaml):

```
00:30 UTC (after the quota reset at 00:00 UTC - verify on your API-Football dashboard)
for each target, in the order of the file:
    finished?        last successful run left no fixture without goal minutes  -> skip (0 requests)
    otherwise        backfill with what is left of today's budget
    stop when        daily_max_requests or the quota is used up, or a run stopped at its budget
```

| Target mode | Fetches | Use for |
|---|---|---|
| `history` | only fixtures whose 15' result is **unknown** | seasons with no second goal-minute source (2022-23 – 2024-25) |
| `crosscheck` | every finished fixture, also known ones | seasons that already have goal minutes elsewhere (2025-26 next to openfootball): the 15'-vs-15' check. Needs a paid plan |

Default queue: Premier League, Bundesliga, Austria — 2024-25, then 2023-24, then 2022-23, all `history`.
Expected duration on the free plan: about 4 weeks (90 requests a day).

Every run it starts is an ordinary ingestion run: **cross-checked, audited and visible** in
`python -m app.cli health` and `/data-health` ([DATA-HEALTH.md](DATA-HEALTH.md)).

---

## 2. Safety

| Guard | Effect |
|---|---|
| Quota guard | each run fits inside today's quota minus the reserve (`config/data_quality.yaml`); nothing left → no request |
| Daily budget | `daily_max_requests` across all targets of the day |
| Cross-process lock | one API-Football job at a time across the API, the worker and the command line (Postgres advisory lock); a busy source stops the day cleanly |
| Per-minute pacing | `API_FOOTBALL_REQUESTS_PER_MINUTE` (free: 10) |
| Config validation | unknown keys, unknown competitions, duplicates, bad time or time zone → the worker refuses to start |
| Coalescing | a missed day runs once when the worker is back (within 6 hours), never twice; never overlaps itself |

---

## 3. Commands

```bash
python -m app.cli backfill-daily          # where every target stands (no request)
python -m app.cli backfill-daily --run    # run today's backfill now
python -m app.cli worker                  # start the scheduler in the foreground (Ctrl+C to stop)
python -m app.cli health --quota          # what the runs did (coverage, audits, quota)
```

## 4. Keeping it running before deployment

The worker only runs while its process runs. Until the backend is deployed (Phase 7), two options on this PC:

| Option | How | Note |
|---|---|---|
| Worker in a terminal | `cd backend`, activate the venv, `python -m app.cli worker` | Runs while the terminal and the PC are on; a run missed by < 6 h is caught up |
| Windows Task Scheduler (no open terminal) | daily task at 03:30 EAT running `backend\venv\Scripts\python.exe -m app.cli backfill-daily --run` with "Start in" = `backend` | Docker (Postgres) must be running at that time |

In both cases Docker Desktop with the `bettory-pg` container must be running, and `.env` must hold `API_FOOTBALL_KEY`.

## 5. After upgrading to a paid plan

1. `.env`: `API_FOOTBALL_PLAN=pro`, `API_FOOTBALL_REQUESTS_PER_MINUTE` = your plan's limit, a larger
   `API_FOOTBALL_MAX_REQUESTS_PER_RUN`.
2. `config/backfill.yaml`: raise `daily_max_requests`; enable the commented `crosscheck` targets for 2025-26.
3. The remaining history targets then finish in one or two days (20 fixtures per request).

---

## 6. What the branch `daily-backfill` changed

13 files, 686 lines added. Commit `eb86a64`, on top of `main` after PR #4.

### 6.1 How the pieces fit

```
python -m app.cli worker
  └─ jobs/scheduler.py        build_scheduler(): one cron job "daily_backfill" at schedule.time
       └─ jobs/backfill.py    run_daily_backfill(): for each unfinished target, in order
            ├─ target_status()                    finished?  <- storage/repositories/ops.py latest_run()
            └─ ingestion/pipeline.py              api_football_backfill(..., skip_known_15=history mode)
                 ├─ _exclusive()                  one API-Football job across processes <- ops.exclusive()
                 ├─ _plan_quota()                 fit the run into today's quota (from PR #4)
                 └─ Job.finish()                  cross-check + audit of the run (from PR #4)
```

### 6.2 New and changed code, file by file

| File | Status | What is in it |
|---|---|---|
| [config/backfill.yaml](../backend/config/backfill.yaml) | new | The queue: `schedule` (time zone, HH:MM), `daily_max_requests`, ordered `targets` (competition, season, mode). The 2025-26 `crosscheck` targets are commented out until a paid plan |
| [app/jobs/backfill.py](../backend/app/jobs/backfill.py) | new (was a one-line stub) | **Config models** `Target`, `Schedule`, `BackfillConfig` (strict: unknown keys, bad time / time zone and duplicate targets are rejected); `check_registry()` (every target must be a registered competition with an API-Football league id); `load_backfill_config()`. **Progress**: `TargetState` (FINISHED / IN_PROGRESS / NOT_STARTED) and `target_status()`, read from the target's last SUCCEEDED run (`fixtures_still_without_minutes == 0`, not stopped at its budget; a history run never finishes a crosscheck target). **The day**: `run_daily_backfill()` returns a `DayResult` (runs, requests used, why it stopped); `plan()` shows every target without a request |
| [app/jobs/scheduler.py](../backend/app/jobs/scheduler.py) | new (was a stub) | `build_scheduler()`: APScheduler `BlockingScheduler`, cron trigger in the configured time zone, `coalesce=True`, `max_instances=1`, `misfire_grace_time` 6 h; checks the config against the registry at start. `daily_backfill_job()` logs the outcome and never lets an error stop the scheduler. Phase 5 adds its jobs here |
| [app/storage/repositories/ops.py](../backend/app/storage/repositories/ops.py) | new (was a stub) | `exclusive(engine, name)`: Postgres session advisory lock (`pg_try_advisory_lock`) as a context manager, yields False at once if another process holds it; stable 64-bit key from SHA-256 of the name. `latest_run(engine, job_name, status)`: the newest run row of a job |
| [app/ingestion/pipeline.py](../backend/app/ingestion/pipeline.py) | changed | `ConcurrentRunError` and `_exclusive()`: the three API-Football jobs (season, day, backfill) now run under the lock `ingest:API_FOOTBALL`. `api_football_backfill()` takes the required keyword `skip_known_15`; new statistic `skipped_known_15` |
| [app/cli.py](../backend/app/cli.py) | changed | `worker` (start the scheduler), `backfill-daily` (plan) and `backfill-daily --run` (one day now); `ingest api-football-backfill --skip-known` |
| [app/api/schemas.py](../backend/app/api/schemas.py), [app/api/routers/jobs.py](../backend/app/api/routers/jobs.py) | changed | `POST /jobs/ingest/api-football-backfill` accepts `skip_known_15` (default false) |
| [pyproject.toml](../backend/pyproject.toml) | changed | mypy: `apscheduler` ships no type information (same treatment as scipy) |

Nothing changed in the calculation, decision, analysis or backtest code, and there is no database migration: the
lock is a Postgres feature and progress is read from the existing `raw.ingestion_runs`.

### 6.3 Design decisions

| Decision | Why |
|---|---|
| Progress is derived from run rows, not stored separately | One source of truth: manual runs of the same target count too, and nothing can get out of sync |
| `history` mode skips matches already known | For 2022-23 – 2024-25 no second goal-minute source exists; a half-time 0-0 match is already a certain 0-0 at 15', so its minutes add nothing (about a quarter of each day's requests saved) |
| Database lock instead of only the in-process lock | The API and the worker are separate processes sharing one quota; two runs at once would fetch the same fixtures twice |
| A failed target does not stop the day, but quota / lock problems do | A season-specific error (e.g. a season the plan does not allow) should not block the others; a used-up quota or a busy source affects every target |
| Targets are for finished seasons | The current season changes daily; it belongs to the Phase 5 day jobs |

### 6.4 Tests

| Test file | Covers |
|---|---|
| [tests/integration/test_daily_backfill.py](../backend/tests/integration/test_daily_backfill.py) (new, 6 tests) | targets in order inside the daily budget; a finished target costs no request on the next day; crosscheck mode fetches the known matches a history run skipped; no quota → the day stops with no request; another process holding the lock → the day stops cleanly and the lock is released; config validation; the shipped config is valid and builds the expected schedule |
| [tests/integration/test_api_football_backfill.py](../backend/tests/integration/test_api_football_backfill.py) (1 test added) | history mode fetches exactly the unknown fixtures |

The fake API keeps **one quota counter across runs**, like the real daily limit. Results: 411 tests pass;
ruff, mypy (strict) and the import contracts are clean.

### 6.5 Verified on the development database

`python -m app.cli backfill-daily` (no request): ENG1 2024-25 IN_PROGRESS, the other 8 targets NOT_STARTED.
The real run starts with the next quota day. The worker is **not started automatically**: see §4.
