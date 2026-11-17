# Supabase — the hosted database

> First synced 2026-11-17 (owner approval the same day). No secrets in this file: the database password and keys
> live only in the repo-root `.env` (git-ignored).

## 1. State

| Item | Value |
|---|---|
| Project | `syheclbxfmviccltkppa` (from `SUPABASE_URL`) |
| Region | eu-west-1 (Ireland), Postgres 17 |
| Migrations applied | all 8 in `supabase/migrations/` (2026-11-14 .. 2026-11-20), recorded in `supabase_migrations.schema_migrations` |
| Check 2026-11-17 | schema compared with the local database: 298 columns, 218 constraints, 22 triggers, 14 functions, 61 indexes, 16 enums, 24 tables — identical; RLS on every table; no grants and no schema usage for `anon` / `authenticated`; a rolled-back smoke test confirmed the hash chain and the append-only guards |
| Data | none yet: the backend still uses the local database (`DATABASE_URL`) |

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

`db push` applies only migrations not yet recorded in `supabase_migrations.schema_migrations`. A migration that is
already applied must never be edited: write a new one.

## 4. Still to decide

- **Moving the backend to Supabase** (`DATABASE_URL` → the pooler URL): the local database holds the loaded
  fixtures, goal minutes and run history, and the raw payloads are stored on local disk. Moving needs a data copy
  and a decision on where raw payloads live (Supabase Storage); the free plan has a 500 MB database limit.
