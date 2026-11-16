-- Data health: stored cross-source checks (with owner review) and an audit of every ingestion run.
-- docs/DATA-HEALTH.md

-- One row per cross-check of a competition-season. The latest row per season is its current status;
-- a FAIL blocks that season's data from qualifying bets until the owner accepts it after review.
create table core.crosscheck_results (
  id                     uuid primary key default gen_random_uuid(),
  competition_season_id  uuid not null references core.competition_seasons (id),
  checked_at             timestamptz not null default now(),
  run_id                 uuid references raw.ingestion_runs (id),   -- null: started by hand
  status                 text not null check (status in ('PASS', 'FAIL', 'NOT_MEASURABLE')),
  fifteen_verified       boolean not null,   -- 15' results compared between >= 2 goal-minute sources and passed
  matches                integer not null check (matches >= 0),
  multi_source           integer not null check (multi_source >= 0),
  ft_compared            integer not null check (ft_compared >= 0),
  ft_agreed              integer not null check (ft_agreed between 0 and ft_compared),
  ht_compared            integer not null check (ht_compared >= 0),
  ht_agreed              integer not null check (ht_agreed between 0 and ht_compared),
  h15_compared           integer not null check (h15_compared >= 0),
  h15_agreed             integer not null check (h15_agreed between 0 and h15_compared),
  h15_vs_ht_compared     integer not null check (h15_vs_ht_compared >= 0),
  h15_vs_ht_agreed       integer not null check (h15_vs_ht_agreed between 0 and h15_vs_ht_compared),
  min_agreement          numeric(5, 4) not null check (min_agreement between 0 and 1),
  min_compared           integer not null check (min_compared >= 1),
  failed_checks          text[] not null default '{}',
  disagreements          jsonb not null default '[]'::jsonb check (jsonb_typeof(disagreements) = 'array'),
  accepted_at            timestamptz,
  accepted_note          text,
  check ((accepted_at is null) = (accepted_note is null)),
  check (accepted_at is null or status = 'FAIL'),
  check (accepted_note is null or length(trim(accepted_note)) > 0)
);
create index crosscheck_latest on core.crosscheck_results (competition_season_id, checked_at desc);

-- Expected vs actual after every run: OK / WARNING / ERROR with the findings.
alter table raw.ingestion_runs
  add column audit_status text check (audit_status in ('OK', 'WARNING', 'ERROR')),
  add column audit jsonb not null default '[]'::jsonb check (jsonb_typeof(audit) = 'array');

-- Security: backend only (service role), like every raw/core table. RLS on, no policies.
alter table core.crosscheck_results enable row level security;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    execute 'revoke all on table core.crosscheck_results from anon, authenticated';
  end if;
end $$;
