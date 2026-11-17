# Change record — 17 and 18 November 2026

> What was built or changed in these two days, **how** it was done, and **how it affects the system**.
> The detailed reference for each part stays in its own document (linked in every section); this file is the
> overview to read first. Branches: `paper-betting` → `frontend-api` → `frontend` (each builds on the one before;
> pushed to GitHub on 18 Nov, not merged into `main`). 22 commits, from `4e24d18` to `9920243`.

## Contents

1. [Summary](#1-summary)
2. [The system now, end to end](#2-the-system-now-end-to-end)
3. [Paper betting — the forward test](#3-paper-betting--the-forward-test)
4. [Lineup re-check and withdrawals](#4-lineup-re-check-and-withdrawals)
5. [Closing odds and closing line value](#5-closing-odds-and-closing-line-value)
6. [Real bets](#6-real-bets)
7. [The API for the frontend](#7-the-api-for-the-frontend)
8. [Telegram notifications](#8-telegram-notifications)
9. [The database moved to Supabase](#9-the-database-moved-to-supabase)
10. [Tests isolated from the real services](#10-tests-isolated-from-the-real-services)
11. [The frontend application](#11-the-frontend-application)
12. [API-Football Pro readiness and new-team review](#12-api-football-pro-readiness-and-new-team-review)
13. [Database changes](#13-database-changes)
14. [Configuration and environment](#14-configuration-and-environment)
15. [Incidents and what was done about them](#15-incidents-and-what-was-done-about-them)
16. [What is not live yet](#16-what-is-not-live-yet)

---

## 1. Summary

| Change | What it gives the system | Status | Reference |
|---|---|---|---|
| Paper betting (forward test) | Every session's predictions are sealed before kick-off and judged on real results: the honest test of whether BETTORY works (Gate B) | Built, tested, **not running yet** | [PAPER-BETTING.md](PAPER-BETTING.md) |
| Lineup re-check | 30–75 min before kick-off, a listed match is re-verified with lineups and fresh odds and **withdrawn** if it no longer passes | Built, tested | PAPER-BETTING.md §10 |
| Closing odds and CLV | The last prices before kick-off are stored, and every bet is measured against them | Built, tested | PAPER-BETTING.md §11 |
| Real bets | Placed bets are recorded (append-only, TZS), rule breaches flagged, settlement computed on read | Built, tested | [API.md](API.md) §9 |
| Frontend API | Every screen's data and actions as typed endpoints, with an exported contract | Built, tested | API.md, `docs/api/openapi.json` |
| Telegram | Messages for report ready, no matches, withdrawals, failures and logins | **Working** (test message delivered) | PAPER-BETTING.md §12 |
| Supabase | The database (schema + data) now lives in Supabase; the backend uses it | **Live** | [SUPABASE.md](SUPABASE.md) |
| Test isolation | The test suite can never reach Supabase, Telegram or the football APIs | In force | SUPABASE.md §5 |
| Frontend steps 1–4 | Login, Today, Bets section, match analysis, odds check, bets, performance and paper charts | Built, checked in the browser | [FRONTEND.md](FRONTEND.md) |

Test status at the end of 18 Nov: **508 backend tests pass**; frontend type check, lint, production build and 8 unit
tests pass.

## 2. The system now, end to end

```
 API-Football / football-data.org / openfootball
            │  (ingestion, nightly backfill)
            ▼
 Supabase PostgreSQL (eu-west-1) ── raw → core (fixtures, goals, odds, lineups)
            │
            ▼
 Recorder cycle, every 5 minutes (worker or Task Scheduler)            ── not switched on yet
   capture odds → FREEZE predictions at the cut-off (sealed, hash-chained)
   → RE-CHECK 30–75 min before kick-off (withdraw if it fails)
   → CLOSING odds 12 min before kick-off → RESULTS → SETTLE (append-only)
   → NOTIFY (Telegram outbox)
            │
            ▼
 Python API (FastAPI, owner-only: Supabase token with two-factor)
            │  typed contract docs/api/openapi.json
            ▼
 React frontend (Vite, shadcn preset) — reads and displays; never calculates
```

**Main rule that ties it together:** the session report the owner bets from **is** the frozen record that the
forward test judges. There is no second copy that could disagree (API.md §4).

## 3. Paper betting — the forward test

**What.** A forward test of BETTORY on matches that have not been played yet, so no result can leak into a
prediction. Commits `4e24d18`, `c55a516`, `04b6b69`, `7a12c88`, `39a97ae`.

**How it is done.**

| Step | How |
|---|---|
| Capture | Before each session's cut-off, the odds of its fixtures are fetched and stored (snapshot `ANALYSIS`) |
| Freeze | At the cut-off the full verification runs once; each prediction is written to `paper.predictions` with every probability, minimum odds, check and the odds seen, and **sealed**: a SHA-256 hash of the row plus the previous row's hash (a hash chain). The database refuses any change or deletion |
| Paper bet | Sealed at the same moment: one unit on the market BETTORY would bet, at the best price seen |
| Settle | When the 15′ result is known, a settlement row is **added** to `paper.settlements` (never an update); a corrected score adds a new row |
| Report | `paper-report` / `GET /paper/report` judges the record with the backtest's own rules: Gate B criteria, calibration, Brier against the league-only price, ROI with a session bootstrap interval, and two **e-values** (sequential evidence that stays valid however often the report is read) |
| Recorder | `paper-cycle` runs all steps; each decides from the database whether it is due, under one lock, within a daily request bound (free 25 / pro 1000) |

**Effect on the system.**
- The owner's Bets section shows the sealed report after the cut-off and a clearly marked preview before it.
- `paper-verify --replay` / **Replay** in the app recomputes a session and must match the seal: proof that nothing
  was changed and that the analysis is deterministic.
- Gate B can only be passed with real forward evidence; at +5 % edge it takes roughly a thousand bets
  (PAPER-BETTING.md §7.2).
- Uses API requests on the recorder schedule (bounded per day, counted under the `paper_` job prefix).

## 4. Lineup re-check and withdrawals

**What.** Commit `f9e1f0e`. Owner decisions: re-verify with fresh data (no lineup-specific rule until one is defined
and tested); a withdrawn match's paper bet is VOID.

**How.** 30–75 minutes before kick-off (at most 3 attempts, 15 minutes apart, timed by the database clock) the
recorder fetches lineups and odds (2 requests per fixture), re-runs the **full** verification at that moment and
stores the outcome in `paper.rechecks`: `CONFIRMED`, `LINEUPS_PENDING` or `WITHDRAWN`. Each re-check row is tied by
hash to the sealed prediction; re-checks are refused at or after kick-off; a withdrawal is final.

**Effect.**
- Report entries carry `lineup_status`; the app shows a red "Withdrawn — never bet this match" banner and
  struck-through odds; the odds check answers NO BET.
- A real bet on a withdrawn match is recorded with the breach `WITHDRAWN`; the paper bet settles VOID.
- The sealed prediction itself never changes (the re-check is a separate, append-only record).

## 5. Closing odds and closing line value

**What.** Commit `ce4ddde`.

**How.** 12 minutes before kick-off the recorder stores the prices of listed matches and of matches with real bets
(snapshot `CLOSING`). The closing line is each bookmaker's last price before kick-off (hand-entered prices excluded).
Two measures are computed in Python (`app/settlement/clv.py`):
- **CLV (fair)** = odds taken × mean de-vigged closing probability − 1
- **CLV (book)** = odds taken ÷ that bookmaker's closing odds − 1

**Effect.** Every bet (real and paper) shows its CLV once the match has kicked off; Performance and the paper report
show the mean CLV and the share of bets that beat the close — the earliest sign of a real edge, long before profit is
conclusive.

## 6. Real bets

**What.** Commit `3a4c88d`. Owner decisions: **D1** a bet that breaks a rule is recorded and flagged, never refused;
**D2** no editing — cancel and record again; **D3** currency TZS.

**How.** `betting.placed_bets` and `betting.bet_cancellations`, both append-only (the database refuses UPDATE, DELETE
and TRUNCATE), recording time from the database clock, a future `placed_at` refused. Rule breaches computed at
recording: `NOT_LISTED`, `BELOW_MIN_ODDS`, `BOTH_MARKETS`, `AFTER_KICKOFF`, `WITHDRAWN`. Settlement (WIN / LOSE /
VOID / PENDING) is **computed on every read** from the stored 15′ result, so a corrected score shows at once.
`config/betting.yaml`: `currency: TZS`, `max_stake` (a typing-error guard).

**Effect.** Performance separates bets that followed the rules from those that did not, so discipline is measurable.
`GET /settings` now also reports `betting.currency` and `betting.max_stake` (added 18 Nov for the bet form).

## 7. The API for the frontend

**What.** Commits `4b918e1`, `fc87ad8`, `3a4c88d`, `87274cf`.

**How.** FastAPI routers for every screen (sessions, matches, paper, recorder, bets, performance, notifications,
settings). Every response has a strict pydantic model (`app/api/responses.py`, unknown fields are an error); all
times are UTC ending in `Z`. `python -m app.cli openapi` exports `docs/api/openapi.json`; a test fails when it is
out of date. Every endpoint is owner-only: a Supabase access token with two-factor (`aal2`) and the owner's user id;
a local development bypass exists only with `ENVIRONMENT=local` and only from this machine.

**Effect.** The frontend's TypeScript types are generated from the contract, so a backend change breaks the frontend
build instead of a screen. The paper report (open JSON) gained chart series on 18 Nov:
`forecasts.*.buckets`, `e_profit.path`, `e_forecast.path`, `bets.profit_curve`, `bets.hit_rate`.

## 8. Telegram notifications

**What.** Commits `09b0ad3`, `8132d5c`. Bot `@logorexen_bot`, sending to the owner's private chat.

**How.** An outbox table `ops.notifications`: each event has a unique key and is sent once (up to 3 attempts),
messages older than 24 hours expire instead of arriving late, failures are reported at most once an hour. Events:
report ready, no matches, withdrawal, failure, login (the first `GET /me` of each login session), test. The client
forces IPv4 (this PC's IPv6 route to Telegram timed out), uses `link_preview_options`, honours `retry_after` on 429
and redacts the token from every error.

**Effect.** The owner is told when a report is ready and when something goes wrong, without opening the app. The
recorder must be running for report messages to be sent.

## 9. The database moved to Supabase

**What.** Commits `7a4a48b`, `723cdf6`. Project `syheclbxfmviccltkppa`, eu-west-1, Postgres 17.

**How.**
1. All 8 migrations applied to Supabase.
2. Schema compared with the local database: 298 columns, 218 constraints, 22 triggers, 14 functions, 61 indexes,
   16 enums, 24 tables — identical; row-level security on every table; no access for `anon` / `authenticated`.
3. Data copied in one transaction (data-only dump); every table checked row by row (checksums by id; for
   `core.match_revisions`, per-row server-side md5: 3,256 rows, 0 different).
4. The backend's `.env` points to the **session pooler** (`aws-0-eu-west-1.pooler.supabase.com:5432`, TLS), because
   the direct host is IPv6-only and this PC has no IPv6 route. The local database is kept as a frozen copy
   (`LOCAL_DATABASE_URL`, no longer updated).

**Effect.**
- Production data now lives in Supabase.
- From the owner's PC each query takes ≈ 250 ms, so analysis screens are slow (session preview 93 s, match analysis
  72 s, dashboard 11 s). The fix is to run the backend on a host in or near eu-west-1 (ARCHITECTURE.md §5 option A).
- Development and screen checks use the local copy (under 1 ms per query) through the local bypass.
- Raw payload files (183 files, 1.5 MB) are still on local disk until `SUPABASE_SERVICE_ROLE_KEY` is set.

## 10. Tests isolated from the real services

**What.** Commits `723cdf6`, `c76ce98`.

**How.** `backend/tests/conftest.py` blanks the database, Telegram, API-Football, football-data.org and storage
settings before any test module loads (environment variables override `.env`), and since `c76ce98` also makes any
attempt to open a real Telegram connection raise an error. Integration tests use a throwaway local Docker database
(`TEST_DATABASE_URL`).

**Effect.** Running the tests can never write to Supabase, spend API quota or send a Telegram message (see §15).

## 11. The frontend application

**What.** Commits `1623103`, `0fa380a`, `d876626`, `9920243` on branch `frontend`. Full detail: FRONTEND.md.

**How.**

| Part | How |
|---|---|
| Stack | React 19 + TypeScript (strict), Vite, TanStack Query, typed client on the generated contract |
| Look | The owner's shadcn preset `b3Ha1DvmO8` (Lyra style on Base UI, zinc + green, Inter / JetBrains Mono); light / dark / system; restrained motion (page fade, pop-up notifications) |
| Login | Supabase Auth in the browser: password → 6-digit authenticator code (QR set-up on first login) → `GET /me`; every page except `/login` requires it |
| Step 1 | Layout, sidebar (slide-in menu on phones), theme, login |
| Step 2 | **Today** (alerts, next session with countdowns, nearby sessions, paper record) and **Bets section** (session picker, sealed / preview banner, listed-match cards with minimum odds, lineup status, withdrawals; all fixtures with plain-English reasons) |
| Step 3 | **Match analysis** (verdict; checks, models, team windows, odds, match facts), **odds check** (BET / NO BET / WAIT card), **record a bet**, **My bets** (filters, settlement, CLV, breaches, cancel) |
| Step 4 | **Performance** and **Paper betting** with hand-written SVG charts: profit curves, ROI by group with uncertainty, e-value evidence (log scale, bar 1/alpha = 20), calibration, Gate B checklist, chain verify and replay |

**Rules kept.**
- **No calculations in the frontend** (ARCHITECTURE.md R2): every probability, odds, EV, ROI and rate comes from
  the API; the frontend only formats and maps numbers to pixels. Rates are shown as "7/10", never divided. (One slip
  — a hit rate computed in the browser — was caught and moved to the API the same day.)
- Only public values in the frontend (Supabase URL and publishable key).
- Charts use one validated series colour (the preset's orange `--chart-3`; the brighter `--chart-2` failed the dark
  lightness check), grey reference lines, a tooltip by mouse, touch and keyboard, and a data table under each chart.

**Effect.** The owner can do everything from a browser or phone: read the session report, check a bookmaker's
prices, record and cancel bets, and watch performance and the forward test. Writes (odds check, bets, cancel) were
checked with mocked requests because bets are append-only; real writes are tested in step 6 on a throwaway database.

## 12. API-Football Pro readiness and new-team review

**What.** Commit `93524d6`.

**How.** A backfill mode `current` for the season being played (paid plan only; skipped at no cost on Free); daily
request limits per plan; a Pro run on a Free key stops before any counted request; `review resolve --new-team` and
`POST /reviews/{id}/new-team` to create a club new to the database (refused on an existing name, and on a likely
duplicate unless confirmed).

**Effect.** Once the owner's Pro upgrade is active, `backfill-daily --run` fills the history the analysis needs;
until then every fixture shows "Not enough data".

## 13. Database changes

All in `supabase/migrations/`, applied to both the local database and Supabase. A migration that is applied is never
edited again: a change means a new migration (SUPABASE.md §3).

| Migration | Adds |
|---|---|
| `20261117120000_paper_predictions.sql` | schema `paper`: `predictions` (sealed, hash-chained), `settlements` (append-only) |
| `20261118120000_placed_bets.sql` | schema `betting`: `placed_bets`, `bet_cancellations` (append-only, database clock) |
| `20261119120000_paper_rechecks.sql` | `paper.rechecks` (hash-tied to the sealed prediction, refused after kick-off) |
| `20261120120000_notifications.sql` | schema `ops`: `notifications` (the Telegram outbox) |

## 14. Configuration and environment

| File | Change |
|---|---|
| `backend/config/paper.yaml` | new: recorder schedule (5 min), daily request bounds, capture / re-check / closing / results timing and limits |
| `backend/config/betting.yaml` | new: `currency: TZS`, `max_stake` |
| `backend/config/backtest.yaml` | `paper_betting`: alpha 0.05, min_bets 100 (**proposed, to be confirmed by the owner**) |
| `backend/config/backfill.yaml` | `current` mode, per-plan daily limits |
| `BETTORY/.env` (never committed) | `SUPABASE_PROJECT_REF`, `DATABASE_HOST` (pooler), `DATABASE_PORT=5432`, `TELEGRAM_CHAT_ID`; `DATABASE_URL` renamed `LOCAL_DATABASE_URL`. Still to add: `OWNER_USER_ID`, `SUPABASE_SERVICE_ROLE_KEY`, `APP_URL` |
| `frontend/.env.local` (never committed) | `VITE_API_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY`, `VITE_AUTH_BYPASS` (development only) |

## 15. Incidents and what was done about them

| Date | What happened | Cause | Fix | Effect now |
|---|---|---|---|---|
| 17 Nov | The Supabase "dry run" applied all 8 migrations for real (the owner had approved the push) | In `cmd.exe` an unquoted `&` in the database URL split the command and dropped `--dry-run` | Reported; the correct quoted command is in SUPABASE.md §3 | Schema verified identical; no harm |
| 17 Nov | The owner received the same "report ready" Telegram message several times | Tests built their settings from `.env`; once the bot token was added, every test that froze a session sent a real message | `tests/conftest.py` blanks real services (12:39); since `c76ce98` a test cannot open a Telegram connection at all | Only one real message exists in the outbox (the test message) |
| 17 Nov | Telegram calls timed out | This PC's dual-stack connection to Telegram hung on IPv6 | The client forces IPv4 | Messages deliver |
| 18 Nov | A database password from 14 Nov is visible in the history of `main` on GitHub; the repository is **public** | `.env` was committed on 14 Nov, before it was ignored | Checked: it is **not** the current password, and no current secret is anywhere in the history | Owner to decide: make the repository private (recommended); change that old password wherever it is still used |
| 18 Nov | A screen crashed with an older API missing a new chart field | The dev API had not been restarted after the backend change | The page now treats missing series as empty | No crash on version mismatch |

Secrets that were pasted in the chat (API key, database password, bot token) should be rotated when convenient.

## 16. What is not live yet

| Item | Needs | Then |
|---|---|---|
| API-Football Pro | Owner completes the upgrade, updates `.env` | `backfill-daily --run` once; fixtures get enough history to be analysed |
| Real login | Owner creates the Supabase user (sign-ups off) and sets `OWNER_USER_ID` | Login with password + authenticator code (FRONTEND.md §3) |
| Recorder | A decision to switch it on (worker or Task Scheduler) — ideally on the hosted backend | Paper betting starts; Telegram report messages flow |
| Backend hosting | Owner chooses Render / Railway / Fly.io near eu-west-1 | Fast screens; recorder runs without the PC |
| Raw files to Storage | Owner adds `SUPABASE_SERVICE_ROLE_KEY` to `.env` | Upload 183 files, `RAW_STORE=supabase` |
| Gate B settings | Owner confirms alpha 0.05 and min_bets 100 | — |
| Frontend steps 5–6 | — | Recorder, Calendar, Data health, Notifications, Settings; whole-application tests |
| Merge | Owner asks for a pull request | `paper-betting` → `frontend-api` → `frontend` into `main` |
