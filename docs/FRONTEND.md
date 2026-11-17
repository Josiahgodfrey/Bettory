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

For development the API can run against the local database copy (fast) instead of Supabase (≈ 250 ms per query from
this PC, SUPABASE.md §4).

| Command | Does |
|---|---|
| `npm run typecheck` / `npm run lint` / `npm run build` | strict types, lint, production build |
| `npm run api-types` | regenerate `src/lib/api-types.ts` after the backend contract changes (`python -m app.cli openapi`) |
| `node scripts/shots.mjs <dir> home settings …` | screenshots of the running app: desktop + phone, light + dark, with console errors |

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
| 3 | Match analysis, odds check, record a bet, My bets (cancel) | ⬜ |
| 4 | Performance, Paper betting (report, predictions, chain, replay) | ⬜ |
| 5 | Recorder, Calendar, Data health, Notifications, Settings | ⬜ |
| 6 | Whole-application tests (Playwright on the real API), phone layout pass | ⬜ |

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
