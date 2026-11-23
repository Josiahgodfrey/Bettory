# Supabase — the hosted database

> First synced 2026-11-17 (owner approval the same day). No secrets in this file: the database password and keys
> live only in the repo-root `.env` (git-ignored).

## 1. State

| Item | Value |
|---|---|
| Project | `syheclbxfmviccltkppa` (from `SUPABASE_URL`) |
| Region | eu-west-1 (Ireland), Postgres 17 |
| Migrations applied | all 9 in `supabase/migrations/` (2026-11-14 .. 2026-11-23; the 9th, `20261123120000_espn_source.sql`, pushed 2026-11-23 after a dry run), recorded in `supabase_migrations.schema_migrations` |
| Check 2026-11-17 | schema compared with the local database: 298 columns, 218 constraints, 22 triggers, 14 functions, 61 indexes, 16 enums, 24 tables — identical; RLS on every table; no grants and no schema usage for `anon` / `authenticated`; a rolled-back smoke test confirmed the hash chain and the append-only guards |
| Data | copied 2026-11-17 from the local database (`pg_dump` data-only + `psql` in one transaction): 24 tables, every row identical (checksums by id) |
| Backend | uses Supabase since 2026-11-17: `.env` `SUPABASE_PROJECT_REF`, `DATABASE_HOST` (pooler), `DATABASE_PORT=5432`, `DATABASE_PASSWORD`; the local database is kept as `LOCAL_DATABASE_URL` (frozen copy, not updated any more) |

## 2. Connecting

The direct host `db.<ref>.supabase.co` is IPv6 only, and this machine has no IPv6 route. Use the **session
pooler** (IPv4, port 5432):

```
postgresql://postgres.<ref>:<DATABASE_PASSWORD>@aws-0-eu-west-1.pooler.supabase.com:5432/postgres?sslmode=require
```

(the user name is `postgres.<project ref>`; the password must be URL-encoded if it has special characters)

## 3. Pushing new migrations

From the repo root, with the URL above in **double quotes** (in `cmd.exe` an unquoted `&` in the URL splits the
command — that is how the first "dry run" became a real push on 2026-11-17):

```
supabase db push --db-url "<pooler url>" --dry-run      # see what would be applied
supabase db push --db-url "<pooler url>"                # apply
```

The Supabase CLI reads `BETTORY/.env` and refuses a variable name with a hyphen (`unexpected character '-' in
variable name`, 2026-11-23: a non-BETTORY entry). Remove such lines from `.env`, or run the CLI from a folder that
holds only a copy of `supabase/`.

`db push` applies only migrations not yet recorded in `supabase_migrations.schema_migrations`. A migration that is
already applied must never be edited: write a new one.

## 4. Latency — the backend must run next to the database

Measured 2026-11-17 from the owner's PC (Tanzania) to eu-west-1: **≈ 250 ms per query** (local Docker: < 1 ms).
Everything works, but screens that run an analysis are slow, because an analysis makes hundreds of small queries:

| Endpoint | From the PC to Supabase | Locally before |
|---|---|---|
| `GET /sessions/{id}` (preview) | 93 s | 0.4 s |
| `GET /matches/{id}/analysis` | 72 s | < 1 s |
| `GET /paper/status` | 31 s | — |
| `GET /dashboard` | 11 s | — |
| other screens | 1 – 4 s | — |

The fix is the plan of ARCHITECTURE.md §5 option A: run the backend (API + worker, one container) on a host **in
or near eu-west-1** (≈ 1–2 ms per query). The nightly backfill and the recorder cycle work from the PC meanwhile
(slower, still well within their schedules).

## 5. Tests never touch Supabase

`backend/tests/conftest.py` blanks the database, Telegram, API and storage settings before any test runs (environment
variables win over `.env`); integration tests use `TEST_DATABASE_URL` (local Docker). Checked 2026-11-17: a full run
left Supabase unchanged.

## 6. Still open

- ~~Raw payload files~~ **done 2026-11-18**: the 224 files referenced by `raw.payloads` were copied to the private
  Storage bucket `raw-payloads` (`backend/scripts/upload_raw_to_supabase.py`: each file checked against its row's
  sha256 and size, uploaded without overwrite, read back and verified: 224 / 224). The bucket is private (the public
  URL and the browser key both get 400). `.env` now has `RAW_STORE=supabase`, so new payloads go straight to
  Storage. 5 local files not referenced by any row (repeated fetches) were left on disk.
- A backfill run of 2026-11-17 00:30 UTC is stuck in RUNNING (0 requests; its process ended without finishing the
  row). Harmless.
