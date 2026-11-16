# Paper betting — the forward test of Gate B

> Owner decisions 2026-11-16/17: profit is proven by **paper betting**, never by history (no historical 15' odds
> exist); the prospective period of tuning plan v2 starts 2026-11-18. Research basis:
> [RESEARCH-PHASE4-TUNING.md](RESEARCH-PHASE4-TUNING.md) §3.6 and §4.

## 1. Plan

| Step | What | Status |
|---|---|---|
| 0 | Check that the free API-Football plan returns 15' odds (`Goal in 1-15 minutes`, `1x2 - 15 minutes`) for upcoming ENG1 / GER1 fixtures | ⏳ quota: 95/100 used by the backfill on 2026-11-17; runs after the next reset with the new split |
| 1 | **Frozen predictions**: append-only, hash-chained, stamped by the database clock | ✅ this document §2 |
| 2 | Odds snapshot at the same moment: offered 15' odds **and** main-market odds (Model C) | ⬜ |
| 3 | Paper bets (qualified, offered odds ≥ minimum odds, 1 unit) and settlement WIN / LOSE / VOID | ⬜ |
| 4 | `paper-report`: calibration, ROI with session-bootstrap interval, sequential e-value vs the bookmaker's de-vigged 15' price | ⬜ |
| 5 | Jobs in the worker / Task Scheduler | ⬜ |

**Daily API-Football quota (owner decision 2026-11-17):** backfill 70 / recorder 25 / reserve 5
(`config/backfill.yaml` `daily_max_requests: 70`). The backfill finishes about a week later (≈ 5 weeks).

## 2. Step 1 — frozen predictions

### 2.1 What is stored

`paper.predictions` (migration `supabase/migrations/20261117120000_paper_predictions.sql`): one row per fixture,
session and params version, with the verdict (decision, grade, failed checks), the probabilities (P(Under 0.5),
P(draw), Model A, Model B v2, Model C, the competition-only baseline), the minimum odds, the session's `as_of`,
and the provenance: params and formula version, the tuning plan in force and its SHA-256, the git commit of the
code (`+dirty` when `app/` or `config/` had uncommitted changes).

### 2.2 Why a frozen row can be trusted

| Guard | Where |
|---|---|
| Recording time is the **database clock** (`clock_timestamp()`), not the caller's | trigger `paper.seal_prediction` |
| A row whose kick-off is not later than the recording time is refused | same trigger |
| The kick-off must equal `core.matches.kickoff_utc` | same trigger |
| `as_of < kick-off` | check constraint |
| UPDATE, DELETE, TRUNCATE refused (append-only) | triggers `predictions_no_change`, `predictions_no_truncate` |
| Each row stores SHA-256(previous row's hash, own content): a change made around the triggers (a superuser can switch them off) breaks the chain | `paper.prediction_hash`, `paper.verify_chain()` |
| Rows removed from the **end** leave no gap: `paper-verify` prints the head (position + hash); note it down or publish it — a later head must extend it | `python -m app.cli paper-verify` |
| **Replay check**: the same fixtures evaluated again at the stored `as_of` with the stored params version must give the same numbers. A difference means data known before `as_of` arrived or changed after the freeze (e.g. a late result), and is reported, never hidden | `paper-verify --replay SESSION` |

The freeze uses the same session step as the backtest ([app/analysis/session.py](../backend/app/analysis/session.py)):
`as_of` = the session's `run_start`, the same cap, the same verdict. A frozen live prediction and its backtest
replay are therefore directly comparable.

### 2.3 Commands

```bash
python -m app.cli paper-freeze                          # every session not ended, yesterday .. tomorrow
python -m app.cli paper-freeze --session 2026-11-21-NOON --params 1.5 --params 1.6   # several params = shadow test
python -m app.cli paper-verify [--replay 2026-11-21-NOON]   # exit 4 if the chain is broken or a replay differs
python -m app.cli paper-list 2026-11-21-NOON
```

`paper-freeze` statuses: `FROZEN` (new / already / started / listed counts and chain positions), `TOO_EARLY`
(before the data cut-off `as_of`: freeze after it), `NO_FIXTURES`, `ENDED`. Freezing twice is harmless (the
second run reports `already`). Fixtures that kicked off before the freeze are evaluated (the cap ranks the whole
session) but never stored.

### 2.4 Code

| File | Role |
|---|---|
| [app/analysis/session.py](../backend/app/analysis/session.py) | `evaluate_session` (analysis + verdict + cap) and `league_baseline`, shared by the backtest and the freeze |
| [app/paper/freeze.py](../backend/app/paper/freeze.py) | `freeze_session`, `replay_session`, `due_sessions` |
| [app/storage/repositories/paper.py](../backend/app/storage/repositories/paper.py) | `session_fixtures`, `insert_prediction`, `frozen`, `verify_chain` |
| [tests/integration/test_paper_freeze.py](../backend/tests/integration/test_paper_freeze.py) | point in time, idempotence, database guards, tamper detection, replay |

## 3. Data the recorder needs (found 2026-11-17)

The tables work; on the real database the first freeze found **no 2026-27 fixture**: the current season had never
been loaded (the backfill covers finished seasons only).

- `ingest football-data-org --comp ENG1 --season 2026-27` loaded 380 fixtures (306 linked; **74 wait for team
  review**: Coventry City and Hull City are new to the database and `review resolve` can only link to an existing
  team).
- A preview (nothing stored) of the next ENG1 session, 2026-11-21 NOON: 6 linked fixtures, **all
  INSUFFICIENT_DATA** — the current season has no goal minutes (51 INCOMPLETE, 30 DERIVED), so the team windows
  and the competition baseline fail.
- openfootball has no 2026-27 file yet (HTTP 404). The API-Football free plan cannot list current-season fixtures
  (Phase 0, T1) and fixtures by date only cover yesterday .. tomorrow, so the goal minutes of this season's
  **already played** matches cannot be reached on the free plan; from now on the recorder can collect them day by
  day.
- AUT1 is not on football-data.org's free tier; GER1 (`BL1`) is.
