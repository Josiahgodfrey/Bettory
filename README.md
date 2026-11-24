# BETTORY

A private, single-owner **football 0–15′ verification engine**. For each fixture it estimates the chance of no
goal in the first fifteen minutes and reports **BET / SKIP** with every check and number. It never places bets:
you bet only when the offered odds are at or above the report's minimum odds, one market per match.

> **Data costs nothing.** Fixtures, scores and goal minutes come from **ESPN's public JSON feeds** (no key, no
> daily quota, all 50 registered competitions). Historical main-market odds come from **football-data.co.uk**
> CSV files. API-Football is optional (free plan) and is not required for analysis. There is no paid-plan step.

---

## 1. Shape of the system

```
          React + Vite (Vercel)                 FastAPI + worker (Render)
   Today · Sessions · Match · Bets        ←→   ingestion, calculation, decision,
   Performance · Paper · Data health            sealing, settlement, backtest
                    │                                    │
                    └──────────► Supabase ◄──────────────┘
                       Postgres · Auth (owner + TOTP) · Storage
```

| Layer | Where | Rule |
|---|---|---|
| Frontend | `frontend/` | displays numbers only; never calculates |
| Backend | `backend/app/` | all maths, pure calculation package, one engine for live + backtest |
| Database | `supabase/migrations/` | schema, constraints, append-only triggers; the single source of truth |

Core guarantees kept on purpose: point-in-time reads (`as_of`), append-only sealed predictions with a hash chain,
owner-only login with two-factor, two daily sessions in EAT, at most 50 listed matches (a maximum, never a target).

## 2. The free data model

| Need | Source | Cost / limit |
|---|---|---|
| Fixtures, results, HT/FT, **goal minutes** | ESPN public JSON (`backend/app/ingestion/espn.py`) | free, no key, no quota, paced at 1 request/s |
| Historical main-market odds (backtest benchmark) | football-data.co.uk CSV | free download |
| Lineups (optional, ~1h before kick-off) | API-Football free plan | 100 requests/day, optional |
| 15′ market odds | your betting app, entered manually | no free source exists |

Commands:

```bash
cd backend
venv\Scripts\python -m app.cli ingest espn-days --from 2026-07-20 --to 2026-11-24
venv\Scripts\python -m app.cli ingest espn-history --from 2026-11-24T10:00 --to 2026-11-24T22:00
venv\Scripts\python -m app.cli worker            # scheduler: ESPN fill + paper cycle + settlement
```

## 3. Screens

`Today` · `Bets section` (session report) · `Match analysis` · `My bets` · `Performance` · `Paper betting` ·
`Data health` · `Settings`.

## 4. Run it locally

```bash
# backend
cd backend
venv\Scripts\python -m pytest -q                 # 389 unit/contract tests, no network
venv\Scripts\python scripts\dev_api.py           # API on 127.0.0.1:8000, local DB copy, all services off

# frontend
cd frontend
npm install
npm run dev                                      # http://localhost:5173
npm run typecheck && npm run lint && npm test
```

## 5. Documentation

Start here:

- [docs/LEAN-FREE.md](docs/LEAN-FREE.md) — what this simplification changed and what is left.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — technology decisions and rules.
- [docs/DATA-SOURCES-AND-MARKET-MODEL.md](docs/DATA-SOURCES-AND-MARKET-MODEL.md) — sources and model.
- [docs/FIFTEEN-MINUTE-ODDS-CALCULATION.md](docs/FIFTEEN-MINUTE-ODDS-CALCULATION.md) — the maths.
- [docs/PAPER-BETTING.md](docs/PAPER-BETTING.md) — the forward test (Gate B).
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — Render + Vercel.

The other files in `docs/` are historical notes; `docs/LEAN-FREE.md` lists which parts they no longer describe.
