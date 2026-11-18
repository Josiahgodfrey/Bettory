# BETTORY frontend

> Phase 6, started 2026-11-17 on branch `frontend` (stacked on `frontend-api`). The screens and endpoints are
> defined in [API.md](API.md); this file covers the app itself.

## 1. Stack

| Part | Choice |
|---|---|
| App | React 19 + TypeScript (strict), Vite 6 — `frontend/` |
| Look | shadcn, owner's preset `b3Ha1DvmO8` (style *Lyra* on Base UI, zinc + green, Inter / JetBrains Mono headings), light / dark / system |
| Motion | restrained: page fade-in, toasts (Sonner) for notifications, standard component transitions; nothing decorative |
| Data | TanStack Query; typed client `src/lib/api.ts` (openapi-fetch) on types generated from `docs/api/openapi.json` |
| Login | Supabase Auth in the browser: password → 6-digit authenticator code (aal2) → `GET /me` |
| Tests | Vitest (units), Playwright (whole application: real frontend + real API) |

Rules (ARCHITECTURE.md R2, R12): no calculations in the frontend — every probability, odds value and EV comes from the
API and is only formatted (`src/lib/format.ts`); only public values in the frontend (Supabase URL and publishable key).

## 2. Running it locally

```
cd frontend
npm install
cp .env.example .env.local      # then edit
npm run dev                     # http://localhost:5173
```

| `.env.local` | Meaning |
|---|---|
| `VITE_API_URL` | the Python API (default `http://127.0.0.1:8000`) |
| `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY` | real login (public values; never the service role key) |
| `VITE_AUTH_BYPASS=true` | development without login: only in `npm run dev`, and the API must run with `ENVIRONMENT=local` and `AUTH_LOCAL_BYPASS=true` (it then answers only this machine) |

For development, start the API with **`backend/scripts/dev_api.py`** (from `backend/`:
`venv\Scripts\python scripts\dev_api.py`): local database copy (fast, instead of Supabase at ≈ 250 ms per query from
this PC, SUPABASE.md §4), login bypass, and Telegram / API-Football / football-data.org / Supabase OFF. It refuses to
start if any of them is still configured. Do **not** blank variables by hand (`$env:X=""` in PowerShell): on Windows
that deletes the variable and the real value comes back from `.env`.

| Command | Does |
|---|---|
| `npm run typecheck` / `npm run lint` / `npm run build` | strict types, lint, production build |
| `npm run api-types` | regenerate `src/lib/api-types.ts` after the backend contract changes (`python -m app.cli openapi`) |
| `node scripts/shots.mjs <dir> home settings …` | screenshots of the running app: desktop + phone, light + dark, with console errors |
| `npm test` | unit tests (formatting, EAT times, check names) |
| `npm run e2e` | **whole-application tests** (§5): starts the real API on a throwaway database and the frontend, then drives the browser |

Port 5174 is taken by Docker on this PC; use 5173 (default) or 5180.

## 3. Login — owner setup (one time, before the real login works)

1. Supabase dashboard → Authentication → Sign In / Providers: **disable sign-ups**; Auth → Multi-Factor: TOTP on.
2. Authentication → Users → **Add user** (your email, a strong password, "auto confirm").
3. Copy the new user's id into `BETTORY/.env` as `OWNER_USER_ID=` (the API accepts only this user, with two-factor).
4. Open the app, sign in: the first time it shows a **QR code** for your authenticator app; after that it asks for
   the 6-digit code at each login. A Telegram alert is sent for each new login.

Until step 3 is done, the API refuses every real login (503 "authentication is not configured").

## 4. Build steps

| Step | Content | Status |
|---|---|---|
| 1 | Project, preset, typed API client, login (password + code, QR enrolment), layout and navigation, theme | ✅ 2026-11-17 |
| 2 | Today (dashboard) and Bets section (sessions, report, all fixtures) | ✅ 2026-11-18 |
| 3 | Match analysis, odds check, record a bet, My bets (cancel) | ✅ 2026-11-18 |
| 4 | Performance, Paper betting (report, predictions, chain, replay) — with charts | ✅ 2026-11-18 |
| 5 | Recorder, Calendar, Data health, Notifications, Settings | ✅ 2026-11-18 |
| 6 | Whole-application tests (Playwright on the real API), phone layout pass | ✅ 2026-11-18 |

### Step 1 checks (2026-11-17)

