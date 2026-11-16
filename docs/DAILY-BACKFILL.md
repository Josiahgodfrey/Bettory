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
