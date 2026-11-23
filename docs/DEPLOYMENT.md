# Deploying the backend on Render

> Written 2026-11-19. ARCHITECTURE.md §5 option A: one Docker container with the FastAPI app and (when switched on)
> the scheduler. Files: `backend/Dockerfile`, `backend/.dockerignore`, `backend/deploy/start.sh`,
> `backend/requirements.txt`, `render.yaml` (the blueprint). No secret is in any of them.

## 1. What gets deployed

| Item | Value |
|---|---|
| Service | `bettory-api`, a Render **web service** built from `backend/Dockerfile` |
| Region | **Frankfurt** (closest to the Supabase database in eu-west-1, Ireland: a few ms per query instead of ≈ 250 ms from the PC) |
| Plan | **Starter** (≈ $7 / month, always on). The free plan sleeps after 15 minutes without traffic and cannot run the scheduler |
| Image | Python 3.14, the 70 dependencies pinned to the versions the tests ran with, runs as a non-root user, health check `/health` |
| Recorder | **off** (`RUN_WORKER=false`): the API only. Nothing is scheduled, no API-Football request, nothing sealed, until switched on (§5) |
| Checked locally 2026-11-19 | the image in production mode against Supabase: `/health` ok; every other endpoint 401 without a login; `/docs` off (404); database reached over TLS; non-root user |

## 2. Before you start

1. **The code must be on GitHub** in the branch Render deploys (`render.yaml`: `branch: frontend`; change it to `main`
   once the branches are merged).
2. **Recommended: make the GitHub repository private** first (GitHub → repository → Settings → General → Danger
   Zone → Change visibility). Render then needs access to it through its GitHub app (step 3.2).
3. Have `BETTORY/.env` open: the secret values are copied from it into Render's dashboard, one by one.

## 3. Create the service (once)

1. Sign up at **render.com** (sign in with GitHub is simplest) and add a payment method (Starter plan).
2. **Account settings → GitHub**: give Render access to the repository `Bettory` (only that one is enough).
3. Dashboard → **New → Blueprint** → choose the `Bettory` repository → branch **`frontend`**. Render reads
   `render.yaml` and shows the service `bettory-api` with the settings to fill in.
4. Fill in every value Render asks for (all are secrets; Render stores them encrypted):

| Render setting | Where the value comes from |
|---|---|
| `SUPABASE_URL` | `.env` → `SUPABASE_URL` |
| `SUPABASE_PROJECT_REF` | `.env` → `SUPABASE_PROJECT_REF` |
| `SUPABASE_SERVICE_ROLE_KEY` | `.env` → `SUPABASE_SERVICE_ROLE_KEY` |
| `DATABASE_HOST` | `.env` → `DATABASE_HOST` (the `…pooler.supabase.com` address) |
| `DATABASE_PASSWORD` | `.env` → `DATABASE_PASSWORD` |
| `OWNER_USER_ID` | `.env` → `OWNER_USER_ID` |
| `CORS_ORIGINS` | for now `["http://localhost:5173"]`; after the frontend is on Vercel: `["https://<your-app>.vercel.app"]` |
| `APP_URL` | for now `http://localhost:5173`; later the Vercel address (used in the Telegram links) |
| `API_FOOTBALL_KEY` | `.env` → `API_FOOTBALL_KEY` |
| `FOOTBALL_DATA_ORG_KEY` | `.env` → `FOOTBALL_DATA_ORG_KEY` (empty if you do not have one) |
| `TELEGRAM_BOT_TOKEN` | `.env` → `TELEGRAM_BOT_TOKEN` |
| `TELEGRAM_CHAT_ID` | `.env` → `TELEGRAM_CHAT_ID` |

   Already set by the blueprint: `ENVIRONMENT=production`, `RUN_WORKER=false`, `DATABASE_PORT=5432`,
   `RAW_STORE=supabase`, `API_FOOTBALL_PLAN=free` (change to `pro` after the upgrade).
5. **Apply**. The first build takes about 5 minutes (Events / Logs tabs). The log ends with
   `worker off (RUN_WORKER=false): API only` and `Uvicorn running`.

## 4. Check it

| Check | Expected |
|---|---|
| Open `https://bettory-api.onrender.com/health` (the address Render shows) | `{"status":"ok"}` |
| Open `https://…onrender.com/dashboard` | `{"detail":"missing bearer token"}` (401: login required) |
| Open `https://…onrender.com/docs` | Not Found (API docs are off in production) |
| The app against Render: in `frontend/.env.local` set `VITE_API_URL=https://…onrender.com`, restart `npm run dev`, open http://localhost:5173 and sign in | Today loads in about a second instead of ten; you get the Telegram login alert |

## 5. Later steps

| When | What to change (Render → service → Environment, then Save: it redeploys) |
|---|---|
| Frontend on Vercel | `CORS_ORIGINS` = `["https://<your-app>.vercel.app"]`, `APP_URL` = that address |
| API-Football Pro active | `API_FOOTBALL_PLAN=pro` |
| **Go live (paper betting starts)** | `RUN_WORKER=true`. From then on the container also runs the nightly backfill and the recorder every 5 minutes (plan → capture → freeze → re-check → closing → results → settle → Telegram). Sealed predictions cannot be undone |
| Backfill only, no recorder | `RUN_WORKER=true` and `WORKER_ARGS=--no-paper` |

Every push to the deployed branch rebuilds and redeploys automatically (`autoDeploy: true`).

## 6. Rules

- Secrets live only in `BETTORY/.env` (your PC) and in Render's dashboard; never in the repository or the image
  (`backend/.dockerignore` excludes `.env`).
- `ENVIRONMENT=production` refuses the local login bypass and turns the interactive API docs off.
- The database is reached through the Supabase session pooler over TLS (`sslmode=require`).
