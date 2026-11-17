# BETTORY API for the frontend

> Written 2026-11-17. The single reference for building the frontend (Phase 6) on top of the Python API.
> Every endpoint is owner-only: a Supabase access token with two-factor (`aal2`) in `Authorization: Bearer …`
> (ARCHITECTURE.md §7). Local development may use the loopback bypass (`AUTH_LOCAL_BYPASS`).

## 1. Conventions

| Topic | Rule |
|---|---|
| Format | JSON. Request bodies are strict: unknown fields → 422 |
| Times | ISO 8601 in **UTC** (`2026-11-21T12:00:00Z`). The frontend shows them in EAT (`Africa/Nairobi`, from `GET /settings`) |
| Probabilities | fractions 0–1 (`0.778`); the frontend formats them as `77.8%` |
| Odds | decimal (`1.35`) |
| Session id | `2026-11-21-NOON`, `2026-11-21-NIGHT` (ARCHITECTURE.md §12.2) |
| Errors | `401` not logged in / no two-factor · `404` unknown id · `409` conflict (e.g. a job already running) · `422` invalid input, with a `detail` that says why |
| Numbers with context | every probability comes with its sample size and the params version it was computed with |

## 2. What the owner sees and does — screen by screen

| # | Screen | What it shows | What the owner does there | Endpoints |
|---|---|---|---|---|
| 1 | **Login** | Supabase login (password, CAPTCHA, TOTP) | log in | Supabase Auth (frontend only), then `GET /me` |
| 2 | **Today** (home) | the next session: state, countdown to report time and first kick-off, number listed; open bets; alerts (failed runs, blocking data checks, broken record chain, recorder problems) | jump to the session or an alert | `GET /dashboard` |
| 3 | **Bets section** (session report) | session selector; per listed match: kick-off, league, teams, P(15' Under 0.5) and P(15' Draw), fair odds, **minimum odds**, grade, sample sizes, model agreement, odds seen at the cut-off, lineup status; session state and times | pick a match to bet on, open its analysis, record the bet | `GET /sessions`, `GET /sessions/{id}` |
| 4 | **All fixtures of a session** | every fixture of the session with its decision (listed, over the cap, skipped, not enough data) and the failed checks | see why a match is not listed | `GET /sessions/{id}/fixtures` |
| 5 | **Match analysis** | fixture, result and 15' score once played; every model (A, B v2, C, competition baseline) with its probability and sample; every check with measured value and threshold; versions; odds seen | read the reasons | `GET /matches/{id}`, `GET /matches/{id}/analysis` |
| 6 | **Odds check** (decision card) | after entering a bookmaker's 15' prices: de-vigged probability, EV per market, which market to bet (never both), pass/fail with reasons | enter the prices the bookmaker offers | `POST /matches/{id}/quotes`, `GET /matches/{id}/odds` |
| 7 | **Record a placed bet** | form: market, odds taken, stake, bookmaker; warning if the bet breaks a rule | record the real bet | `POST /bets` |
| 8 | **My bets** | open and settled bets: result, profit, WIN / LOSE / VOID / PENDING | cancel a bet recorded by mistake | `GET /bets`, `GET /bets/{id}`, `POST /bets/{id}/cancel` |
| 9 | **Performance** | real bets: ROI with interval, hit rate vs break-even, profit curve, by league / grade / market; calibration of listed predictions | — | `GET /performance` |
| 10 | **Paper betting** (Gate B forward test) | the paper report: record integrity, Gate B criteria, forecasts, calibration, paper bets, e-values; frozen predictions per session; chain check | verify the chain, replay a session | `GET /paper/report`, `GET /paper/predictions`, `GET /paper/chain`, `POST /paper/replay/{session}` |
| 11 | **Recorder / system status** | the recorder (last capture, freeze, results, settlement; what is due next; requests used today vs bound); worker jobs | run one recorder cycle now | `GET /paper/status`, `POST /paper/cycle`, `GET /jobs/runs` |
| 12 | **Calendar** | all fixtures of a day (EAT): kick-off, league, teams, status, result, session, decision if analysed | open a match | `GET /fixtures?date=` |
| 13 | **Data health** | coverage, cross-checks, runs and audits, quota, backtests | run / accept a cross-check | `GET /data-health…` ✅ exists |
| 14 | **Data loading & team review** | ingestion runs, open team reviews | start a load, resolve a review | `POST /jobs/ingest/…`, `GET /reviews`, `POST /reviews/{id}/…` ✅ exist |
| 15 | **Settings** (read-only) | params version and key values, sessions and time zone, competitions, bookmakers, Gate B and recorder settings, API plan | — | `GET /settings`, `GET /bookmakers` |

## 3. Endpoint list

✅ exists · 🆕 this branch (step in §5)

