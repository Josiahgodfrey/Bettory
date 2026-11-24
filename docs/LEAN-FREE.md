# The lean-free pass — what changed

**Branch `simplify/lean-free` · 2026-11-25.** This is the record of the simplification the owner asked for:
remove the complications, make the system smooth, and get all the data it needs without paying.

Read this before the older docs: several of them still describe machinery that no longer exists.

## 1. Goal and result

| Goal | Result |
|---|---|
| Zero data cost | **Met.** ESPN (no key, no quota, 50 competitions) is the goal-minute/fixture source; football-data.co.uk CSVs provide historical odds. No paid plan is needed. |
| Fewer moving parts | Ingestion sources 5 → 3 (ESPN, football-data.co.uk, optional API-Football lineups). API endpoints and frontend screens roughly halved. |
| One way to decide | The live path uses **Model A + the league baseline** only. B2/C stay dormant for legacy params and old backtests, and are not computed in the live (1.6) path. |
| One way to evaluate | The pre-registered tuning plans (v1–v3) and their tooling were removed. Backtest + paper betting remain. |
| Keep what proves the system | Untouched: point-in-time reads, sealed hash-chained predictions, owner + TOTP auth, two sessions, 50 cap, settlement, forward test. |

## 2. Removed

### Data sources

| Removed | Why |
|---|---|
| football-data.org adapter, parser, CLI, API | ESPN covers the same matches with goal minutes and no quota; a second source only created review work |
| openfootball adapter, parser, CLI, API | Same; its current-season files are absent anyway |
| `FOOTBALL_DATA_ORG_KEY` setting, Render variable, probe script | The source is gone |

Kept: **ESPN** (primary), **football-data.co.uk** (free CSV history + odds), **API-Football** (optional lineups,
free plan). Historical rows from removed sources remain in the database; the enum values stay so old data still
reads.

### API surface

Removed routers/endpoints: `/jobs/*` (ingest, run status), `/reviews/*` (team review queue),
`/data-health/crosschecks*`, `/data-health/quota`, `/data-health/backtests*`.
Ingestion is CLI-only; the review queue is CLI-only (`python -m app.cli review ...`).

`GET /data-health` now returns coverage + recent runs only.

### Frontend

14 screens → 8 real pages (7 in the nav + the match detail): Today, Sessions, Match, Bets, Performance, Paper,
Data health, Settings. Removed: Calendar, Data loading, Notifications, Recorder, Backtest, and the standalone
sessions index. Nav groups 4 → 4, items 12 → 7.

### Model / tuning

| Removed from the live path | Detail |
|---|---|
| Model B v2 | Not computed when `params.probability` is set (1.6). Its index factors are 1.0. |
| Model C | Not fetched when probabilities-only mode is active (it was already ignored by Layer 3). |
| Tuning plans v1–v3, `tuning.py`, `tuning_plan.py`, `tuning_report.py`, lock file, CLI `tune` / `tuning-plan` | One evaluation path (backtest + paper) instead of two |
| `params_v1.4.yaml` | Unused |

The paper freeze still records provenance; it now stores **the params file and its SHA-256** in the existing
`plan_file` / `plan_sha256` columns (names kept to avoid a migration; no prediction has been frozen yet).

### Dependencies

`requirements.txt` 73 → 41 packages. Removed the unused Sentry/OpenTelemetry stack and the FastAPI CLI extras
(`fastapi-cli`, `typer`, `rich`, `watchfiles`, `httptools`, `websockets`, `requests`, `jinja2`, `email-validator`,
`python-multipart`, …). `pyproject.toml` now declares plain `fastapi` + `uvicorn`; run the API with
`uvicorn app.main:app --reload` instead of `fastapi dev`.

## 3. What deliberately stayed

| Kept | Why |
|---|---|
| Sealed predictions (hash chain, append-only) | The only honest proof that the model works; Gate B depends on it |
| Point-in-time repository | Prevents future information leaking into a prediction |
| Owner-only auth (password + TOTP + aal2) | Private system, no sign-ups |
| Two sessions, EAT, 50 cap, minimum odds | The product the owner actually uses |
| Backtest | Evidence for parameters and calibration |
| Cross-check machinery (FT/HT/15′) | Automatic, no manual work; still useful between ESPN and football-data.co.uk |
| Team review queue (CLI) | Needed when ESPN names a club differently; no screen, no daily work |

## 4. Free-data decisions

| Question | Answer |
|---|---|
| Do I need API-Football Pro? | **No.** The current season comes from ESPN. Pro only bought bulk goal minutes that ESPN already provides. |
| What if ESPN changes its feeds? | Keep API-Football free as a fallback for lineups; football-data.co.uk still provides scores/odds; the adapters are in git history if a second goal-minute source is needed. |
| Can I get 15′ odds for free? | No provider offers them free. Enter them manually when placing a bet; the report works without odds (probability-only mode, params 1.6). |
| What about live odds? | Optional: The Odds API free tier (500 credits/month) covers 1X2/totals, not the 15′ market. Capture is off by default. |

## 5. Verification of this pass

| Check | Result |
|---|---|
| Backend test suite | **389 passed, 98 skipped** (DB-backed tests skip without `TEST_DATABASE_URL`) |
| Frontend typecheck / lint / unit | pass (8 unit tests) |
| Import boundary / purity | unchanged (`app.calculation` and `app.decision` still import only `app.domain`) |
| OpenAPI contract | regenerated (`docs/api/openapi.json`, `frontend/src/lib/api-types.ts`) |
| Deleted tests | jobs/data-health/openfootball/football-data.org/tuning tests removed with their subjects; parser and decision tests kept |

## 6. Known leftovers (deliberate, not bugs)

1. **Database tables** for cross-checks, reviews, `raw` rows of removed sources remain. They are read-only history;
   dropping them is a separate migration once the owner is sure no old data is needed.
2. **Paper columns** `p_model_b2`, `p_market` stay nullable; the live path writes `NULL`.
3. **`params_v1.5.yaml`** remains as the legacy odds-based fixture for tests (the live file is 1.6). It is not
   referenced by the app at runtime.
4. **Docs**: `ARCHITECTURE.md`, `API.md`, `FRONTEND.md`, `DATA-HEALTH.md` and `DATA-SOURCES-…` still describe some
   removed endpoints/screens; this file is the authoritative delta.
5. **Model B2/C modules** remain in `app/calculation/models/` for legacy params and old backtests; the live path
   does not compute them.
