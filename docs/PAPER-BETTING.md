# Paper betting — the forward test of Gate B

> Owner decisions 2026-11-16/17: profit is proven by **paper betting**, never by history (no historical 15' odds
> exist); the prospective period of tuning plan v2 starts 2026-11-18. Research basis:
> [RESEARCH-PHASE4-TUNING.md](RESEARCH-PHASE4-TUNING.md) §3.6 and §4.

## 1. Plan

| Step | What | Status |
|---|---|---|
| 0 | Check that the free API-Football plan returns 15' odds (`Goal in 1-15 minutes`, `1x2 - 15 minutes`) for upcoming ENG1 / GER1 fixtures | ⏳ 2026-11-17 (still Free): the free plan refuses `next` ("Free plans do not have access to the Next parameter"; 2 requests). On Free it works only by date (yesterday .. tomorrow) - the first ENG1 matches after the international break are on Sat 21 Nov, so `paper-capture` on Fri 20 / Sat 21 Nov is step 0. On Pro any time |
| 1 | **Frozen predictions**: append-only, hash-chained, stamped by the database clock | ✅ this document §2 |
| 2 | Odds snapshot at the same moment: offered 15' odds **and** main-market odds (Model C) | ✅ §5 |
| 3 | Paper bets (qualified, offered odds ≥ minimum odds, 1 unit) and settlement WIN / LOSE / VOID | ✅ §6 |
| 4 | `paper-report`: calibration, ROI with session-bootstrap interval, sequential e-value vs the bookmaker's de-vigged 15' price | ✅ §7 |
| 5 | Jobs in the worker / Task Scheduler: one cycle (capture → freeze → results → settle) every 5 minutes | ✅ §8 |

> **Status 2026-11-17: built, NOT live — unfinished activity.** The code for steps 1–5 is done and tested, but
> the recorder has not been switched on and no real prediction has been frozen yet. What is left: §9.

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

## 4. Owner decision 2026-11-17: API-Football Pro

To start paper betting with this season's data (the free plan cannot reach this season's played matches), the
owner chose to upgrade API-Football to **Pro**. Prepared in the code:

| Piece | What it does |
|---|---|
| `mode: current` in `config/backfill.yaml` | ENG1 / GER1 / AUT1 2026-27 first: the season listing (also the upcoming fixtures, incl. AUT1, which football-data.org does not cover) and the goal minutes of every finished match, 20 per request. Never "finished": every night picks up the new results. Skipped at no cost while the plan is free |
| `daily_max_requests: {free: 70, pro: 2000}` | the backfill budget follows `API_FOOTBALL_PLAN` |
| plan check | `/status` (not counted) reports the key's real plan: with `API_FOOTBALL_PLAN=pro` on a key that is still Free, a run stops before any counted request ("plan mismatch"); `health --quota` prints the plan and flags a mismatch |
| team review | `python -m app.cli review resolve <review id> --new-team --name "Club"` (API: `POST /reviews/{id}/new-team`) creates a club new to the database, e.g. promoted; refused if the name already belongs to a team, and if existing teams share a distinctive word unless confirmed with `--not-similar`. Used for Coventry City and Hull City (ENG1 2026-27: all 380 fixtures linked) |

### After paying (owner, about 5 minutes)

1. Upgrade on the API-Football dashboard; the key normally stays the same. Note the plan's **requests per minute**.
2. In the repo root `.env` (`BETTORY/.env`, the file the settings read) set or add:
   ```
   API_FOOTBALL_PLAN=pro
   API_FOOTBALL_REQUESTS_PER_MINUTE=<the dashboard value, e.g. 300>
   API_FOOTBALL_MAX_REQUESTS_PER_RUN=1000
   ```
3. `python -m app.cli health --quota` → `API-FOOTBALL PLAN: Pro`, no MISMATCH, daily limit 7,500.
4. `python -m app.cli backfill-daily --run` once (instead of waiting for the night): this season's goal minutes
   and the 2022-23 .. 2024-25 backlog.
5. Then steps 0 and 2 – 5 of §1 continue (step 0 no longer competes with the backfill for the quota).

## 5. Step 2 — the odds seen, sealed with the prediction