| Method | Path | Purpose | Status |
|---|---|---|---|
| GET | `/health` | liveness (no login) | ✅ |
| GET | `/me` | who is logged in, two-factor level | 🆕 1 |
| GET | `/settings` | params, sessions, competitions, recorder and Gate B settings | 🆕 1 |
| GET | `/bookmakers` | bookmakers known to the system | 🆕 1 |
| GET | `/sessions?from=&to=` | sessions of a date range: times, state, counts | 🆕 1 |
| GET | `/sessions/{id}` | the session report (listed matches) | 🆕 1 |
| GET | `/sessions/{id}/fixtures` | every fixture of the session with its decision | 🆕 1 |
| GET | `/fixtures?date=` | calendar of one EAT date | 🆕 1 |
| GET | `/matches/{id}` | one fixture: teams, status, scores, 15' result, goals | 🆕 1 |
| GET | `/matches/{id}/analysis?session=` | full analysis (models, checks, versions) at the session's cut-off | 🆕 1 |
| GET | `/matches/{id}/odds` | latest odds per bookmaker and market | 🆕 1 |
| GET | `/paper/report` | the paper betting report (JSON of `paper-report`) | 🆕 2 |
| GET | `/paper/predictions?session=` | frozen predictions | 🆕 2 |
| GET | `/paper/chain` | hash chain check | 🆕 2 |
| POST | `/paper/replay/{session}` | recompute a frozen session and compare | 🆕 2 |
| GET | `/paper/status` | recorder status and what is due | 🆕 2 |
| POST | `/paper/cycle` | run one recorder cycle now | 🆕 2 |
| GET | `/dashboard` | the Today screen in one call | 🆕 2 |
| POST | `/matches/{id}/quotes` | enter a bookmaker's 15' prices → decision card | 🆕 3 |
| POST | `/bets` | record a placed bet | 🆕 3 |
| GET | `/bets`, `/bets/{id}` | placed bets with settlement | 🆕 3 |
| POST | `/bets/{id}/cancel` | cancel a bet recorded by mistake (append-only) | 🆕 3 |
| GET | `/performance` | real-bet performance | 🆕 3 |
| | `/jobs/…`, `/reviews…`, `/teams`, `/data-health/…` | data loading, team review, data health | ✅ |

## 4. Design decisions

1. **The published session report is the frozen record.** A session is analysed once, at its cut-off (`as_of`),
   by the paper-betting freeze. The report (`GET /sessions/{id}`) reads those frozen rows, so what the owner bets on
   is exactly what the forward test judges, with no second copy that could disagree. Before the cut-off the same
   endpoint returns a **preview** computed now (state `PREVIEW`, not stored, may still change).
2. **The full analysis is recomputed, not stored.** `GET /matches/{id}/analysis` runs the same analysis at the
   stored `as_of` (deterministic, as the replay check proves) and returns every model and check.
3. **Odds entered by hand** are stored like any other odds (append-only, source MANUAL, snapshot `BET_TIME`).
   They are captured after the cut-off, so they never change a frozen prediction.
4. **Not built here** (other Phase 5 tasks): the lineup re-check and withdrawals (lineup status shows
   `NOT_CHECKED`), Telegram messages, closing odds and CLV.

## 5. Build steps

| Step | Content | Status |
|---|---|---|
| 1 | Read endpoints: me, settings, bookmakers, sessions, session report, session fixtures, calendar, match, analysis, odds | ✅ §7 |
| 2 | Paper betting and recorder endpoints, dashboard | ✅ §8 |
| 3 | Odds check, placed bets (new table, settlement), performance — needs the owner decisions in §6 | ⬜ |
| 4 | OpenAPI schema export and generated TypeScript types for the frontend | ⬜ |

## 6. Owner decisions needed before step 3

| # | Question | Recommendation |
|---|---|---|
| D1 | A placed bet that breaks a rule (match not listed, odds below the minimum, both markets on one match): refuse it, or record it with a flag? | **Record it with a flag** (`rule_breaches`): the money is already staked, so it must count; performance shows rule-following and rule-breaking bets separately |
| D2 | Can a placed bet be edited? | **No** — append-only like every other record; a mistake is cancelled (`POST /bets/{id}/cancel`, with a reason) and recorded again |
| D3 | Stake currency | one currency for all bets, set in settings (e.g. KES) |

## 7. Step 1 — read endpoints (done)

### 7.1 Session states

`GET /sessions` gives each session a `report_state` and a `phase`:

| report_state | Meaning |
|---|---|
| `NO_FIXTURES` | no fixture of the report competitions in the session |
| `PLANNED` | before the cut-off (`as_of_utc`): the report endpoint shows a **preview** computed now |
| `AWAITING_FREEZE` | the cut-off has passed but the recorder has not frozen the session yet (normally ≤ 5 minutes) |
| `PUBLISHED` | frozen: the report is final and sealed |
| `MISSED` | the session ended without a freeze (recorder was off) |

`phase`: `UPCOMING` (before the first kick-off), `IN_PROGRESS`, `ENDED`.

