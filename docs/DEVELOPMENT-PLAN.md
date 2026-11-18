# BETTORY — Development Plan

**Development phase planning · v1.0 · 2026-11-14**

Companion to [SYSTEM-DOCUMENTATION.md](SYSTEM-DOCUMENTATION.md), [DATA-SOURCES-AND-MARKET-MODEL.md](DATA-SOURCES-AND-MARKET-MODEL.md), [FIFTEEN-MINUTE-ODDS-CALCULATION.md](FIFTEEN-MINUTE-ODDS-CALCULATION.md) and [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Table of contents

1. [Approach](#1-approach)
2. [Phase overview](#2-phase-overview)
3. [Prerequisites](#3-prerequisites)
4. [Phase 0 — Verify assumptions](#phase-0--verify-assumptions)
5. [Phase 1 — Foundation](#phase-1--foundation)
6. [Phase 2 — Calculation engine](#phase-2--calculation-engine)
7. [Phase 3 — Data pipeline](#phase-3--data-pipeline)
8. [Phase 4 — Backtest (gate)](#phase-4--backtest-gate)
9. [Phase 5 — Session pipeline and FastAPI](#phase-5--session-pipeline-and-fastapi)
10. [Phase 6 — Frontend](#phase-6--frontend)
11. [Phase 7 — Deployment and careful go-live](#phase-7--deployment-and-careful-go-live)
12. [Working rules](#12-working-rules)
13. [Definition of done](#13-definition-of-done)
14. [Risks](#14-risks)
15. [Progress tracker](#15-progress-tracker)

---

## 1. Approach

| Rule | Meaning |
|---|---|
| **Numbers and data first, screens last** | The formula and the data decide whether BETTORY works. They are built and proven before the frontend. |
| **One league end-to-end first** | Every phase is first completed for 1–2 leagues, then extended. Problems appear early and cheaply. |
| **Gates** | Phase 0 and Phase 4 are decision gates. If a gate fails, the design or formula is fixed before continuing. |
| **Documentation is the specification** | Code follows the docs. When the code needs a change of design, the docs are updated first. |
| **Worked examples are tests** | Every worked example in the docs becomes an automated test. |

---

## 2. Phase overview

```
Phase 0  Verify assumptions ──► GATE A: design confirmed / adjusted
   │
Phase 1  Foundation (repo, Supabase, migrations, FastAPI skeleton, CI)
   │
Phase 2  Calculation engine (pure functions + golden tests)   ← can start in parallel with Phase 1
   │
Phase 3  Data pipeline (1–2 leagues → more)
   │
Phase 4  Backtest ──► GATE B: formula proven / fixed
   │
Phase 5  Session pipeline + FastAPI endpoints + Telegram
   │
Phase 6  Frontend (login + Bets section + performance)
   │
Phase 7  Deployment → paper betting → small real stakes
```

| Phase | Name | Rough duration* | Depends on |
|---|---|---|---|
| 0 | Verify assumptions | 2–3 days | Accounts (§3) |
| 1 | Foundation | ~1 week | Phase 0 findings |
| 2 | Calculation engine | 1–2 weeks | Docs only (parallel with 1) |
| 3 | Data pipeline | 1–2 weeks | 1, 2 |
| 4 | Backtest | 1–2 weeks | 2, 3 |
| 5 | Session pipeline + API | 1–2 weeks | Gate B passed |
| 6 | Frontend | 1–2 weeks | 5 |
| 7 | Deployment + paper betting | 2–4 weeks | 6 |

\* Estimates for one developer working with assistance; real speed will be measured and the plan updated.

---

## 3. Prerequisites

### 3.1 Accounts (all with two-factor enabled)

| Account | Needed from | Purpose |
|---|---|---|
| GitHub (private repository) | Phase 1 | Code, CI |
| API-Football (free) | Phase 0 | Data API key |
| football-data.org (free) | Phase 0 | Backup data API key |
| Supabase | Phase 1 | Database, auth, storage |
| Telegram bot (via @BotFather) | Phase 5 | Notifications |
| Cloudflare Turnstile | Phase 6 | Login CAPTCHA |
| Vercel | Phase 6 | Frontend hosting |
| Render / Railway / Fly.io | Phase 7 | Backend container |

### 3.2 Tools on the development PC

| Tool | Version |
|---|---|
| Python | 3.12+ |
| Node.js | 20 LTS+ |
| Docker Desktop | current (needed for local Supabase) |
| Supabase CLI | current |
| Git | current |

### 3.3 Secrets hygiene (before any key is created)

- `.env` files are **never committed**. A `.gitignore` (with the leading dot) must list `.env`, `.env.*` (except `.env.example`).
- A committed `.env.example` lists variable **names only**.
- Any key that was ever committed or pushed is treated as leaked and **rotated**.

---

## Phase 0 — Verify assumptions

**Goal:** confirm the free data sources behave as the design assumes, before building on them.

### Tasks

| # | Task | Checks item |
|---|---|---|
| 0.1 | Register API-Football free key; request a past season's fixtures | V1 — which seasons the free plan unlocks |
| 0.2 | Request 20 fixtures by `ids` in one call; confirm events with goal minutes are included | V2 |
| 0.3 | Call API-Football **odds** endpoint for one date; list available markets (HT/FT totals, 15-minute markets?) | Architecture §12.10 |
| 0.4 | football-data.org: fetch one match; confirm `goals[]` with `minute`/`injuryTime` on free tier | V3 |
| 0.5 | Download sample openfootball JSON; record field names | V4 |
| 0.6 | Download sample football-data.co.uk CSV; confirm HT columns and Pinnacle closing O/U 2.5 columns | V5 |
| 0.7 | Read your bookmaker's 15-minute market rules (cutoff, stoppage, own goals, void) | V6 |
| 0.8 | List leagues where your bookmaker offers 15-minute markets | V8 |
| 0.9 | Compare goal-minute labels against live text for a few matches (is "15'" = 14:00–14:59?) | V10 |
| 0.10 | Check API-Football competition list for cups / continental competitions on free plan | V11 |

### Deliverable

`docs/PHASE-0-FINDINGS.md` — results of every check, with the design changes they cause.

### Gate A — exit criteria

- ✅ At least one free source gives goal minutes for the target leagues.
- ✅ Daily API budget (Data doc §3.3) confirmed feasible.
- ✅ Odds decision made: **automatic Model C** (odds endpoint usable) or **minimum-odds report only** (with stricter EV threshold).
- ✅ Bookmaker 15' settlement rule recorded.
- If any check fails → update the design docs before Phase 1 continues.

---

## Phase 1 — Foundation

**Goal:** a working skeleton where every later piece has a place to live.

### Tasks

| # | Task |
|---|---|
| 1.1 | Repository layout: `backend/`, `frontend/`, `supabase/`, `docs/`; `.gitignore`, `.env.example`, README |
| 1.2 | Supabase project (production) + Supabase CLI local stack (Docker) |
| 1.3 | **Database migrations** — schemas `raw`, `core`, `calc`, `output`, `ops`, `api`; all tables from the docs; enums; CHECK constraints; foreign keys; unique keys; append-only triggers |
| 1.4 | Auth configuration: sign-ups disabled, MFA (TOTP) enabled, owner account created, `ops.app_owner`, RLS policies (owner + `aal2`) |
| 1.5 | Python project: `pyproject.toml`, package layout (Architecture §9), settings loader, Pydantic domain models and enums |
| 1.6 | FastAPI skeleton: `/health`, JWT verification dependency (owner + `aal2`), CORS, error format |
| 1.7 | Parameter file `config/params_v1.4.yaml`; registration in `ops.params_versions` |
| 1.8 | CI (GitHub Actions): ruff, mypy --strict, pytest, migration check, import-boundary check |

### Deliverables

- Local Supabase with full schema; same migrations applied to the Supabase project.
- `bettory api` starts and `/health` responds; protected test endpoint rejects anyone but the owner with two-factor.
- CI green.

### Exit criteria

- ✅ Every table, enum and constraint in the docs exists in a migration.
- ✅ Tests prove: an invalid row (e.g. odds = 0.9, probability = 1.2) is **rejected** by the database.
- ✅ Tests prove: UPDATE/DELETE on append-only tables fails.
- ✅ Tests prove: a non-owner token and an `aal1` token are rejected by RLS and by FastAPI.

---

## Phase 2 — Calculation engine

**Goal:** every formula implemented as pure functions and proven by tests. Can start in parallel with Phase 1.

### Tasks

| # | Module | Content |
|---|---|---|
| 2.1 | `calculation/poisson.py` | Poisson pmf/cdf, score grid, bisection solver |
| 2.2 | `calculation/devig.py` | Multiplicative, power, (Shin optional); overround |
| 2.3 | `calculation/baselines.py` | Competition averages, early share `s` and `r` with shrinkage, λ15 per competition |
| 2.4 | `calculation/recovery.py` | 15' score recovery decision table and data statuses |
| 2.5 | `calculation/team_stats.py` | Rates per view, win/draw/loss, averages, H2H stats |
| 2.6 | `calculation/models/model_a.py` | Attack/defence ratings (FT and HT), μ_home / μ_away |
| 2.7 | `calculation/models/model_l.py` | League-average timeline model |
| 2.8 | `calculation/models/model_b2.py` | Competition-adjusted team index |
| 2.9 | `calculation/models/model_c.py` | Market-implied μ: single line, Asian whole line, multi-market optimisation, consistency check |
| 2.10 | `calculation/pricing.py` | μ blend → λ15 → grid → P(0-0), P(Draw), leads → fair odds → minimum odds |
| 2.11 | `calculation/ev.py` | EV, expected bookmaker price |
| 2.12 | `decision/` | Layers 0–5, grades, reason builder, ranking + 50 cap, one-way downgrade |
| 2.13 | Invariant checks | Grid sums to 1 ± 0.001, 0 < P < 1, λ ≥ 0, fair odds > 1 |

### Tests

| Type | Examples |
|---|---|
| Golden | λ15 = 0.3125 → P(0-0) = 0.7316 → fair 1.367; power de-vig k ≈ 1.154 → 73.9%; Model L example → 77.9%; Model B v2 index 0.78; System doc worked example 77.8% |
| Property | P(0-0) decreases as λ increases; de-vigged probabilities sum to 1; P(Draw) ≥ P(0-0) |
| Unit | Every row of the 15' recovery table; every red flag; ranking order; cap of 50 |

### Exit criteria

- ✅ All worked examples in the docs reproduce exactly.
- ✅ Test coverage ≥ 95% for `calculation/` and `decision/`.
- ✅ Import-boundary check passes (`calculation` imports only `domain`).

---

## Phase 3 — Data pipeline

**Goal:** real, validated data in the database for 1–2 leagues, then more.

### Tasks

| # | Task |
|---|---|
| 3.1 | Ingestion adapters: API-Football, football-data.org, openfootball, football-data.co.uk (raw payloads → Supabase Storage + `raw` records) |
| 3.2 | Normalisation: standard match model, team/competition ID mapping, goal events, 15'/HT/FT derivation, recovery, `data_status` |
| 3.3 | Storage repositories with **point-in-time enforcement** (`as_of`) |
| 3.4 | Seed 1–2 pilot leagues (current + previous 2 seasons) |
| 3.5 | Calculate competition baselines and team statistics for pilot leagues |
| 3.6 | Cross-check: compare a sample of matches across two sources |
| 3.7 | Extend to Tier 1 leagues (Data doc §12.2) |

### Exit criteria

- ✅ For any pilot-league fixture, the last 10 general / home / away matches of both teams are returned correctly from the database.
- ✅ Point-in-time test: no match after `as_of` is ever returned.
- ✅ Cross-source check: ≥ 98% of sampled 15' results agree; disagreements explained.
- ✅ Re-running ingestion creates no duplicates.

---

## Phase 4 — Backtest (gate)

**Goal:** prove — or disprove — the formula on history before building daily operations.

### Tasks

| # | Task |
|---|---|
| 4.1 | Backtest runner: replays day by day, both sessions, `as_of` = kick-off, same code as live |
| 4.2 | Phase 1 backtest — calibration: Brier score, log loss, predicted vs actual by 2-point bucket |
| 4.3 | Phase 2 backtest — estimated value using Pinnacle O/U 2.5 closing odds → Model C estimate (Data doc §9) |
| 4.4 | Compare against a baseline (competition average only) |
| 4.5 | Parameter tuning on one period; **validation on a later, unseen period** |
| 4.6 | Report: per league, per grade, per model, number of qualified matches per session |

### Deliverable

`docs/BACKTEST-REPORT.md` with results, chosen parameter version and decision.

### Gate B — exit criteria

- ✅ ≥ 300 qualified historical selections.
- ✅ Calibration: predicted and actual 0-0 rates within max(±3 pts, 2 × standard error) in each populated bucket (≥ 50 fixtures). *(Owner decision 2026-11-16; a flat ±3 pts is below the noise for buckets under ~900 fixtures.)*
- ✅ Model beats the competition-average baseline (lower Brier score).
- ✅ Estimated ROI positive on the **unseen validation period**.
- If not met → adjust formula/parameters (new version), re-run; **do not start Phase 5** until passed.

---

## Phase 5 — Session pipeline and FastAPI

**Goal:** the system runs itself twice a day and reports on Telegram.

### Tasks

| # | Task |
|---|---|
| 5.1 | Scheduler (APScheduler) + `bettory worker` command |
| 5.2 | Planner jobs (07:00 / 19:00 EAT) → `ops.session_plans` |
| 5.3 | Session run: ingest → normalise → calculate → price → verify → rank & cap → publish |
| 5.4 | Automatic odds (if Gate A allowed) → Model C |
| 5.5 | Lineup re-check job (withdraw only) |
| 5.6 | Settlement job (WIN / LOSE / VOID, profit, CLV) |
| 5.7 | FastAPI endpoints: sessions, bets (report), match analysis, record placed bet, settle manually, job status |
| 5.8 | `api` schema views for the frontend |
| 5.9 | Telegram: report ready, no matches, withdrawal, failure, login alert |
| 5.10 | Job logging, retries (3), `LATE` / `FAILED` handling |

### Exit criteria

- ✅ 7 consecutive days of both sessions running locally on schedule, reports ready by `ready_by`.
- ✅ Each report ≤ 50 matches, only QUALIFIED, all with reasons.
- ✅ Withdrawal and failure alerts tested.
- ✅ API quota stays within the daily limit (measured).

---

## Phase 6 — Frontend

**Goal:** a private React app to read reports and record bets.

### Tasks

| # | Task |
|---|---|
| 6.1 | React + TypeScript (Vite) project, Supabase client, generated types, TanStack Query |
| 6.2 | `/login`: email + password + Turnstile → TOTP step; route guard; no sign-up page |
| 6.3 | **Bets section**: session selector, qualified matches with minimum odds, lineup status, withdrawn badge |
| 6.4 | Match analysis view: all numbers, sample sizes, reasons, versions |
| 6.5 | Record placed bet form (market, odds taken, stake) |
| 6.6 | Performance pages: ROI, win rate vs break-even, CLV, calibration chart, by league/grade |
| 6.7 | Job status page (last runs, failures) |
| 6.8 | Mobile-friendly layout (bets are placed from the phone) |

### Exit criteria

- ✅ Login works only for the owner with two-factor; everything else redirects to `/login`.
- ✅ Telegram link opens the correct session in the Bets section.
- ✅ No calculation code in the frontend (review check).
- ✅ Usable on a phone screen.

---

## Phase 7 — Deployment and careful go-live

**Goal:** run in production and prove the system with real prices before real money.

### Tasks

| # | Task |
|---|---|
| 7.1 | Backend Docker image; deploy `api` + `worker` to the container host |
| 7.2 | Frontend to Vercel; environment variables; CORS to production domain |
| 7.3 | Production Supabase: migrations, owner, MFA, Turnstile, backups |
| 7.4 | Monitoring: job failures to Telegram; daily health summary |
| 7.5 | **Paper betting 2–4 weeks**: follow reports, record the odds you *would* have taken, no real money |
| 7.6 | Review: hit rate vs predicted, CLV, number of qualified matches per session |
| 7.7 | Go-live with **flat 1% stakes**; weekly reviews |

### Exit criteria

- ✅ Paper-betting results consistent with backtest (calibration within tolerance; CLV not negative).
- ✅ No missed session deadlines in the last 7 days.
- Only then: real stakes. Kelly staking only after 300+ live bets confirm positive ROI and CLV.

---

## 12. Working rules

| Rule | Detail |
|---|---|
| Branches | `main` always deployable; one feature branch per task; merge only with CI green |
| Commits | Small, one purpose each; message says what and why |
| Docs first | A design change updates the relevant doc in the same branch |
| Versions | Any formula or parameter change → new `formula_version` / `params_version` |
| No shortcuts on strictness | Never weaken a validation or constraint to make a test pass |
| Secrets | Never in the repository; `.env.example` holds names only |

---

## 13. Definition of done

A task is done when:

1. Code follows the architecture boundaries.
2. Tests written and passing (golden/property/unit/integration as relevant).
3. Type checks and lint pass (`mypy --strict`, `ruff`, `tsc`).
4. Validation and failure behaviour covered (fail closed).
5. Docs updated if behaviour or design changed.
6. Merged to `main` with CI green.

---

## 14. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Free API plan restricts seasons or endpoints | Not enough history | Phase 0 checks; seed from openfootball / football-data.co.uk |
| No free odds for HT/FT totals or 15' markets | Model C manual or missing | Minimum-odds report; stricter EV threshold without market check |
| Backtest fails Gate B | Formula not profitable | Iterate formula versions; stop before building operations |
| Goal-minute labels inconsistent across sources | Wrong 15' results | Phase 0.9 check; cross-source validation in Phase 3 |
| Supabase free project pauses / limits | Downtime | Daily activity; upgrade to Pro if needed |
| Secrets leaked in repository | Account takeover | `.gitignore`, `.env.example`, rotate any exposed key |
| Bookmaker margins on 15' markets too high | Few qualified bets | Accept — the cap is a maximum, not a target |

---

## 15. Progress tracker

Changes of 17–18 Nov 2026 (paper betting, re-check, CLV, real bets, API, Telegram, Supabase, frontend steps 1–4):
what, how and the effect on the system in [CHANGES-2026-11.md](CHANGES-2026-11.md).

| Phase | Status | Started | Finished | Notes |
|---|---|---|---|---|
| 0 — Verify assumptions | 🟡 In progress | | | Open: your bookmaker's 15' rules (V6), 15' market per league |
| 1 — Foundation | ✅ Done | | | |
| 2 — Calculation engine | ✅ Done | | | Models L, A, B v2, C, pricing, decision layer; params v1.5 |
| 3 — Data pipeline | 🟡 In progress | | | Infrastructure done; 15'-vs-15' cross-check and multi-season goal minutes wait for API-Football Pro |
| 4 — Backtest (Gate B) | 🟡 In progress | 2026-11-16 | | Runner and tuning tool done (BACKTEST-REPORT.md); real tuning waits for the backfill. Paper betting (forward test, PAPER-BETTING.md): steps 1–5 done (frozen predictions, sealed odds, paper bets + settlement, report with e-values, recorder cycle in the worker / Task Scheduler) — **built, not live yet (unfinished)**: waits for the Pro upgrade, the alpha / min_bets confirmation, migration approval and switching the recorder on (PAPER-BETTING.md §9) |
| 5 — Session pipeline + API | 🟡 In progress | 2026-11-17 | | 5.7 FastAPI endpoints for the frontend done (docs/API.md, branch `frontend-api`): sessions and report, match analysis, odds check, real bets (append-only, TZS), performance, paper betting, recorder, dashboard; typed contract docs/api/openapi.json. Also done: lineup re-check with withdrawals, closing odds and CLV, Telegram notifications (PAPER-BETTING.md §10–12) |
| 6 — Frontend | 🟡 In progress | 2026-11-17 | | Steps 1–5 done (docs/FRONTEND.md): every screen — login, Today, Bets section, match analysis, odds check, bets, performance and paper charts, recorder, calendar, data health, notifications, settings; next: step 6 (whole-application tests) |
| 7 — Deployment + go-live | ⬜ Not started | | | |

Status values: ⬜ Not started · 🟡 In progress · ✅ Done · ⛔ Blocked