### 5.1 Flow per session

```
as_of - ~20 min   python -m app.cli paper-capture --session 2026-11-21-NOON     (step 5 will schedule it)
                    1 request per UTC date the session covers: all fixtures of the day (gives the API-Football
                      ids, also for leagues loaded from other sources, and the results of matches already played)
                    then 1 request per upcoming fixture of the plan's leagues in the session: its odds
                      (15' markets and main markets), into core.odds_snapshots (append-only)
                    stops at --max-requests (default 12) - the fixtures left out are counted
>= as_of          python -m app.cli paper-freeze --session 2026-11-21-NOON
                    seals with every prediction the prices known at as_of
```

Capture **before** as_of: a price captured after it (status `LATE`) is stored but never sealed with that
session — it is information the analysis did not have. A snapshot's time is the bookmaker's own update time
(API-Football `update`).

### 5.2 What is sealed

New columns of `paper.predictions`, inside the hash chain:

| Column | Content |
|---|---|
| `odds_seen` | every price known at as_of: latest snapshot per bookmaker, market, line and selection captured strictly before as_of — the same read Model C uses (`PointInTimeReader.odds`) — for `Goal in 1-15 minutes`, `1x2 - 15 minutes`, FT and HT totals, FT 1X2 |
| `best_under05_odds`, `best_under05_book` | the highest `Goal in 1-15 minutes: No` (= 0-0 after 15') among them, and its bookmaker |
| `best_draw_odds`, `best_draw_book` | the highest `1x2 - 15 minutes: Draw`, and its bookmaker |

The paper bet of step 3 is taken at these sealed prices, never at a price seen later. `paper-verify --replay`
also compares the sealed odds with a fresh read.

### 5.3 Daily budget

`config/backfill.yaml` `daily_max_requests` is now a bound per **quota day** (from 00:00 UTC): earlier backfill
runs of the same day count (2026-11-17: a manual run and the night job each took their full 70 before).
The capture's requests come out of what is left (the quota guard keeps the reserve of 5).

### 5.4 Code

| File | Role |
|---|---|
| [app/ingestion/pipeline.py](../backend/app/ingestion/pipeline.py) `api_football_session_odds` | the capture run (cross-checked and audited like every ingestion run) |
| [app/paper/capture.py](../backend/app/paper/capture.py) | `capture_session`: window, run, status CAPTURED / LATE / ENDED |
| [app/paper/odds.py](../backend/app/paper/odds.py) | `seen_odds`: the sealed list and the best 15' prices |
| [tests/integration/test_paper_capture.py](../backend/tests/integration/test_paper_capture.py) | only the session's paper fixtures, the budget, sealing only what was known at as_of |

## 6. Step 3 — the paper bet and its settlement

### 6.1 The bet is decided at the freeze and sealed

For a fixture **listed** in the session report (QUALIFIED after the cap), every bookmaker whose sealed odds hold a
**complete** 15' market (both sides of `Goal in 1-15 minutes`, all three of `1x2 - 15 minutes`: only then can the
margin be removed) is run through the live decision code, `verify` in quote mode (layer 3):

- offered odds ≥ the minimum odds (EV ≥ 5 %, 8 % without a market check, and ≥ Model C fair odds);
- |P − the bookmaker's de-vigged P| ≤ 10 pts (`value.max_bookmaker_gap_pts`): a price far from our own estimate is
  more likely our error than the bookmaker's;
- of the two markets, the higher EV — never both (a 0-0 wins both: they are one risk).

The best qualifying offer (highest EV; ties: Under 0.5, then the bookmaker name) is the paper bet: `bet_market`,
`bet_odds`, `bet_book`, `bet_ev`, `bet_stake` (flat 1 unit, `config/backtest.yaml` `estimated_value.stake`) in the
sealed row. The highest price is not always the bet: the test case has a bookmaker at 1.50 rejected by the gap
rule and the bet taken at 1.40. `paper-verify --replay` recomputes the bet as well.

### 6.2 Settlement

`python -m app.cli paper-settle` — for every frozen prediction whose match kicked off more than 2 h ago:

