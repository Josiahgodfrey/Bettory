# BETTORY API for the frontend

> **Lean-free pass (2026-11-25):** parts of this document describe machinery that was removed or replaced.
> Read [LEAN-FREE.md](LEAN-FREE.md) for the delta before relying on endpoint or screen lists here.

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
| GET | `/notifications` | the Telegram outbox and whether Telegram is configured | 🆕 later |
| POST | `/notifications/test` | send a test Telegram message now | 🆕 later |
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
4. **The re-check** about an hour before kick-off can withdraw a listed match (PAPER-BETTING.md §10): every
   report entry carries `lineup_status` and `recheck`.

## 5. Build steps

| Step | Content | Status |
|---|---|---|
| 1 | Read endpoints: me, settings, bookmakers, sessions, session report, session fixtures, calendar, match, analysis, odds | ✅ §7 |
| 2 | Paper betting and recorder endpoints, dashboard | ✅ §8 |
| 3 | Odds check, placed bets (new table, settlement), performance | ✅ §9 |
| 4 | Typed responses, OpenAPI schema export for the frontend's TypeScript types | ✅ §10 |

## 6. Owner decisions (2026-11-17)

| # | Question | Decision |
|---|---|---|
| D1 | A placed bet that breaks a rule (match not listed, odds below the minimum, both markets on one match, placed after kick-off) | **Recorded and flagged** (`rule_breaches`): the money is staked, so it counts; performance shows rule-following and rule-breaking bets separately |
| D2 | Can a placed bet be edited? | **No** — append-only; a mistake is cancelled (`POST /bets/{id}/cancel`, with a reason; it stays visible as `CANCELLED`) and recorded again |
| D3 | Stake currency | **TZS** for all bets (`config/betting.yaml`) |

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
| `lineup_status` | `NOT_CHECKED` (no re-check yet), `LINEUPS_PENDING`, `CONFIRMED`, **`WITHDRAWN` — never bet it** |
| `recheck` | the latest re-check: `status`, `checked_at`, `decision`, `failed_checks`, lineups stored, its probabilities and minimum odds |
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

## 9. Step 3 — odds check, real bets, performance (done)

### 9.1 `POST /matches/{id}/quotes` — the decision card

