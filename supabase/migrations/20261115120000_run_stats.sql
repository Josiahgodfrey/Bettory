-- Store each ingestion run's statistics (matches created, teams sent to review, ...) so the API
-- can show them after the run has finished. Written once, when the run finishes.

alter table raw.ingestion_runs
  add column stats jsonb not null default '{}'::jsonb check (jsonb_typeof(stats) = 'object');

create index ingestion_runs_started on raw.ingestion_runs (started_at desc);