| Stored result | What happens |
|---|---|
| unchanged since its latest settlement | nothing |
| 15' result not known yet (INCOMPLETE / PENDING) | **pending** — never assumed 0-0; waits for goal minutes |
| known (COMPLETE / DERIVED; PARTIAL for Under 0.5) | a `paper.settlements` row: the 15' outcomes `under05`, `draw` (also without a bet: the calibration of step 4 needs every prediction), and the bet's WIN / LOSE / VOID with its profit (`app/settlement/settle.py`) |
| abandoned / postponed / cancelled | VOID, stake returned |
| changed after a settlement (a corrected score) | a **new** row that `supersedes` the previous one — nothing is overwritten, the correction stays visible |

`paper.settlements` is append-only like the predictions (database clock, UPDATE / DELETE / TRUNCATE refused), and
each row carries the `row_hash` of the sealed prediction it settles (checked by the database). A stored result that
contradicts itself is reported, not settled (exit code 4).

### 6.3 Code

| File | Role |
|---|---|
| [app/paper/bet.py](../backend/app/paper/bet.py) | `bookmaker_quotes`, `paper_bet` (pure; the live `verify`) |
| [app/paper/settle.py](../backend/app/paper/settle.py) | `settle_due` |
| [app/storage/repositories/paper.py](../backend/app/storage/repositories/paper.py) | `settlement_candidates`, `insert_settlement` |
| [tests/integration/test_paper_bets.py](../backend/tests/integration/test_paper_bets.py) | the bet chosen by the live rule, pending, WIN, no-bet outcomes, a corrected score, the database guards |

## 7. Step 4 — the paper betting report

`python -m app.cli paper-report [--params 1.5] [--json] [--out folder]` reads only the sealed predictions and their
latest settlements, and judges them with the **same** rules as the backtest (`calibration_criterion` and
`baseline_criterion` in [app/backtest/report.py](../backend/app/backtest/report.py), `config/backtest.yaml`).
Exit code 4 if the hash chain is broken (the report then starts with a FAIL "Record integrity").

| Section | Content |
|---|---|
| Record | frozen / settled / pending, sessions, corrected results, the chain status and its head |
| Gate B (forward test) | qualified selections (settled listed predictions, ≥ 300), calibration, beats the competition-only price, profit — accruing criteria show NOT_EVALUABLE, never FAIL, until they have enough data |
| Forecasts | Under 0.5 and Draw on every priced, settled prediction: n, mean P, actual, Brier, log loss, calibration buckets; the paired Brier difference vs competition-only with a session-bootstrap interval |
| Paper bets | settled / won / void / pending, staked, profit, ROI with its interval, hit rate vs break-even |
| Sequential evidence | the two e-values below, now and at their peak, against the bar 1/alpha |

### 7.1 The e-values ([app/settlement/evalues.py](../backend/app/settlement/evalues.py))