Body: `{"bookmaker": "…", "under05": {"yes": 3.30, "no": 1.40}, "draw": {"home": …, "draw": …, "away": …},
"store": true}` — at least one **complete** market (both sides of Under 0.5, all three of the 15' result: one
side alone cannot reveal the bookmaker's margin). The live quote check runs on the analysis at the session's
frozen cut-off (or now, as a preview), exactly as the paper bet does.

| Field | Meaning |
|---|---|
| `advice` | `BET` (listed and a quoted market passes), `NO_BET` (not listed, no market passes, or started), `WAIT` (report not published yet) |
| `reason` | one sentence, e.g. "bet UNDER05 at 1.40 or more" |
| `selected_market`, `selected_odds`, `ev` | the market to bet — the higher EV of the two, **never both** |
| `checks[]` | every check, including `QUOTE_*_PRICE` (offered vs minimum, the bookmaker's margin) and `QUOTE_*_BOOKMAKER_GAP` (BETTORY's P vs the bookmaker's de-vigged P) |
| `listed`, `report_decision`, `mode` | whether the match is on the published report; `FROZEN` or `PREVIEW` |

The prices are stored (append-only, source `MANUAL`, snapshot `BET_TIME`); a new bookmaker name is added to
`core.bookmakers`. Prices entered **before** a session's cut-off are information known at the cut-off and are
sealed with the freeze like any captured price.

### 9.2 Real bets

`POST /bets` `{"match_id", "market": "UNDER05" | "DRAW", "odds", "stake", "bookmaker", "placed_at"?, "note"?}`
→ `201` with the bet. Refused (`422`) only when it cannot be a real bet: `placed_at` in the future or without a
time zone, a stake above `max_stake` (typing-error guard), unknown fields. Everything else is recorded, with:

| Rule breach | When |
|---|---|
| `NOT_LISTED` | the match is not QUALIFIED on the session's published (frozen) report |
| `BELOW_MIN_ODDS` | odds taken below the frozen minimum odds of that market |
| `BOTH_MARKETS` | another active bet on the same match is on the other market |
| `AFTER_KICKOFF` | placed at or after kick-off |
| `WITHDRAWN` | placed after the re-check withdrew the match |

Each bet: `state` = `PENDING` / `WIN` / `LOSE` / `VOID` / `CANCELLED`, `profit` (in the currency), `result_15`,
`follows_rules`, the frozen `min_odds`, `grade`, `prediction_id`. The settlement is **not stored**: it is read from
the match's stored 15' result on every request, so a corrected score shows at once (same rules as the paper
settlement: unknown → PENDING, never assumed 0-0; not played → VOID).

Database (`supabase/migrations/20261118120000_placed_bets.sql`, schema `betting`): `placed_bets` and
`bet_cancellations`, both append-only (UPDATE / DELETE / TRUNCATE refused), recording time from the database
clock, a `placed_at` later than the database clock refused, RLS on, no access for anon / authenticated.
Applied to the local database only; **Supabase needs the owner's approval** like the paper migration.

### 9.3 `GET /performance?from=&to=`

`overall` and `by_market`, `by_competition`, `by_grade`, `by_rules` (`FOLLOWED` / `BROKEN`): settled, wins, voids,
pending, staked, profit, `roi` with `roi_interval` (bootstrap over whole sessions, `reliable` from 30 sessions),
`hit_rate` vs `break_even`; `profit_curve` (cumulative, by kick-off). Cancelled bets are excluded and counted.
CLV: `clv_n`, `clv_mean`, `clv_beat` per group (PAPER-BETTING.md §11).

### 9.4 Code

| File | Role |
|---|---|
| [app/api/betting.py](../backend/app/api/betting.py) | `quote_card`, `record_bet` (rule breaches), `bet_json` (settlement on read), `performance` |
| [app/api/routers/bets.py](../backend/app/api/routers/bets.py) | the endpoints |
| [app/storage/repositories/betting.py](../backend/app/storage/repositories/betting.py) | bets, cancellations, bookmakers, manual odds |
| [app/domain/betting_config.py](../backend/app/domain/betting_config.py), [config/betting.yaml](../backend/config/betting.yaml) | currency TZS, `max_stake` |
| [tests/integration/test_api_bets.py](../backend/tests/integration/test_api_bets.py) | decision card, stored prices, every breach, cancellation, append-only, settlement, performance, refused input |

## 10. Step 4 — the typed contract (done)

### 10.1 Typed responses

Every frontend endpoint declares its response model ([app/api/responses.py](../backend/app/api/responses.py)).
FastAPI validates each response against it (unknown fields are an error), so a change in what an endpoint
returns fails the tests instead of breaking the frontend silently. Three responses stay open JSON objects on
purpose: `GET /matches/{id}/analysis` (every model's internals), `GET /settings` (the whole params file) and
`GET /paper/report` (structure in PAPER-BETTING.md §7). All times are ISO 8601 UTC ending in `Z`.

### 10.2 The schema for the frontend

`python -m app.cli openapi` writes [docs/api/openapi.json](api/openapi.json) (45 paths). A test fails when the
committed file is out of date, when an endpoint listed in §3 is not served, or when a frontend endpoint has no
response type. In the frontend (Phase 6):

```
npx openapi-typescript ../docs/api/openapi.json -o src/lib/api-types.ts
```

gives the TypeScript types of every request and response; `src/lib/api.ts` wraps `fetch` with them.

### 10.3 Connecting the frontend

| Topic | How |
|---|---|
| Start the API locally | `cd backend` → `fastapi dev app/main.py` (http://127.0.0.1:8000; interactive docs at `/docs` when `ENVIRONMENT=local`) |
| Login | Supabase Auth in the frontend (password + TOTP); send the session's access token as `Authorization: Bearer <token>`; the API accepts only `OWNER_USER_ID` with `aal2` |
| Local development without login | `AUTH_LOCAL_BYPASS=true` (only with `ENVIRONMENT=local`, only from this machine) |
| CORS | `CORS_ORIGINS` (default `http://localhost:5173`, the Vite dev server); methods GET and POST only |
| Polling | the Today screen and the recorder status are cheap: poll every 30–60 s; a session report with a preview recomputes the session (≈ 0.5 s), so refresh it on demand |

### 10.4 Screen → endpoints (quick reference)

| Screen | Load | Actions |
|---|---|---|
| Today | `GET /dashboard` | — |
| Bets section | `GET /sessions`, `GET /sessions/{id}` | open analysis, odds check, record bet |
| All fixtures | `GET /sessions/{id}/fixtures` | — |
| Match analysis | `GET /matches/{id}`, `GET /matches/{id}/analysis`, `GET /matches/{id}/odds` | — |
| Odds check | — | `POST /matches/{id}/quotes` |
| Record a bet | `GET /bookmakers` | `POST /bets` |
| My bets | `GET /bets` | `POST /bets/{id}/cancel` |
| Performance | `GET /performance` | — |
| Paper betting | `GET /paper/report`, `GET /paper/predictions`, `GET /paper/chain` | `POST /paper/replay/{session}` |
| Recorder | `GET /paper/status` | `POST /paper/cycle` |
| Calendar | `GET /fixtures?date=` | — |
| Data health | `GET /data-health…` | `POST /data-health/crosschecks…` |
| Data loading, team review | `GET /jobs/runs`, `GET /reviews`, `GET /teams` | `POST /jobs/ingest/…`, `POST /reviews/{id}/…` |
| Settings | `GET /settings`, `GET /me` | — |

### 10.5 Not built yet (later Phase 5 tasks)

Nothing from the original list: the re-check, Telegram and CLV are built (§11). The `betting` migration, like the paper migration, still needs the owner's approval for Supabase.

## 11. Additions after step 4

- **Re-check (2026-11-17):** `lineup_status` / `recheck` on report entries, `withdrawn` on sessions, `lineups` on
  `GET /matches/{id}` (each team: formation, players with `starter`), the `WITHDRAWN` rule breach, NO_BET in the
  odds check for a withdrawn match.
- **Closing odds and CLV (2026-11-17):** each bet's `clv` (`fair_closing_p`, `fair_closing_odds`, `books`,
  `book_closing_odds`, `clv_fair`, `clv_book`) once its match has kicked off; `clv_n`, `clv_mean`, `clv_beat` in
  every performance group; the paper report's bets carry the same three figures.
- **Telegram (2026-11-17):** `GET /notifications` (outbox, latest first: kind, message, status `PENDING` /
  `SENT` / `FAILED` / `EXPIRED`), `POST /notifications/test` (409 when not configured), `telegram` in
  `GET /paper/status`; `GET /me` queues one login alert per Supabase login session (the frontend calls it right
  after login).
