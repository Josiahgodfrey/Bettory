# BETTORY — Data Models and Ingestion: What Was Built

**Status:** implemented on branch `data-models-logic` (Phase 1 — data foundation)
**Date:** 2026-11-15
**Related docs:** [DATA-MODEL.md](DATA-MODEL.md) (the design), [PHASE-0-FINDINGS.md](PHASE-0-FINDINGS.md) (what each source really gives), [ARCHITECTURE.md](ARCHITECTURE.md), [DEVELOPMENT-PLAN.md](DEVELOPMENT-PLAN.md)

This document describes what the code does today: how match data is fetched from the four free sources, turned into one standard format, checked, combined into a single trusted record per match, and stored in PostgreSQL (Supabase). [DATA-MODEL.md](DATA-MODEL.md) explains *why* the model looks like this. This file explains *what is implemented and where*.

---

## 1. The goal of this layer

The pricing models (A, L, B v2, C) need one thing above all: for every past match, a **trustworthy answer to "what was the score at 15 minutes?"**, together with FT and HT scores and odds. No single free source gives that for every league, so this layer:

1. downloads data from each source and keeps an exact copy (raw payload);
2. converts each source's format into one standard shape (`SourceMatch`, `SourceOdds`);
3. maps every team name or provider id to one internal team, **without creating duplicates**;
4. stores each source's version of a match separately (append-only);
5. combines all versions into one trusted `core.matches` row, marking disagreements as `CONFLICT`;
6. derives the 15-minute score only when the evidence proves it, and otherwise marks it `INCOMPLETE`. It never guesses.

---

## 2. Data flow

```
 Source API / file
        │  ingestion/<source>.py   (HTTP only: rate limit, retries, request budget)
        ▼
  Fetched (raw bytes)
        │  storage/raw_store.py    gzip file, path = source/yyyy/mm/dd/<sha256>.<ext>.gz
        │  repositories/raw.py     raw.payloads row  (same sha256 → skip, idempotent)
        ▼
  normalisation/parsers/<source>.py   (pure: bytes → ParseResult)
        ▼
  SourceMatch / SourceOdds  (domain/models.py, strict validation)
        │  ingestion/loader.py
        ├─ team resolution:  provider id map → alias → single team creator → review queue
        ├─ core.match_source_records   (append-only, one row per source per payload)
        ├─ find (home, away, ±1 day) or create core.matches
        ├─ latest_records()  →  normalisation/reconcile.py  →  calculation/recovery.py
        └─ apply_trusted():  core.matches, core.match_goals, core.data_quality_issues
                             core.odds_snapshots (only for matches already linked)
```

Each payload is loaded in **one database transaction**. If anything fails, nothing from that payload is half-written. Every job writes a `raw.ingestion_runs` row: `RUNNING`, then `SUCCEEDED` or `FAILED` with the error.

---

## 3. Database (Supabase migrations)

| Migration | What it does |
|---|---|
| [20261114120000_core_data_model.sql](../supabase/migrations/20261114120000_core_data_model.sql) | Creates the `raw` and `core` schemas, 16 enums and 17 tables, plus the triggers, CHECK rules, point-in-time read functions and RLS |
| [20261115090000_ingestion_fields.sql](../supabase/migrations/20261115090000_ingestion_fields.sql) | Adds the fields the pipeline needs: `competitions.code` (e.g. `ENG1`), `competitions.team_source_id` (the single team creator), `match_source_records.kickoff_date` and `round`, and the index `msr_source_key` |

### 3.1 Main tables