The wealth of a bet against a null hypothesis H0 that starts at 1, staking a fraction λ of the wealth each time on
a ratio whose expectation is at most 1 under H0, averaged over a fixed grid of λ (0.01 … 0.5). Under H0 it reaches
1/alpha **at most alpha of the time, however often the report is read** (Ville's inequality). That is what allows
a daily look and stopping as soon as the bar is reached — "check the ROI every week and stop when it looks good"
does not have this property.

| Test | Ratio per observation | H0 |
|---|---|---|
| **Profit** (Gate B) | the sealed bet's odds if it won, 0 if it lost | no edge: P(win) ≤ 1 / odds for every bet |
| Forecast | BETTORY's probability of what happened ÷ the bookmakers' de-vigged probability (mean over the bookmakers with a complete sealed `Goal in 1-15 minutes` market) | the market's 15' price is right |

**Gate B profit** = PASS when the profit e-value has reached 1/alpha with at least `min_bets` settled bets
(`config/backtest.yaml` `paper_betting`: alpha **0.05 — proposed, to be confirmed by the owner**, min_bets 100).

### 7.2 How many bets it takes (simulation, odds 1.40, 400 runs each)

| True edge (EV) | Reached by 500 / 1,000 / 2,000 / 3,000 bets | Median bets |
|---|---|---|
| 0 % (no edge) | 3 % / 3 % / 3 % / 4 % | never (as it should: ≤ 5 %) |
| +3 % | 9 % / 18 % / 36 % / 51 % | ≈ 2,900 |
| +5 % | 22 % / 43 % / 80 % / 95 % | ≈ 1,150 |
| +8 % | 62 % / 91 % / 100 % / 100 % | ≈ 390 |
| +12 % | 96 % / 100 % / 100 % / 100 % | ≈ 170 |

A small edge needs a long test: at +5 % about a thousand bets, i.e. many months at a few bets per session. The
forecast e-value and the paired Brier usually give an earlier signal of whether the model is on track.

### 7.3 Code

| File | Role |
|---|---|
| [app/backtest/paper_report.py](../backend/app/backtest/paper_report.py) | `summarise_paper`, `render_paper`, `paper_json`, `market_p_under05` |
| [app/settlement/evalues.py](../backend/app/settlement/evalues.py) | `e_process`, `profit_ratio`, `forecast_ratio` |
| [app/storage/repositories/paper.py](../backend/app/storage/repositories/paper.py) | `report_rows` (each prediction with its latest settlement) |
| [tests/unit/test_evalues.py](../backend/tests/unit/test_evalues.py) | arithmetic, validity under no edge (simulated, looking after every bet), power with an edge |
| [tests/unit/test_paper_report.py](../backend/tests/unit/test_paper_report.py) | the profit verdict, min_bets, the market probability, integrity, the outputs |

## 8. Step 5 — the recorder runs by itself

### 8.1 One cycle, every 5 minutes

`python -m app.cli paper-cycle` runs one cycle. The worker runs it every `schedule.every_minutes` (5), or Windows
Task Scheduler does. Each step decides **from the database alone** whether it is due, so a cycle can run at any time,
as often as wanted, from any process: a step that is not due does nothing and costs nothing. Two cycles never overlap
(a database lock across processes).

| Step | Due when | Requests |
|---|---|---|
| Capture | the session's `as_of` is less than `capture.lead_minutes` (20) away and the session has no successful capture run in that window yet; at most `capture.attempts` (3) runs, failed ones included | fixtures listing per date + 1 odds request per fixture, ≤ `capture.max_requests` (free 12 / Pro 60) |
| Freeze | `as_of` has passed and the session has upcoming fixtures not frozen yet (live params version) | none |
| Results | at most every `results.every_hours` (6): frozen fixtures that kicked off 2 h .. `lookback_hours` (48 h) ago and still have no 15' result | free: 1 per fixture; Pro: 20 fixtures per request; ≤ `results.max_requests` (free 8 / Pro 10) |
| Settle | every cycle | none |

All recorder runs (`paper_capture:<session>`, `paper_results:<time>`) share one bound per quota day (00:00 UTC):
`daily_max_requests` free **25** / Pro 1,000, the owner's split of 2026-11-17 (backfill 70 / recorder 25 /
reserve 5); the quota guard still applies on top. A manual `paper-capture` counts too (same run name).

A freeze without a capture in the window still freezes the predictions, but no odds are sealed and so no paper bet
is placed; the cycle reports it ("no capture before as_of"). The freeze happens at most 5 minutes after `as_of`.

The tuning plan, its SHA-256 and the git commit are read again for every cycle (a new commit is picked up without a
restart); the **params file is read when the worker starts**: restart the worker after changing `PARAMS_FILE`.

### 8.2 Running it

Docker Desktop with `bettory-pg` must be running in both cases.

| Way | How |
|---|---|
| Worker (terminal open) | `python -m app.cli worker` runs the nightly backfill **and** the paper cycle; `--no-paper` without it |
| Task Scheduler (no terminal) | a task every 5 minutes: `backend\venv\Scripts\python.exe -m app.cli paper-cycle`, "Start in" = `backend`; next to the nightly `backfill-daily --run` task. Use one of the two ways, not both (both would be safe because of the lock, just pointless) |

To register the Task Scheduler task (PowerShell, as the owner; adjust the path):