- Type check, lint and production build clean; no console errors on any page (desktop and phone, light and dark).
- Dev API on the local database with the bypass: `GET /me` → connected.
- Real Supabase login page: a protected address redirects to `/login`; wrong credentials are refused by Supabase
  and shown.

### Step 2 checks (2026-11-18)

- **Today** (`GET /dashboard`, refreshed every minute): alerts first (blocking in red), the next session with its
  cut-off, report and first kick-off times counting down, counts (fixtures, listed, withdrawn, paper bets), the
  sessions from yesterday to tomorrow, the paper record (chain, frozen predictions, last freeze, unsettled).
- **Bets section** (`/sessions/{id}`): session picker (sessions with fixtures, a week back to two weeks ahead, arrows
  for previous / next), the state banner (preview with "Recompute", published and sealed, awaiting freeze, missed),
  decision counts, the betting rule, one card per listed match: **minimum odds** for both markets side by side (also
  on a phone), P, fair odds, best price seen at the cut-off, grade, lineup status, re-check minimum when it changed,
  n, model agreement, paper bet. A withdrawn match shows a red "never bet" banner and struck-through odds.
- **All fixtures** tab (`?tab=all`): every fixture with its decision and the first reasons in plain English
  (`src/lib/checks.ts`), linking to the match analysis.
- Checked on the local database (real fixtures, all "not enough data" before the Pro backfill) and with a mocked
  published report for the listed cards; navigation driven in a phone-sized browser; no console errors; unit tests
  (`npm test`) for formatting, EAT times and check names.

### Step 3 checks (2026-11-18)

- **Match analysis** (`/matches/{id}?session=`): header with scores and the 15′ result, the verdict (sealed record or
  preview, versions, decision, grade, lineup status, minimum odds of both markets), then tabs: **Checks** by layer
  (value vs threshold, what blocks the bet, repeated reasons shown once), **Models** (BETTORY, A, B v2, market, league
  baseline for both markets), **Teams** (the four last-10 windows and head to head, rates as "count/n", never divided
  in the frontend), **Odds** (stored prices, yours marked), **Match** (goals, lineups, sources).
- **Odds check** (`#odds`, linked from the Bets section): bookmaker (remembered in this browser), "Goal in minutes 1–15"
  Yes / No (= Under 0.5) and the 15′ result Home / Draw / Away; a market must be complete or empty. The decision card
  shows BET / NO BET / WAIT, the reason, the market to bet with offered odds and EV, and the price checks; "Record
  this bet" opens the bet form already filled in.
- **Record a placed bet**: market, odds, stake in the currency from `GET /settings` (`betting`, added to the API for
  this), bookmaker, placed now or at a time in EAT, note. Odds below the minimum show a warning before saving; the
  saved bet's rule breaches come back from the API and show as a warning notification.
- **My bets** (`/bets`): All / Open / Settled / Cancelled, each bet with result and profit, odds vs minimum, stake,
  bookmaker, 15′ outcome, CLV once closing odds exist, rule breaches, note; **Cancel bet…** asks for a reason.
- Checked in a phone-sized browser: the whole flow match → odds check → record → My bets → cancel (writes mocked,
  because bets are append-only; real writes are tested in step 6 on a throwaway database), all tabs, desktop and
  dark mode; no console errors; 8 unit tests.

### Step 4 checks (2026-11-18) — the visual metrics

Charts are hand-written SVG (`src/components/charts/`, no chart library), following the dataviz rules: one series
colour, the preset's orange `--chart-3` (validated: lightness band, chroma and ≥ 3:1 contrast on both card surfaces;
the brighter `--chart-2` failed the dark band), grey reference lines, 1px solid grid, 2px lines, ringed end dots,
a crosshair tooltip (pointer, touch, arrow keys) and "Show the numbers" (the data table) under every chart. The
frontend only maps numbers to pixels; every figure comes from the API.

- **Performance** (`/performance?range=`): All time / 90 / 30 / 7 days; stat tiles (profit, ROI with its 95 % range,
  hit rate vs break-even, settled, CLV); **profit over time** (cumulative, zero line); ROI **by market, rules followed
  vs broken, grade and league** as bars on one zero baseline with the uncertainty whisker.
- **Paper betting** (`/paper`): **Gate B** checklist; sealed / settled; record chain with **Verify now**; **Replay a
  session**; **profit evidence** and **forecast evidence** (e-values over time, log scale, with the bar 1/alpha = 20
  and the neutral 1); **calibration** per market (predicted vs happened, ±1 standard error, hollow = too few to
  judge) with Brier and the comparison to the league-only price; **paper bets** (tiles + profit curve); the sealed
  listed predictions.