| Table | Holds | Rule |
|---|---|---|
| `raw.ingestion_runs` | One row per job run | Status, requests used, error |
| `raw.payloads` | One row per downloaded file/response | Append-only; unique `(source, sha256)` |
| `core.sources` | The 4 sources and MANUAL | Fixed ids: 1, 2, 3, 4 and 9 |
| `core.competitions`, `core.competition_seasons` | Leagues and seasons | `code` must look like `ENG1` |
| `core.teams`, `core.team_aliases` | Internal teams and every known spelling | Alias unique per (country, normalised name) |
| `core.provider_entity_map` | Provider id or name → internal team | Unique per (source, type, key) |
| `core.entity_review_queue` | Names that could not be matched safely | Waits for a manual decision |
| `core.match_source_records` | Each source's version of a match | Append-only; `match_id` and team ids can be set **once** |
| `core.matches` | The single trusted match | Strict CHECKs (below); every change is copied to `core.match_revisions` |
| `core.match_goals` | Trusted goal list with minutes | Rebuilt whenever the trusted match changes |
| `core.odds_snapshots` | Odds per bookmaker, market and selection | Append-only; odds must be > 1 |
| `core.data_quality_issues` | Conflicts and inconsistencies | One open issue per (entity, code) |

### 3.2 Rules the database enforces itself

The database rejects bad data even if the Python code has a bug:

- Scores are non-negative, and come in pairs (both home and away, or neither).
- **15' ≤ HT ≤ FT** for both teams.
- `goal_before_15` must agree with the 15' score.
- `data_status` must agree with the data: `COMPLETE`/`DERIVED` need a 15' score; `INCOMPLETE`, `PARTIAL_DRAW_UNKNOWN` and `PENDING` must not have one.
- A `SCHEDULED` match has no scores. A `FINISHED` match must have an FT score.
- A first-goal minute needs a first-goal team.
- No team plays itself. Only one match per (home, away, kickoff date).
- Raw payloads, source records and odds cannot be updated or deleted (trigger `forbid_update_delete`, guard `msr_guard`).
- RLS is on for every table with no policies, and `anon`/`authenticated` are revoked. Only the backend can read or write.

### 3.3 Point-in-time read functions

These are used by the calculation layer later. They return only matches that kicked off **strictly before `as_of`**. They exclude `CONFLICT` matches and friendlies.

- `core.team_history(team, category GENERAL|HOME|AWAY, as_of, limit)`
- `core.h2h(team_a, team_b, as_of)`
- `core.competition_matches(...)`

---

## 4. Code, file by file

All paths are under `backend/`.

### 4.1 Configuration

| File | Purpose |
|---|---|
| [config/competitions.yaml](../backend/config/competitions.yaml) | Competition registry: ENG1, ENG2, GER1, AUT1, ESP1, ITA1, FRA1, NED1, POR1. Each entry has its id or key at every provider, time zone, season format and which sources may create teams |
| [config/params_v1.4.yaml](../backend/config/params_v1.4.yaml) | Model parameters, used in later phases |
| [app/core/settings.py](../backend/app/core/settings.py) | Reads `.env` (repo root, then `backend/.env`): database, raw-store choice, API keys, Telegram, owner id. Secrets are `SecretStr` and never printed |

### 4.2 Domain — the standard format

| File | Purpose |
|---|---|
| [app/domain/enums.py](../backend/app/domain/enums.py) | Python copies of the DB enums. `test_enum_parity` fails if they drift from the database. Also `SourceCode` and `SOURCE_IDS` |
| [app/domain/models.py](../backend/app/domain/models.py) | The standard shapes every parser must produce. All are frozen and reject unknown fields. **`Score`**. **`SourceGoal`**: side, minute, extra minute, own goal, penalty, player; `from_label("90+4")`. **`SourceMatch`**: source, provider key, competition code, season, kickoff date/UTC, names, provider team keys, status, FT, HT, goals, `has_goal_minutes`; its validators reject impossible data; `goals_consistent()` checks the goal list against FT and HT. **`SourceOdds`**: market, selection, line, odds > 1, snapshot type |
| [app/domain/registry.py](../backend/app/domain/registry.py) | Loads `competitions.yaml`. Looks up a competition by provider key (`by_provider`). Season helpers: `season_label`, `season_start_year`, `fdcouk_season_code` ("2025-26" → "2526") |

### 4.3 Normalisation — pure logic

These functions take no database connection and make no network calls.

