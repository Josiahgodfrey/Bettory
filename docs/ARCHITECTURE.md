# BETTORY — System Architecture

**Architecture specification · v1.0 · 2026-11-14**

Companion to [SYSTEM-DOCUMENTATION.md](SYSTEM-DOCUMENTATION.md), [DATA-SOURCES-AND-MARKET-MODEL.md](DATA-SOURCES-AND-MARKET-MODEL.md) and [FIFTEEN-MINUTE-ODDS-CALCULATION.md](FIFTEEN-MINUTE-ODDS-CALCULATION.md).

---

## Table of contents

1. [Technology decisions](#1-technology-decisions)
2. [Architecture principles (strict logic)](#2-architecture-principles-strict-logic)
3. [System overview](#3-system-overview)
4. [Component responsibilities](#4-component-responsibilities)
5. [Where the Python backend runs](#5-where-the-python-backend-runs)
6. [Database design on Supabase](#6-database-design-on-supabase)
7. [Authentication and security](#7-authentication-and-security)
8. [Key flows](#8-key-flows)
9. [Python backend structure](#9-python-backend-structure)
10. [React frontend structure](#10-react-frontend-structure)
11. [Strictness mechanisms](#11-strictness-mechanisms)
12. [Sessions and scheduling](#12-sessions-and-scheduling)
13. [Environments, configuration and secrets](#13-environments-configuration-and-secrets)
14. [Testing and CI](#14-testing-and-ci)
15. [Free-tier limits and capacity](#15-free-tier-limits-and-capacity)
16. [Decisions and open items](#16-decisions-and-open-items)

---

## 1. Technology decisions

| Layer | Technology | Hosted on |
|---|---|---|
| Frontend | **React** + TypeScript (Vite) | **Vercel** |
| Database | **PostgreSQL** | **Supabase** |
| Authentication | **Supabase Auth** | Supabase |
| File storage (raw downloads) | **Supabase Storage** | Supabase |
| Backend logic (ingestion, calculation, pricing, decision, settlement, backtest) | **Python** (FastAPI + worker) | Container host — see §5 |

---

## 2. Architecture principles (strict logic)

| # | Principle | Rule |
|---|---|---|
| R1 | **One calculation engine** | All maths lives in Python. Live analysis and backtesting run the same code. |
| R2 | **No maths in the frontend** | React only displays numbers produced by Python and collects input. It never calculates probabilities, odds or EV. |
| R3 | **Pure calculation functions** | The backend is built with **FastAPI**. Inside it, the calculation package consists of *pure functions* (no side effects): no network or database access, same inputs → same outputs. FastAPI endpoints and the worker **call** these functions. "Pure" describes the functions, not "Python without a framework". |
| R4 | **Point-in-time** | Every data read for a fixture takes `as_of` (kick-off). The repository layer refuses data after it. |
| R5 | **Never guess** | Missing data gets an explicit status. Uncertainty → SKIP. |
| R6 | **Validate at every boundary** | API input, provider data, odds entry and calculation outputs are validated; failure stops processing for that fixture. |
| R7 | **Immutable raw data, append-only results** | Raw files and decisions are never edited; corrections are new records. |
| R8 | **Versioned everything** | Each result stores `formula_version`, `params_version` and an input hash. |
| R9 | **Explainable decisions** | Every BET/SKIP stores its passed and failed checks with values. |
| R10 | **Exact numbers for money** | Odds, stakes and profit use `NUMERIC` (DB) and `Decimal` (Python). Probabilities use float64. Rounding only for display. |
| R11 | **Single schema owner** | Database schema is defined only in Supabase SQL migrations. |
| R12 | **Least privilege** | The frontend never holds privileged keys; it can only read approved views and call the Python API. |

---

## 3. System overview

```
                          ┌──────────────────────────┐
                          │   React app (Vercel)     │
                          │  dashboard · odds entry  │
                          │  performance · settings  │
                          └───────┬──────────┬───────┘
                 login / read     │          │  odds entry, actions
                 approved views   │          │  (JWT in header)
                                  ▼          ▼
┌─────────────────────────────────────┐   ┌──────────────────────────────┐
│ SUPABASE                            │   │ PYTHON BACKEND (container)   │
│                                     │   │                              │
│  Auth (users, JWT)                  │◄──┤  API (FastAPI)               │
│  PostgreSQL                         │   │   - verifies Supabase JWT    │
│   raw · core · calc · output · ops  │◄──┤   - validates input          │
│   api (views exposed to frontend)   │   │   - runs pricing + decision  │
│  Storage (raw payload files)        │◄──┤  Worker (scheduler)          │
│  Realtime (decision updates)        │   │   - ingestion, normalisation │
└─────────────────────────────────────┘   │   - calculation, settlement  │
                                          │   - backtests                │
                                          └──────────────┬───────────────┘
                                                         │
                                          ┌──────────────▼───────────────┐
                                          │ External data sources        │
                                          │ API-Football · football-     │
                                          │ data.org · openfootball ·    │
                                          │ football-data.co.uk          │
                                          └──────────────────────────────┘
```

---

## 4. Component responsibilities

| Component | Does | Never does |
|---|---|---|
| **React (Vercel)** | Login, show daily calendar/candidates/decisions/performance, odds entry forms, settings | Calculations, direct writes to calculation tables, holding the service key |
| **Supabase Auth** | The login endpoint for the single owner: password, two-factor (TOTP), CAPTCHA, sessions, JWT | Allow sign-ups |
| **Supabase PostgreSQL** | Stores everything; enforces constraints, enums, foreign keys, append-only rules, row-level security | Business calculations (no maths in SQL functions beyond simple aggregation) |
| **Supabase Storage** | Raw provider payloads (gzip), CSV imports | — |
| **Python API** | Authenticated actions: submit odds, trigger re-price, settle manually, start backtest; returns results | Long-running jobs inside a request |
| **Python worker** | Scheduled jobs: ingestion, normalisation, calculation, lineup re-check, settlement, backtests | Serve the frontend |
| **Python calculation package** | All formulas: baselines, Models A / L / B v2 / C, pricing engine, EV | Any I/O |
| **Telegram bot** (sent by the Python worker) | Session-ready messages with a link to the Bets section; withdrawal alerts; job-failure alerts | Contain the full report or any odds data |

---

## 5. Where the Python backend runs

Supabase Edge Functions run TypeScript (Deno), not Python, so Python needs its own host. Vercel can run short Python functions, but the daily jobs and backtests run for minutes, which serverless functions are not designed for.

### Options

| Option | How | Cost | Pros | Cons |
|---|---|---|---|---|
| **A. One container service (recommended)** | FastAPI + scheduler in one Docker container on Render / Railway / Fly.io | ≈ $5–7 / month | One runtime, one codebase, no time limits, simplest to reason about | Small monthly cost |
| B. Free split | API as Vercel Python functions; jobs as GitHub Actions scheduled workflows | Free (within limits) | No cost | Two runtimes to keep identical; cold starts; package size and duration limits; Actions schedules can be delayed |
| C. Your Windows PC | Python service running locally | Free | No cost, full control | Only works while the PC is on; API not reachable from Vercel without a tunnel |

**Recommendation: Option A.** The same Docker image runs as:
- `bettory api` — FastAPI web process
- `bettory worker` — scheduler process (can be the same container on small plans)

---

## 6. Database design on Supabase

### 6.1 Schemas

| Schema | Contents | Exposed to frontend? | Write access |
|---|---|---|---|
| `raw` | Ingestion records (pointer to Storage file, source, hash, fetched_at) | ❌ | Python only |
| `core` | competitions, teams, provider ID maps, fixtures, historical_matches, match_goals, bookmakers | ❌ | Python only |
| `calc` | competition_baselines, team_statistics, h2h_statistics, fifteen_min_prices | ❌ | Python only |
| `output` | bookmaker_15min_quotes, odds_snapshots, verification_results, bets, bet_settlements | ❌ | Python only |
| `ops` | job_runs, ingestion_logs, params_versions, formula_versions, audit_log | ❌ | Python only |
| **`api`** | **Read-only views** for the frontend (daily calendar, candidates, decision cards, performance) | ✅ | none (views) |

Only the `api` schema is exposed through Supabase's REST interface. All writes go through the Python API, so validation exists in exactly one place.

### 6.2 Database-level strictness

- **Enums** for every status (`data_status`, `decision`, `grade`, `snapshot_type`, `competition_type`, …).
- **CHECK constraints**, e.g.:
  - `odds > 1.01 AND odds < 1000`
  - `probability > 0 AND probability < 1`
  - `overround >= 0 AND overround <= 0.15`
  - `home_goals_15 <= home_goals_ht <= home_goals_ft`
- **Foreign keys** everywhere; no orphan rows.
- **Unique keys** for idempotency, e.g. `(source_id, provider_fixture_id)`, `(fixture_id, team_id, category, params_version)`.
- **Append-only triggers** on `output` and `calc` tables: `UPDATE` / `DELETE` raise an error (corrections are new rows with `supersedes_id`).
- **Money columns** `NUMERIC(12,2)`, odds `NUMERIC(8,3)`, probabilities `DOUBLE PRECISION`.

### 6.3 Connections

| Client | Connection |
|---|---|
| Python worker (long-running) | Supabase pooler, **session mode** |
| Python API | Supabase pooler, **transaction mode** |
| React | Supabase JS client (anon key + user JWT), `api` schema only |

### 6.4 Migrations

- Schema is defined in `supabase/migrations/*.sql` (Supabase CLI) — the single source of truth.
- Python reads the schema; it does **not** create or alter tables.
- TypeScript types are generated from the database (`supabase gen types`), Python models are kept in sync and checked in CI.

---

## 7. Authentication and security

### 7.1 Access model: private, single user

BETTORY is a **private system with exactly one user (you)**. There is no registration, no other roles and no public content. Without valid credentials, nothing beyond the login page is reachable.

The login endpoint is **Supabase Auth** (password hashing, tokens, two-factor codes, rate limits). No custom login code is written in Python; the Python API only **verifies** Supabase tokens.

### 7.2 Security layers

| # | Layer | Implementation |
|---|---|---|
| S1 | **No sign-up** | Public sign-ups **disabled** in Supabase Auth settings. The single account is created manually in the Supabase dashboard. The frontend has **no registration page**. |
| S2 | **Password** | Strong, unique password (password manager recommended). Supabase minimum password length set to ≥ 14 characters. |
| S3 | **Two-factor (TOTP)** | Supabase MFA enabled. After the password, a 6-digit code from an authenticator app (Google Authenticator / Authy / Microsoft Authenticator) is required. |
| S4 | **Owner allow-list** | The owner's user ID is stored as `OWNER_USER_ID` (backend env var) and in `ops.app_owner` (database). Both the database and the API accept **only this user ID** — any other account, even with a valid token, gets nothing. |
| S5 | **Two-factor enforced server-side** | Row-level security policies and the Python API require the token's assurance level `aal2` (two-factor completed). Password-only sessions (`aal1`) are rejected. |
| S6 | **Brute-force protection** | Supabase Auth rate limits + **CAPTCHA** (Cloudflare Turnstile, free) on the login form, verified by Supabase. |
| S7 | **Session limits** | Short-lived access tokens (1 hour) with refresh-token rotation; forced re-login after **12 hours of inactivity** (configurable). |
| S8 | **All routes protected** | Every frontend route except `/login` redirects to login when there is no valid two-factor session. The Telegram link opens login first if needed. |
| S9 | **Login alerts** | Every successful login sends a Telegram message: "🔐 New login to BETTORY at 01:12 EAT". |

### 7.3 Login flow

```
1. Open the app / Telegram link        → redirected to /login
2. Email + password + CAPTCHA          → Supabase verifies password            (session = aal1)
3. 6-digit authenticator code          → Supabase verifies TOTP                (session = aal2)
4. App loads the Bets section          → database + API check:
                                          user_id == OWNER_USER_ID  AND  aal == aal2   ✅
   Any other user, or password only    → rejected                               ❌
```

### 7.4 Database rules (row-level security)

Every table read through the `api` views has a policy equivalent to:

```sql
auth.uid() = (select owner_id from ops.app_owner)
and (auth.jwt() ->> 'aal') = 'aal2'
```

- The `anon` role has **no** read access to anything.
- Writes from the frontend are not allowed at all (all writes go through the Python API).

### 7.5 Python API rules

Every request must carry `Authorization: Bearer <JWT>`. The API:

1. Verifies the JWT signature against Supabase's signing keys, plus expiry and audience.
2. Checks `sub == OWNER_USER_ID`.
3. Checks `aal == "aal2"`.
4. Otherwise returns **401/403** and records the attempt in `ops.audit_log`.

Additional rules:
- CORS accepts only the Vercel production domain.
- Write endpoints are rate-limited.
- The Python backend uses the **service role key**, stored only on the backend host — never in the frontend or the repository.
- External API keys (API-Football, etc.) live only on the backend host.
- Every write action is recorded in `ops.audit_log` (what, when, before/after).

### 7.6 Recovery

| Situation | Recovery |
|---|---|
| Forgot password | Supabase password-reset link sent to the owner's email only |
| Lost phone / authenticator | Use the backup method saved at MFA setup; last resort: remove the MFA factor in the Supabase dashboard and enrol again |
| Suspected compromise | Change password, revoke all sessions in Supabase, rotate service role key and API keys |

### 7.7 Protecting the accounts behind the system

The real keys to BETTORY are the provider accounts. **Enable two-factor on all of them:**

- Supabase
- Vercel
- GitHub
- Backend host (Render / Railway / Fly.io)
- Email account used for login and password reset
- Telegram account

---

## 8. Key flows

### 8.1 Session pipeline (worker)

Runs twice a day, once per session (§12). Times are set by the planner.

```
run_start         ingest      → all results finished since the last run + the session's fixtures
                  normalise   → standard match model, ID mapping, 15' recovery, data_status
                  calculate   → baselines, team stats, H2H, Models A/L/B v2 (as_of = kick-off)
                  price       → 15' pricing engine; Model C if market odds are available
                  verify      → Layers 0–5 → QUALIFIED / SKIP with reasons
                  rank & cap  → at most 50 QUALIFIED matches in the session report
ready_by          publish     → session report in the Bets section + Telegram message
```

### 8.2 Odds entry and pricing (API, synchronous)

> **Optional since A14.** Session reports are produced without manual odds (minimum-odds report). This flow is used to **record placed bets** (odds taken, stake) and, when you choose, to enter extra market odds to re-price a match. Recording a placed bet is required for settlement and performance tracking.

```
React form ──POST /fixtures/{id}/quotes──► Python API
   (both sides of 15' market,               1. verify JWT + role
    HT/FT totals, bookmaker)                2. validate input (odds range, overround 0–15%)
                                            3. store quote (append-only)
                                            4. Model C → blend μ → λ15 → grid → fair odds
                                            5. de-vig bookmaker, EV, Layers 3–5
                                            6. store verification_result with reasons
                                 ◄────────  7. return decision card
React shows decision; Realtime pushes any later change
```

### 8.3 Lineup re-check (worker, ~60 min before kick-off)

Fetch lineups → re-run red flags → a BET may become SKIP (**never SKIP → BET**) → new verification_result row superseding the previous one.

### 8.4 Settlement (worker, after full time)

Fetch result → derive actual 15' score → settle bets (WIN / LOSE / VOID) → profit/loss → CLV from closing quote → performance views refresh.

### 8.5 Backtest (worker, on demand)

```
for each day D in range:
    as_of = D
    run calculate → screen → price (Model C from historical odds / estimates) → decide
    settle with known results
store run under ops.job_runs with params_version → performance report
```

The backtest calls **the same functions** as the live pipeline; only the data source of odds differs.

---

## 9. Python backend structure

```
backend/
├── pyproject.toml
├── Dockerfile
├── bettory/
│   ├── config/            # params_v*.yaml, bookmaker rules, settings loader
│   ├── domain/            # Pydantic models + enums (Fixture, Match, Goal, Quote, Decision…)
│   ├── ingestion/         # one adapter per source; returns raw payloads only
│   │   ├── api_football.py
│   │   ├── football_data_org.py
│   │   ├── openfootball.py
│   │   └── football_data_co_uk.py
│   ├── normalisation/     # raw → domain models, ID mapping, 15' recovery, data_status
│   ├── storage/           # repositories; every read takes as_of and enforces it
│   ├── calculation/       # PURE: baselines, team_stats, models (a, l, b2, c), pricing, devig, ev
│   ├── decision/          # rule layers 0–5, grades, reason builder
│   ├── settlement/        # results, profit/loss, CLV, performance
│   ├── backtest/          # replay runner
│   ├── api/               # FastAPI routers, JWT verification, schemas
│   ├── jobs/              # scheduler and job definitions
│   └── cli.py             # `bettory api`, `bettory worker`, `bettory backtest`
└── tests/
    ├── unit/
    ├── golden/            # worked examples from the docs
    ├── property/          # Hypothesis invariants
    └── integration/       # against local Supabase
```

**Import rule (enforced in CI):** `calculation` may import only `domain`. It may not import `storage`, `ingestion`, `api` or any network library.

---

## 10. React frontend structure

```
frontend/
├── src/
│   ├── lib/supabase.ts        # client (anon key), generated DB types
│   ├── lib/api.ts             # typed client for the Python API
│   ├── features/
│   │   ├── auth/              # /login (password + CAPTCHA + TOTP), route guard; no sign-up page
│   │   ├── calendar/          # daily match calendar
│   │   ├── candidates/        # candidates awaiting odds
│   │   ├── odds-entry/        # validated forms (both sides required)
│   │   ├── decisions/         # decision cards with reasons and numbers
│   │   ├── performance/       # ROI, CLV, calibration charts
│   │   └── settings/          # bookmakers, leagues, parameters (read-only display)
│   └── components/
└── vercel.json
```

Rules:
- TypeScript strict mode.
- Data fetching via TanStack Query; types generated from Supabase.
- Form validation mirrors backend rules for user convenience, but **the backend is the authority**.
- Numbers are displayed with their sample size and version (e.g. `73.2% (n=10) · v1.4`).

---

## 11. Strictness mechanisms

| Mechanism | Where |
|---|---|
| Enums and CHECK constraints | Database |
| Pydantic models with validators | Python boundaries |
| Invariant checks on every calculation (grid sums to 1 ± 0.001; 0 < P < 1; λ ≥ 0; fair odds > 1) | Calculation package |
| Fail closed: any exception → fixture `ERROR`, never BET | Decision layer |
| Idempotent jobs (unique keys + upserts on raw/core only) | Worker |
| Append-only calc/output tables (triggers) | Database |
| Input hash + versions stored with every result | Calculation/decision |
| One-way downgrade rule after odds entry | Decision layer |
| Point-in-time repository | Storage |
| Import-boundary check | CI |
| Golden tests from the documentation | CI |

---

## 12. Sessions and scheduling

### 12.1 Timezone

- User timezone: **East Africa Time (EAT) = UTC+3**, no daylight-saving changes.
- The database stores all times in **UTC** (`TIMESTAMPTZ`). Session logic and the frontend use **EAT**.
- EAT ↔ UTC: subtract 3 hours (e.g. 01:00 EAT = 22:00 UTC the previous day).

### 12.2 The two sessions

Every match belongs to exactly one session, by its kick-off time in EAT. Together they cover all 24 hours.

| | **Night session** of date D | **Noon session** of date D |
|---|---|---|
| Kick-offs covered (EAT) | D 01:00 – D 12:59 | D 13:00 – D+1 00:59 |
| Kick-offs covered (UTC) | D−1 22:00 – D 09:59 | D 10:00 – D 21:59 |
| You place bets (EAT) | 01:00 – 10:00 | 13:00 – 22:00 |
| Report deadline (EAT) | before **01:00** | before **13:00** |
| Report size | **at most 50** qualified matches | **at most 50** qualified matches |

Session ID format: `2026-11-15-NIGHT`, `2026-11-15-NOON`.

### 12.3 When each session is analysed

Two rules must both hold: the report is ready **before the session deadline**, and **at least 2 hours before the first kick-off** in the session.

```
first_ko   = earliest kick-off among the session's eligible fixtures
ready_by   = min( session deadline , first_ko − 2h )
run_start  = ready_by − 45 min        (ingestion + analysis + safety buffer; configurable)
```

| First kick-off in Night session | ready_by (EAT) | run_start (EAT) |
|---|---|---|
| 01:00 | 23:00 (previous evening) | 22:15 |
| 02:30 | 00:30 | 23:45 |
| 05:00 or later | 01:00 | 00:15 |

| First kick-off in Noon session | ready_by (EAT) | run_start (EAT) |
|---|---|---|
| 13:00 | 11:00 | 10:15 |
| 14:30 | 12:30 | 11:45 |
| 15:00 or later | 13:00 | 12:15 |

If a session has **no eligible fixtures**, no run is scheduled and Telegram sends "no matches in this session".

### 12.4 The planner

A planner job books each session run in advance:

| Planner run (EAT) | Plans |
|---|---|
| **19:00** daily | Night session of the next date (earliest possible run_start 22:15) |
| **07:00** daily | Noon session of the same date (earliest possible run_start 10:15) |

The planner:
1. Fetches the session's fixtures (1–2 API requests).
2. Finds `first_ko`, calculates `ready_by` and `run_start`.
3. Stores the plan in `ops.session_plans` (session_id, first_ko, ready_by, run_start, fixture_count, status).
4. Books the session run in the scheduler.

At `run_start`, the run **re-fetches the fixture list** (postponements, kick-off changes, late additions). If the first kick-off moved earlier so that `ready_by` would be missed, the run continues immediately and the report is flagged `LATE` in Telegram.

### 12.5 Ranking and the 50-match cap

- **Only matches that pass every verification layer** appear in the report. All others are stored as SKIP with reasons (visible in the full analysis view, never in the report).
- **The cap is a maximum, never a target.** If 7 matches pass, the report has 7. Standards are never lowered to fill the list.
- If more than 50 pass, they are ranked and the top 50 are kept:

| Order | Ranking key |
|---|---|
| 1 | Grade (A before B) |
| 2 | Edge, when market odds are available (EV); otherwise P(15' Under 0.5), higher first |
| 3 | Smaller gap between models (more agreement first) |
| 4 | Larger complete sample size |

Matches beyond 50 are stored as `QUALIFIED_OVER_CAP` and are not shown in the report.

### 12.6 What the report contains

Each entry in the Bets section for a session shows:

| Field | Example |
|---|---|
| Kick-off (EAT), league, teams | 03:30 · League X · Team A vs Team B |
| P(15' Under 0.5) and P(15' Draw) | 77.8% · 78.6% |
| Fair odds | 1.29 · 1.27 |
| **Minimum odds to bet** | **Under 0.5 ≥ 1.35 · Draw ≥ 1.34** |
| Grade, sample sizes | B · n = 10 / 10 |
| Lineup status | Not yet confirmed / Confirmed / Withdrawn |
| Link to full reasons | passed checks with values |

**How you use it:** on your betting platform, bet a listed match **only if the offered odds are at or above the minimum odds**. Choose **one** market per match: the one where *offered odds ÷ minimum odds* is higher. Never bet both.

### 12.7 After the report: lineup re-check and withdrawals

| Job | When | Effect |
|---|---|---|
| `lineup_check` | Every 15 min, for fixtures 45–75 min before kick-off | Re-runs red flags with confirmed lineups |

A listed match can only be **withdrawn** (QUALIFIED → WITHDRAWN), never added. A withdrawal sends an immediate Telegram alert. Because the Night betting window ends at 10:00, many bets are placed before lineups exist — the report shows "lineups not yet confirmed" for those.

### 12.8 Telegram notifications

| Event | Message |
|---|---|
| Session report ready | "🌙 Night session 15 Nov — 23 matches verified (first kick-off 03:30 EAT). Open: `<app URL>/bets?session=2026-11-15-NIGHT`" |
| No matches qualified | "Night session 15 Nov — 0 matches passed verification." |
| Match withdrawn | "⚠️ Withdrawn: Team A vs Team B (03:30) — reason: key striker in lineup / odds movement." |
| Run late or failed | "❗ Noon session run FAILED at step `normalise` — retrying (2/3)." |

- Messages contain **only a summary and a link**; the link opens the Bets section, which requires login.
- Bot token and chat ID live only on the backend host.

### 12.9 Full job schedule

| Job | Schedule (EAT) | Notes |
|---|---|---|
| `plan_noon` | 07:00 daily | Books the Noon session run |
| `plan_night` | 19:00 daily | Books the next Night session run |
| `session_run` | Booked by planner (10:15–12:15 / 22:15–00:15) | Full pipeline §8.1 |
| `lineup_check` | Every 15 min | Fixtures 45–75 min before kick-off |
| `settle` | Every 30 min | Fixtures finished ≥ 15 min ago |
| `backfill` | 04:00 | Leftover API quota for new leagues/seasons |
| `refresh_performance` | 05:00 | Performance views |

Each run writes `ops.job_runs` (start, end, status, counts, errors). A failed job is retried up to 3 times, then marked `FAILED` and reported on Telegram and the dashboard.

### 12.10 Points still to verify

- Whether API-Football's pre-match **odds endpoint** covers HT / FT totals (and possibly 15-minute markets) on the free plan. If yes, Model C and EV ranking become automatic; if not, the report uses minimum odds only.
- API quota per session run: fixtures (1–2) + results since last run (bulk, ~5–10) + odds (if used, paged) — must stay within 100/day together with lineup checks.

---

## 13. Environments, configuration and secrets

| Environment | Database | Backend | Frontend |
|---|---|---|---|
| **Local** | Supabase CLI (local Docker) | `bettory api` / `worker` locally | Vite dev server |
| **Production** | Supabase project | Container host | Vercel |

| Secret | Lives in |
|---|---|
| Supabase URL, anon key | Vercel env vars (public by design) |
| Python API URL | Vercel env vars |
| Supabase service role key, DB connection string | Backend host env vars only |
| API-Football and other provider keys | Backend host env vars only |
| Telegram bot token, chat ID | Backend host env vars only |
| `OWNER_USER_ID` | Backend host env vars (also stored in `ops.app_owner`) |
| Turnstile site key | Vercel env vars (public by design) |
| Turnstile secret key | Supabase Auth settings only |

Parameters (weights, thresholds) live in `config/params_vX.Y.yaml`, committed to the repository and registered in `ops.params_versions` when first used.

---

## 14. Testing and CI

| Test type | Example |
|---|---|
| **Golden** | 15' worked example: λ15 = 0.3125 → P(0-0) = 0.7316 → fair 1.367; power de-vig k ≈ 1.154 |
| **Property** | P(0-0) decreases as λ increases; grid sums to 1; de-vigged probabilities sum to 1 |
| **Unit** | 15' recovery decision table, every row |
| **Point-in-time** | Repository refuses a match dated after `as_of` |
| **Integration** | Full daily pipeline against local Supabase with fixture data |
| **Backtest regression** | A fixed historical sample must reproduce stored results for a given version |

GitHub Actions on every push: `ruff` + `mypy --strict` (Python), `tsc --noEmit` + lint (React), tests, migration check, import-boundary check. Vercel deploys the frontend from the main branch; the backend image deploys after tests pass.

---

## 15. Free-tier limits and capacity

| Item | Estimate / limit | Impact |
|---|---|---|
| Supabase free DB (500 MB) | ~25 leagues × 3 seasons ≈ 26,000 matches + ~75,000 goals → well under 100 MB | Fits; raw payloads go to Storage |
| Supabase free Storage (1 GB) | Gzipped JSON payloads | Prune or archive older raw files if needed |
| Supabase free project pausing | Free projects pause after a period of inactivity | Daily worker activity keeps it active; move to Pro if it pauses |
| API-Football free (100 requests/day) | See Data doc §3 | Bulk requests + local database |
| Vercel Hobby | Static React app | Fits easily |

Limits change — confirm current values on each provider's pricing page.

---

## 16. Decisions and open items

### Decisions

| # | Decision |
|---|---|
| A1 | Frontend: React + TypeScript, hosted on Vercel |
| A2 | Database, authentication and storage: Supabase (PostgreSQL) |
| A3 | All business logic in Python; no maths in the frontend or SQL |
| A4 | Only the `api` schema (read-only views) is exposed to the frontend; all writes go through the Python API |
| A5 | Schema owned by Supabase SQL migrations |
| A6 | Calculation package is pure and shared by live and backtest |
| A7 | Append-only results with versions and input hashes |
| A8 | **Private single-user system**: sign-ups disabled, password + two-factor (TOTP), owner allow-list, `aal2` enforced in database and API, CAPTCHA, login alerts (§7) |
| A9 | Python host: **Option A** — one container (API + worker) on Render / Railway / Fly.io |
| A10 | Timezone: **EAT (UTC+3)**; database in UTC |
| A11 | Two sessions: **Night 01:00–12:59 EAT**, **Noon 13:00–00:59 EAT** |
| A12 | Report ready by `min(session deadline, first kick-off − 2h)`; times booked by a planner |
| A13 | Report lists **only matches that passed verification**, **at most 50 per session**; standards never lowered |
| A14 | Report gives minimum odds per market; you bet only when offered odds ≥ minimum, one market per match |
| A15 | Delivery: **Bets section** of the app + **Telegram** message with a link |
| A16 | After publication, matches can only be withdrawn, never added |

### Open items

| # | Question | Recommendation |
|---|---|---|
| O1 | ~~Python host~~ | Decided — A9 |
| O2 | ~~Single user or viewers too?~~ | Decided — A8 (single owner only) |
| O3 | Repository layout | One monorepo: `frontend/`, `backend/`, `supabase/`, `docs/` |
| O4 | Formula version to implement first | v1.4 (μ-level blend from the pricing doc) |