### 7.2 One report entry (`GET /sessions/{id}` → `entries[]`)

| Field | Meaning |
|---|---|
| `fixture` | match id, kick-off, competition, teams, status, scores, `result_15` (`{under05, draw}` once known), `session_id` |
| `decision`, `grade`, `failed_checks` | the verdict (`QUALIFIED` = listed) |
| `p_under05`, `p_draw`, `fair_under05`, `fair_draw` | BETTORY's probabilities and fair odds |
| `min_odds_under05`, `min_odds_draw` | **bet only at or above these** |
| `p_model_a`, `p_model_b2`, `p_market`, `p_league_00` (and `_draw`) | each model's view; `model_spread_pts` = their gap |
| `complete_sample` | smallest exact-15' sample among the team windows (the "n") |
| `checks[]` | every check: `layer`, `layer_name`, `code`, `passed`, `value`, `threshold`, `detail`, `blocking` |
| `odds_seen[]`, `best_under05_*`, `best_draw_*` | prices known at the cut-off |
| `paper_bet` | the forward test's bet, or null |
| `lineup_status` | `NOT_CHECKED` until the lineup re-check exists |
| `chain_pos`, `recorded_at`, `as_of`, `params_version` | the sealed record (null in a preview) |

`source` says where the entries come from: `FROZEN`, `PREVIEW` or `NONE` (`?preview=false`, or nothing to show).

### 7.3 Change to the frozen record

The frozen prediction now also seals `fair_under05`, `fair_draw`, `model_spread_pts`, `complete_sample` and
every check with its measured value (`checks`), all covered by the row hash and compared by the replay check.
The migration `20261117120000_paper_predictions.sql` was still unreleased and the local tables empty, so it was
changed in place and re-applied locally.

### 7.4 Code

| File | Role |
|---|---|
| [app/api/views.py](../backend/app/api/views.py) | session states, report and preview, analysis, JSON conversion |
| [app/api/routers/sessions.py](../backend/app/api/routers/sessions.py), [matches.py](../backend/app/api/routers/matches.py), [system.py](../backend/app/api/routers/system.py) | the endpoints |
| [app/storage/repositories/frontend.py](../backend/app/storage/repositories/frontend.py) | fixtures with names, goals, sources, latest odds, bookmakers, frozen counts |
| [app/api/deps.py](../backend/app/api/deps.py) | `clock_dep`, `params_dep`, `report_competitions_dep` (replaceable in tests) |
| [tests/integration/test_api_frontend.py](../backend/tests/integration/test_api_frontend.py) | preview → frozen, all fixtures, calendar, match, analysis vs frozen, odds, settings, bad input |

Import layers: `app.api` is now the top layer (like the command line) and may use `app.paper` and
`app.backtest`.

## 8. Step 2 — paper betting, recorder, dashboard (done)

### 8.1 `GET /dashboard` (the Today screen)

| Field | Content |
|---|---|
| `next_session` | the first session with fixtures that has not ended, looking up to 14 days ahead (same shape as `GET /sessions`) |
| `sessions` | yesterday .. tomorrow (EAT) |
| `alerts[]` | `level` (`BLOCKING` / `WARN`), `code`, `message`: `CHAIN_BROKEN`, `SESSION_MISSED`, `FREEZE_LATE` (cut-off passed > 15 min ago, not frozen: the recorder is probably off), `RUN_FAILED` (last 24 h), `DATA_CHECK_FAILED` (a cross-check blocks bets) |
| `paper` | frozen predictions, last freeze, kicked-off but unsettled, chain intact |

### 8.2 Recorder

`GET /paper/status`: `requests_today` / `requests_daily_bound`, `last_capture`, `last_results` (runs),
`last_freeze_at`, `last_settlement_at`, `kicked_off_unsettled`, `due_now` (a dry-run cycle: what the next cycle
would do), `cycle_running`, `manual_cycle` (the latest cycle started from the API: lines, errors).

`POST /paper/cycle` starts one real cycle in the background (`202`; `409` if one started from the API is still
running) — the same cycle as the worker and `paper-cycle`, with the same lock and request bound.
`?dry_run=true` answers at once with what is due.

### 8.3 Code

| File | Role |
|---|---|
| [app/api/recorder.py](../backend/app/api/recorder.py) | `cycle_inputs` (shared with the CLI and the worker), `recorder_status`, `paper_summary`, `dashboard` |
| [app/api/routers/paper.py](../backend/app/api/routers/paper.py) | `/paper/report`, `/predictions`, `/chain`, `/replay/{session}`, `/status`, `/cycle` |
| [app/core/provenance.py](../backend/app/core/provenance.py) | `code_version()` (moved from the CLI) |
| [tests/integration/test_api_paper.py](../backend/tests/integration/test_api_paper.py) | chain, predictions, replay, report, recorder status and cycle, dashboard alerts over time |