| File | Purpose |
|---|---|
| [app/normalisation/names.py](../backend/app/normalisation/names.py) | `normalize_team_name`: lower case, strip accents and punctuation, drop "FC/AFC/CF" and similar tokens. Used for alias matching |
| [app/normalisation/parsers/\_\_init\_\_.py](../backend/app/normalisation/parsers/__init__.py) | `ParseError` (the payload is unusable, e.g. an API error body) and `ParseResult` (matches, odds, skipped counts by reason) |
| [parsers/api_football.py](../backend/app/normalisation/parsers/api_football.py) | `parse_fixtures`: fixtures, with goal events when present. Missed penalties are ignored. `parse_odds`: maps the bookmaker markets to BETTORY markets, including **"1x2 - 15 minutes" → FIRST15_RESULT** and **"Goal in 1-15 minutes" → FIRST15_GOAL**, plus HT/FT totals |
| [parsers/football_data_org.py](../backend/app/normalisation/parsers/football_data_org.py) | `parse_matches`: kickoff, FT and HT, with team ids as provider keys. No goal minutes |
| [parsers/openfootball.py](../backend/app/normalisation/parsers/openfootball.py) | `parse_season_file`: plain files (FT/HT) and **-full files (goal minutes)**. Handles times like `20:00 UTC+1`. In a full file, a 0-0 written without goal lists counts as complete |
| [parsers/football_data_co_uk.py](../backend/app/normalisation/parsers/football_data_co_uk.py) | `parse_csv`: FT/HT and Pinnacle closing odds (1X2, O/U 2.5) as `CLOSING` snapshots. Rejects a file that is missing required columns. Not verified on a real file yet (§8) |
| [app/normalisation/reconcile.py](../backend/app/normalisation/reconcile.py) | `reconcile(records)` → `TrustedMatch` (see §5.3) |
| [app/calculation/recovery.py](../backend/app/calculation/recovery.py) | `derive_fifteen(...)`: the 15-minute rules (see §5.4) |

### 4.4 Ingestion — fetching (HTTP only, no parsing)

| File | Purpose |
|---|---|
| [app/ingestion/base.py](../backend/app/ingestion/base.py) | `Fetched` (raw bytes plus a description of the request). `HttpSource` waits a minimum interval between requests, retries 3 times on network errors, 429 and 5xx (honouring `Retry-After`), and enforces a hard request budget. TLS verification is always on |
| [app/ingestion/api_football.py](../backend/app/ingestion/api_football.py) | Fixtures by date, by league and season, one fixture with events, and odds. Default budget 90 requests per run (the free plan allows 100 a day) |
| [app/ingestion/football_data_org.py](../backend/app/ingestion/football_data_org.py) | Competition matches per season, 6.5 s between requests (the free tier allows 10 a minute) |
| [app/ingestion/openfootball.py](../backend/app/ingestion/openfootball.py) | Season JSON file from GitHub, plain or `-full` |
| [app/ingestion/football_data_co_uk.py](../backend/app/ingestion/football_data_co_uk.py) | Division CSV per season (e.g. `2526/E0.csv`) |
| [app/ingestion/loader.py](../backend/app/ingestion/loader.py) | `Loader`: team resolution, source records, match linking, reconciliation, odds (see §5) |
| [app/ingestion/pipeline.py](../backend/app/ingestion/pipeline.py) | `Job` (run row, raw storage, sha256 skip, one transaction per payload) and one job function per source (§6) |

### 4.5 Storage

| File | Purpose |
|---|---|
| [app/storage/db.py](../backend/app/storage/db.py) | Builds the SQLAlchemy/psycopg engine. TLS is required except for localhost. On the Supabase pooler (port 6543), prepared statements are switched off |
| [app/storage/raw_store.py](../backend/app/storage/raw_store.py) | `LocalRawStore` (`backend/data/raw`, git-ignored) and `SupabaseRawStore` (private bucket `raw-payloads`). Files are gzipped, named by content hash, and never overwritten |
| [repositories/raw.py](../backend/app/storage/repositories/raw.py) | `start_run`, `finish_run`, `insert_payload` (returns `is_new=False` if the same content was already stored) |
| [repositories/reference.py](../backend/app/storage/repositories/reference.py) | Competitions, seasons, teams, aliases, provider map, `claim_team_source`, review queue (`queue_for_review`, `open_reviews`, `resolve_review`) |
| [repositories/matches.py](../backend/app/storage/repositories/matches.py) | `insert_source_record`, `link_source_record`, `find_match` (same pairing, ±1 day), `create_match`, `latest_records`, `apply_trusted`, `record_issue`, bookmakers, `insert_odds` |