- API additions (`GET /paper/report`): `forecasts.*.buckets`, `e_profit.path` / `e_forecast.path`,
  `bets.profit_curve`, `bets.hit_rate` (tests in `tests/unit/test_paper_report.py`).
- Checked with simulated histories (260 paper bets, 40 real bets) at desktop and phone width, light and dark, and
  with the real (still empty) records; tooltips by mouse and keyboard; no console errors.

### Step 5 checks (2026-11-18)

- **Recorder** (`/recorder`, `GET /paper/status` every minute, every 3 s while a cycle started here runs): API requests
  today against the recorder's bound, sealed predictions, kicked-off-not-settled, Telegram on/off; **what the next
  cycle would do** (the dry run); **Run one cycle now** behind a confirmation (it may spend requests and seal
  predictions; `409` when one is running); last capture / results / freeze / settlement; the 25 latest job runs with
  errors.
- **Calendar** (`/calendar?date=`): every fixture of an EAT day by league, previous / next / pick a date / today,
  score, 15′ result, session and decision; each row opens the match analysis.
- **Data health** (`/data-health`): blocked-bets alert, API-Football quota (read live; says so when the backend has
  no key), coverage per league-season (usable 15′, exact / derived / partial, conflicts, sources), cross-check per
  league-season with FT / HT / 15′ agreement and **Accept after review…** (note required) for a failed one, recent runs
  with their audit findings, stored backtests with their Gate B results, open team reviews.
- **Notifications** (`/notifications`): the Telegram outbox with status and errors, counts, **Send a test message**
  (disabled with an explanation when Telegram is off).
- **Settings** (`/settings`, read-only): account, sessions and time, real-bet rules, Gate B (alpha and min_bets marked
  "to confirm"), recorder timing, bookmakers, the competition registry, and the whole params file as a tree.
- Checked against the dev API (local data, Telegram and data APIs off — `backend/scripts/dev_api.py`): every page at
  desktop and phone width, light and dark, no page wider than the phone, no console errors; the cycle button's
  confirm / cancel / run (request mocked), the test message refused while Telegram is off, calendar navigation.

## 5. Whole-application tests (step 6, 2026-11-18)

`npm run e2e` (from `frontend/`) runs Playwright against the **real backend and real frontend together**:

| Part | How |
|---|---|
| Backend | `backend/tests/e2e/server.py` on port 8100: a **fresh database** on the local test server (`TEST_DATABASE_URL`, default the Docker `bettory-pg` at 127.0.0.1:54329; it refuses any non-local host, so never Supabase), all migrations, the synthetic league TST1, two fixtures 40–50 minutes ahead whose session is **sealed with the real freeze**. Isolated like the test suite (`tests/conftest.py`): no Telegram connection possible, no data-API keys, raw files in a temporary folder. Old `bettory_e2e_*` databases are dropped at start |
| Frontend | Vite on port 5190 with the login bypass and its own dependency cache (`node_modules/.vite-e2e`), so it can run beside `npm run dev` |
| Config | `frontend/playwright.config.ts` (both servers are started and stopped by Playwright) |

What the 31 tests cover:

| File | Tests |
|---|---|
| `e2e/pages.spec.ts` | every screen on **desktop and phone** (Pixel 7): loads from the API, no browser error, never wider than the screen; the Bets section's listed cards; every match-analysis tab; the not-found page |
| `e2e/betting.spec.ts` | Today → sealed session → listed cards with the sealed minimum odds; **odds check with real writes**: a too-good price is refused (NO BET, "BETTORY vs bookmaker"), a realistic one gives BET, the prices are stored and shown; **record the bet** from the card → toast → stored by the API (TZS, follows the rules) → My bets; a bet **below the minimum** is recorded and flagged; **cancel** it with a reason → still listed as cancelled, excluded from performance; an implausible stake is stopped by the form |
| `e2e/system.spec.ts` | Paper betting: 2 sealed predictions, **Verify now** (chain intact), **Replay** (identical to the seal); Recorder: a **real cycle** from the app (confirm → started → finished, status reported); Notifications: Telegram off, the test message refused (409); Calendar shows the sealed fixtures; Settings show the betting rules |

Last run 2026-11-18: **31 passed** (about 1 minute), twice in a row from a fresh database.