```powershell
$b = "C:\Users\<you>\Documents\BETTORY\backend"
$a = New-ScheduledTaskAction -Execute "$b\venv\Scripts\python.exe" -Argument "-m app.cli paper-cycle" -WorkingDirectory $b
$t = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName "BETTORY paper cycle" -Action $a -Trigger $t -Settings $s
```

Check what is due without doing anything: `python -m app.cli paper-cycle --dry-run` (no request, nothing stored).
Exit code 4 when a step failed (the line starts with `!`; the failure is also a FAILED run in `health`).

### 8.3 Open points

- **Results on the free plan:** whether `fixtures?id=` returns a current-season fixture on the free plan is not
  confirmed yet (the first Saturday shows it). If not, results come from the nightly backfill (`mode: current`,
  Pro), and settlement simply waits (PENDING).
- On Pro, the nightly backfill also loads every result, so the results step usually finds nothing to do.

### 8.4 Code

| File | Role |
|---|---|
| [app/paper/cycle.py](../backend/app/paper/cycle.py) | `run_cycle`: the four steps, the daily bound, the lock, dry run |
| [app/paper/config.py](../backend/app/paper/config.py), [config/paper.yaml](../backend/config/paper.yaml) | schedule and request bounds, validated (the capture window must be longer than the cycle) |
| [app/ingestion/pipeline.py](../backend/app/ingestion/pipeline.py) | `api_football_results`; `api_football_session_odds(job_name=...)` |
| [app/jobs/scheduler.py](../backend/app/jobs/scheduler.py) | `IntervalJob`: the worker runs the cycle (built in the CLI: `app.jobs` must not import `app.paper`) |
| [app/storage/repositories/paper.py](../backend/app/storage/repositories/paper.py) | `frozen_match_ids`, `results_wanted` |
| [tests/integration/test_paper_cycle.py](../backend/tests/integration/test_paper_cycle.py) | capture once inside the window, freeze once after as_of, freeze without capture reported, results fetched once, dry run does nothing |

## 9. Unfinished — what is left before paper betting is live (2026-11-17)

Branch `paper-betting` (commits 4e24d18 .. 39a97ae), **not pushed**, no PR yet. Everything below is open.

### 9.1 Owner actions

| # | Action | Why it blocks |
|---|---|---|
| 1 | Finish the API-Football **Pro** upgrade, then set `API_FOOTBALL_PLAN=pro`, `API_FOOTBALL_REQUESTS_PER_MINUTE`, `API_FOOTBALL_MAX_REQUESTS_PER_RUN=1000` in `BETTORY/.env`; check `health --quota` (§4) | the key still reports **Free**: this season's played matches (goal minutes) cannot be loaded, so the session's fixtures are INSUFFICIENT_DATA and nothing qualifies |
| 2 | Run `backfill-daily --run` once after the upgrade | loads this season's goal minutes and the 2022-23 .. 2024-25 backlog |
| 3 | Confirm **alpha = 0.05** and **min_bets = 100** (`config/backtest.yaml` `paper_betting`, marked PROPOSED) | the profit rule must be fixed before the first real bet is recorded |
| 4 | Approve the Supabase migration `20261117120000_paper_predictions.sql` | applied to the local Docker database only |
| 5 | Switch the recorder on: `python -m app.cli worker`, **or** the Task Scheduler task of §8.2 (not both) | nothing runs by itself until then; Docker (`bettory-pg`) must be running |
| 6 | Say when to push `paper-betting` and open the PR | |

### 9.2 Still to verify (first real weekend: Sat 21 Nov, ENG1)

- **Step 0:** the free plan returns the 15' markets (`Goal in 1-15 minutes`, `1x2 - 15 minutes`) for upcoming
  fixtures — the first capture shows it (`paper-list <session>` lists the sealed odds). With the recorder on by
  Friday evening this happens without further action.
- **Results on the free plan:** whether `fixtures?id=` answers for a current-season fixture (§8.3). If not,
  settlement waits for the nightly backfill on Pro.
- After the first freeze: `paper-verify --replay <session>` → chain INTACT and replay IDENTICAL.

### 9.3 Afterwards (not started)

- The real `tune --real` once the backfill has finished (BACKTEST-REPORT.md §9).
- Read `paper-report` regularly; Gate B profit needs months of bets (§7.2).