### 4.6 Command line

[app/cli.py](../backend/app/cli.py) runs jobs and handles the review queue (§6). The same jobs can be started over HTTP (§6.1).

### 4.7 Placeholder files (later phases)

These files exist with a one-line docstring only. They will be written in Phases 2 to 6:

- `calculation/*`: models A, L, B v2 and C, Poisson, devig, pricing, EV
- `decision/*`, `jobs/*`, `settlement/*`, `notifications/telegram.py`, `backtest/*`
- `api/routers/*` other than `health`
- `storage/point_in_time.py`, `storage/repositories/{competitions,fixtures,odds,ops,results}.py`
- `normalisation/{id_mapping,match_builder,validation}.py`. Their job is currently done by `loader.py`, `parsers/` and the `models.py` validators. They will be merged or removed when those phases start.

---

## 5. Key logic explained

### 5.1 Team resolution — no duplicate teams

For every team name or provider id, `Loader.team_id` tries these steps in order:

1. **Provider map.** This source already mapped this id or name → use it.
2. **Alias.** The normalised name equals a known alias in the same country → use it and remember the mapping (`ALIAS`).
3. **Single team creator.** Only one source per competition may create new teams. The first allowed source (from `team_sources` in the registry) claims `competitions.team_source_id`. Only that source may create teams from then on (`EXACT_ID`).
4. **Review queue.** Otherwise the name goes to `core.entity_review_queue`, and its source record is stored but not linked to a match (`records_waiting_for_team_review`).

This fixed a real bug. Before the single-creator rule, API-Football created its own copy of 7 teams that openfootball had already created, because their spellings differed beyond what name normalisation removes. Normalisation is deliberately conservative: it strips accents, punctuation and tokens like FC/AFC, and does no fuzzy matching. A review is resolved with `review resolve <review_id> <team_id>`. That adds the mapping and the alias, so the name matches automatically next time.

### 5.2 Source records and match linking

- Every parsed match becomes a `core.match_source_records` row, even when its teams are unknown. The insert is `ON CONFLICT DO NOTHING`, because the table is append-only.
- The trusted match is found by **(home, away, kickoff date ±1 day)**, which allows for time zones and reschedules. If none is found, a match is created, but only if the source gives a kickoff time.
- A record is linked to its match once. The DB trigger forbids changing that link.

### 5.3 Reconciliation (`reconcile.py`)

`latest_records()` rebuilds **one version per source**, taking scores and status from that source's latest record. Goal minutes come from the source's latest record *that has minutes*, and only if that record's FT and HT equal the latest scores. So a plain openfootball file can never erase minutes from the full file. This was bug 1, now fixed and covered by a test.

`reconcile()` then:

| Field | Priority |
|---|---|
| Scores (FT/HT) | API-Football → football-data.org → openfootball → football-data.co.uk |
| Goal minutes | API-Football → openfootball (only goal lists consistent with that source's FT and HT) |
| Kickoff time | API-Football → football-data.org → football-data.co.uk → openfootball |

Result:

- **CONFLICT**: two finished sources disagree on FT, HT, or the 15' score derived from goals. `BLOCKING` issues are recorded. CONFLICT matches are excluded from all statistics.
- **AGREED**: two or more finished sources and no disagreement.
- **SINGLE_SOURCE**: only one finished source.
- A goal list that does not add up to FT/HT is ignored, with a `GOALS_INCONSISTENT` warning.

### 5.4 15-minute derivation (`recovery.py`)

The rules are applied in this order:

1. The match was not played → `PENDING` (scheduled, live, postponed, cancelled) or `INCOMPLETE` (abandoned, awarded).
2. There is a consistent goal list → `COMPLETE`. The 15' score is counted from goals whose minute label is ≤ 15. The first goal (minute and team) is recorded.
3. HT is 0-0 (or FT is 0-0) → `DERIVED` 0-0. No goal before 15' is then certain.
4. Anything else → `INCOMPLETE`. **It is never assumed to be 0-0.**

`PARTIAL_DRAW_UNKNOWN` exists in the schema (a goal before 15' is known but the 15' score is not), but no source produces it yet.

**Selection-bias rule** ([DATA-MODEL.md](DATA-MODEL.md) §7.1): `INCOMPLETE` matches are never silently dropped from a team's window or a league baseline. Dropping them would make the 0-15 period look goal-less more often than it really is. If too many are missing, the window is marked `INSUFFICIENT_DATA`.

### 5.5 Writing the trusted match

`apply_trusted()` updates `core.matches` only when a value actually changed (`IS DISTINCT FROM`). Each change is copied to `core.match_revisions` by trigger. The goal list in `core.match_goals` is rebuilt at the same time. Data-quality issues are deduplicated while open.

### 5.6 Odds

Odds are stored only for a match that is already linked through that source's provider key (`odds_without_linked_match` otherwise). The bookmaker is created on first sight. Every snapshot is append-only, so odds history is never lost.

---

## 6. How to run it

Run these from `backend/` with the venv active. The database comes from `.env`.

```bash
# History with goal minutes (best free 15' source)
python -m app.cli ingest openfootball --comp ENG1 --season 2025-26 --full
# Second opinion on FT/HT
python -m app.cli ingest football-data-org --comp ENG1 --season 2025-26
# Closing odds (currently blocked on this network)
python -m app.cli ingest football-data-co-uk --comp ENG1 --season 2025-26
# API-Football season (free plan: seasons 2022-2024 only)
python -m app.cli ingest api-football-season --comp ENG1 --season 2024-25
# Daily: fixtures of the day, goal minutes for finished, odds (incl. 15') for upcoming
python -m app.cli ingest api-football-day --date 2026-11-14 --events 20 --odds 20

# Team names that need a decision
python -m app.cli review list
python -m app.cli review resolve <review_id> <team_id>
```

Add `--reparse` to parse payloads again that were already stored, for example after a parser fix. Each command prints its statistics: matches created, teams matched by alias, `reconciliation:AGREED`, `data_status:COMPLETE` and so on.

### 6.1 The same jobs over the API

These endpoints are in [routers/jobs.py](../backend/app/api/routers/jobs.py) and [routers/reviews.py](../backend/app/api/routers/reviews.py). All of them are owner-only (§6.2).

| Method and path | Does |
|---|---|
| `GET /jobs/competitions` | Registry competitions, their sources and season format |
| `POST /jobs/ingest/openfootball` | `{competition, season, full, reparse}` |
| `POST /jobs/ingest/football-data-org` | `{competition, season, reparse}` |
| `POST /jobs/ingest/football-data-co-uk` | `{competition, season, reparse}` |
| `POST /jobs/ingest/api-football-season` | `{competition, season, reparse}` |
| `POST /jobs/ingest/api-football-day` | `{date, max_event_requests, max_odds_requests, reparse}`; 1 + events + odds must be ≤ 90 |
| `GET /jobs/runs?source=&status=&limit=` | Latest runs, from the API, the CLI and later the scheduler |
| `GET /jobs/runs/{run_id}` | Status, requests used, error and statistics of one run |
| `GET /reviews` | Open team-name reviews |
| `POST /reviews/{review_id}/resolve` | `{team_id}`: maps the name to that team and adds the alias |
| `GET /teams?q=&country=` | Find a team id, searching names and aliases |

How the ingest endpoints behave:

- The request is checked first: the competition exists and has that source; the season is written the competition's way (`2025-26` or `2025`); the API key is configured; unknown fields are rejected. Nothing starts if the check fails (422 or 503).
- The run row is created, then **202** is returned at once with `run_id`. The job runs in the background ([ingestion/runner.py](../backend/app/ingestion/runner.py)). Poll `GET /jobs/runs/{run_id}`.
- **One job per source at a time.** A second request for the same source gets 409, because two jobs would share one request quota.
- If the job crashes, even before the pipeline starts, the run is marked `FAILED` with the error. A run never stays `RUNNING` because of an error. It can stay `RUNNING` if the server process is stopped in the middle of a job.
- Run statistics are stored in `raw.ingestion_runs.stats` (migration [20261115120000_run_stats.sql](../supabase/migrations/20261115120000_run_stats.sql)).

### 6.2 Owner-only access

This is in [api/deps.py](../backend/app/api/deps.py). Every endpoint except `/health` requires `Authorization: Bearer <Supabase access token>`, and fails closed:

| Check | Refused with |
|---|---|
| Token missing, malformed, bad signature, expired, wrong audience or issuer | 401 |
| Signature verified with the project's public keys (JWKS); HS256 only if `SUPABASE_JWT_SECRET` is set; `alg: none` never | 401 |
| `sub` ≠ `OWNER_USER_ID` | 403 |
| `aal` ≠ `aal2` (two-factor not completed) | 403 |
| `OWNER_USER_ID` or `SUPABASE_URL` not configured | 503 |

**Local testing before the frontend exists:** set `AUTH_LOCAL_BYPASS=true` and `ENVIRONMENT=local`. Requests without a token are then accepted **only from this machine** (127.0.0.1). The app refuses to start if the bypass is on outside `local`, and the interactive `/docs` page is also off outside `local`.

**Recommended order for a new competition:** openfootball `--full` first, then football-data.org, then the rest. Sources that come later match the teams created by the first one.

---

## 7. Tests and real results

**122 tests pass**:
```bash
TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:54329/postgres pytest
```
This uses the Docker `postgres:17` container `bettory-pg`. The integration tests are skipped when `TEST_DATABASE_URL` is not set.

| Test file | Covers |
|---|---|
| `unit/test_enum_parity.py` | Python enums = DB enums |
| `unit/test_domain_models.py` | Validators, minute labels, goal consistency |
| `unit/test_recovery_and_reconcile.py` | 15' rules, priorities, conflicts |
| `unit/test_parsers.py` | All four parsers on real payload slices (football-data.co.uk: synthetic CSV) |
| `integration/test_core_schema.py` | Every DB CHECK, append-only triggers, link-once, point-in-time functions, RLS |
| `integration/test_ingestion_pipeline.py` | End to end across sources: no duplicate teams, minutes not erased, idempotent re-runs, review queue |
| `unit/test_api_auth.py` | Every refused token case (401/403/503), the local bypass limited to this machine and to `local` |
| `integration/test_api_jobs.py` | Ingest through the API into a real database, statistics, failed runs recorded, lock released, 409 when busy, invalid requests, review resolve flow |

**Real dev-database runs (Premier League):**

| Season | Result |
|---|---|
| 2025-26 | 380 matches `COMPLETE` and `AGREED`; 1,045 goals; **71.8 % with no goal before 15'** |
| 2024-25 | 380 matches `AGREED` across 3 sources; 0 conflicts |
| | 23 teams in total, no duplicates |

---

## 8. Phase 3 — data pipeline infrastructure (branch `data-pipeline`)

### 8.1 Where goal minutes come from (checked 2026-11-16)

Every 15' value needs **goal minutes**. Coverage of the free sources:

| Source | Seasons with goal minutes | Notes |
|---|---|---|
| openfootball (`-full` JSON and the source `.txt` repositories) | **2025-26 only**, England 1, Germany 1, Austria 1 | 2026-27 and all older seasons: FT/HT only |
| API-Football free | league seasons 2022–2024, or any match by id | 1 request per match, 100 per day; 2026-27 history cannot be listed (dates ±1 day) |
| API-Football Pro | all seasons | 20 matches per request (`ids`), 7,500 requests per day |

Consequence: without a paid API-Football plan, current-season team windows stay `INSUFFICIENT_DATA` until enough new matches with minutes are collected (months), and the backtest has one season only.

### 8.2 New pieces

| Piece | File | Does |
|---|---|---|
| Point-in-time reader | [storage/point_in_time.py](../backend/app/storage/point_in_time.py) | Fixture, team windows, H2H, competition matches, seasons, odds, all as known strictly before `as_of`; every row is checked a second time (`PointInTimeError`) |
| Analysis builder | [analysis/builder.py](../backend/app/analysis/builder.py) | `AnalysisContext(conn, params, registry, as_of)`: pooled baselines, windows, Model B v2 history with per-match competition baselines, Models A / L / B v2 / C, price, H2H, favourite odds → `FixtureAnalysis` → `verdict()`. Used by the session run and the backtest |
| Cross-source check | [normalisation/crosscheck.py](../backend/app/normalisation/crosscheck.py) | FT, HT and 15' agreement between sources, plus "15' from goals fits every other source's HT"; lists each disagreement with each source's value |
| API-Football backfill | `pipeline.api_football_backfill` | Season fixtures, then goal minutes for finished fixtures without API-Football minutes: 20 per request on Pro, 1 on free. **Resumable** and capped per run |

Baseline pooling: the fixture's own season is always included; older seasons are pooled only when complete. Excluding a whole season a source does not cover is unrelated to match outcomes, unlike dropping single INCOMPLETE matches.

### 8.3 Commands

```bash
python -m app.cli crosscheck --comp ENG1 --season 2025-26 --show 10
python -m app.cli ingest api-football-backfill --comp ENG1 --season 2024-25 --max-requests 50
```

API: `POST /jobs/ingest/api-football-backfill` with `{competition, season, max_requests?}`.

Settings (`.env`): `API_FOOTBALL_PLAN=free|pro` (bulk `ids` only on `pro`), `API_FOOTBALL_MAX_REQUESTS_PER_RUN` (default 90, the hard cap of one run).

New configuration: `fifteen_minute_market` per competition in [competitions.yaml](../backend/config/competitions.yaml) — set to `true` for the 9 leagues as an **assumption to confirm with your bookmaker**; and `model_c.bookmaker_priority` in the params file.

### 8.4 Results on the development database

| Competition | Sources | FT agree | HT agree | 15' vs other sources' HT |
|---|---|---|---|---|
| Premier League 2024-25 | 3 | 380/380 | 380/380 | — (no minutes) |
| Premier League 2025-26 | 2 | 380/380 | 380/380 | 380/380 |
| Bundesliga 2025-26 | 2 | 306/306 | 306/306 | 306/306 |
| Austrian Bundesliga 2025-26 | 1 (loaded with minutes) | — | — | — |

The full **15'-vs-15'** comparison (exit criterion ≥ 98%) needs a second goal-minute source and runs after the API-Football upgrade. Loading the Bundesliga second source sent 7 differently spelled team names to the review queue (no duplicates created); they were resolved manually.

The analysis builder priced the last 90 matches of each of the Premier League and the Bundesliga 2025-26 point in time and reproduced the earlier prototype exactly: 129 QUALIFIED / 51 SKIP, no errors.

---

## 9. Known limits and open items

| Item | Status |
|---|---|
| Goal minutes | Only 2025-26 (ENG1, GER1, AUT1) until the API-Football upgrade (§8.1) |
| football-data.co.uk | Blocked by the network's Sophos TLS interception. Certificate checks are deliberately **not** disabled. The parser is tested on a synthetic CSV only |
| API-Football league ids | Only 39 (Premier League) is verified. 40/78/218/140/135/61/88/94 must be checked |
| `fifteen_minute_market` flags | Assumed `true`; confirm per league with your bookmaker |
| Migrations on Supabase | Applied to local Docker only. Applying them to the Supabase project changes the live database and needs the owner's go-ahead |
| DB password | It was in git history. **Rotate it** in Supabase |
| `PARTIAL_DRAW_UNKNOWN` | No source produces it yet |

---

## 10. What comes next

After the API-Football upgrade: backfill 3+ seasons of the pilot leagues with goal minutes, run the 15' cross-check (≥ 98%), then the Phase 4 backtest (Gate B).
