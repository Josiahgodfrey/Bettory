-- BETTORY core data model v1.0 — see docs/DATA-MODEL.md
-- Schemas raw (downloads) and core (standard, trusted data). Strict: enums, checks, FKs,
-- append-only triggers, point-in-time retrieval functions, RLS on with no policies.

create schema if not exists raw;
create schema if not exists core;

-- ---------------------------------------------------------------------------
-- Enums (mirrored by backend/app/domain/enums.py — kept in sync by a test)
-- ---------------------------------------------------------------------------
create type core.source_kind          as enum ('API', 'OPEN_DATA', 'CSV', 'MANUAL');
create type core.competition_type     as enum ('LEAGUE', 'CUP', 'CONTINENTAL', 'FRIENDLY');
create type core.gender               as enum ('MEN', 'WOMEN');
create type core.age_group            as enum ('SENIOR', 'U23', 'U21', 'U20', 'U19', 'U18', 'U17', 'OTHER');
create type core.fixture_status       as enum ('SCHEDULED', 'LIVE', 'FINISHED', 'POSTPONED', 'CANCELLED', 'ABANDONED', 'AWARDED');
create type core.data_status          as enum ('PENDING', 'COMPLETE', 'DERIVED', 'PARTIAL_DRAW_UNKNOWN', 'INCOMPLETE');
create type core.reconciliation_status as enum ('SINGLE_SOURCE', 'AGREED', 'CONFLICT', 'MANUAL');
create type core.history_category     as enum ('GENERAL', 'HOME', 'AWAY');
create type core.entity_type          as enum ('COMPETITION', 'TEAM');
create type core.mapping_method       as enum ('EXACT_ID', 'ALIAS', 'MANUAL');
create type core.review_status        as enum ('OPEN', 'RESOLVED', 'REJECTED');
create type core.side                 as enum ('HOME', 'AWAY');
create type core.market_code          as enum ('FT_1X2', 'FT_TOTAL', 'HT_TOTAL', 'HT_ASIAN_TOTAL', 'FIRST15_RESULT', 'FIRST15_GOAL', 'OTHER');
create type core.snapshot_type        as enum ('OPENING', 'ANALYSIS', 'BET_TIME', 'CLOSING');
create type core.severity             as enum ('INFO', 'WARN', 'BLOCKING');
create type core.run_status           as enum ('RUNNING', 'SUCCEEDED', 'FAILED');

-- ---------------------------------------------------------------------------
-- Shared trigger functions
-- ---------------------------------------------------------------------------
create function core.forbid_update_delete() returns trigger
language plpgsql as $$
begin
  raise exception 'table %.% is append-only (% not allowed)', tg_table_schema, tg_table_name, tg_op;
end $$;

create function core.touch_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end $$;

-- ---------------------------------------------------------------------------
-- Sources
-- ---------------------------------------------------------------------------
create table core.sources (
  id          smallint primary key,
  code        text not null unique check (code ~ '^[A-Z][A-Z0-9_]*$'),
  name        text not null,
  kind        core.source_kind not null,
  base_url    text
);

insert into core.sources (id, code, name, kind, base_url) values
  (1, 'API_FOOTBALL',        'API-Football',        'API',       'https://v3.football.api-sports.io'),
  (2, 'FOOTBALL_DATA_ORG',   'football-data.org',   'API',       'https://api.football-data.org/v4'),
  (3, 'OPENFOOTBALL',        'openfootball',        'OPEN_DATA', 'https://raw.githubusercontent.com/openfootball/football.json/master'),
  (4, 'FOOTBALL_DATA_CO_UK', 'football-data.co.uk', 'CSV',       'https://www.football-data.co.uk'),
  (9, 'MANUAL',              'Manual entry',        'MANUAL',    null);

-- ---------------------------------------------------------------------------
-- Raw downloads
-- ---------------------------------------------------------------------------
create table raw.ingestion_runs (
  id             uuid primary key default gen_random_uuid(),
  source_id      smallint not null references core.sources (id),
  job_name       text not null,
  started_at     timestamptz not null default now(),
  finished_at    timestamptz,
  status         core.run_status not null default 'RUNNING',
  requests_used  integer not null default 0 check (requests_used >= 0),
  items_stored   integer not null default 0 check (items_stored >= 0),
  error          text,
  check ((status = 'RUNNING') = (finished_at is null)),
  check (finished_at is null or finished_at >= started_at)
);

create table raw.payloads (
  id            uuid primary key default gen_random_uuid(),
  run_id        uuid not null references raw.ingestion_runs (id),
  source_id     smallint not null references core.sources (id),
  endpoint      text not null,
  params        jsonb not null default '{}'::jsonb,
  http_status   smallint check (http_status between 100 and 599),
  storage_path  text not null,
  sha256        char(64) not null check (sha256 ~ '^[0-9a-f]{64}$'),
  byte_size     integer not null check (byte_size >= 0),
  fetched_at    timestamptz not null default now(),
  unique (source_id, sha256)
);
create trigger payloads_append_only before update or delete on raw.payloads
  for each row execute function core.forbid_update_delete();

-- ---------------------------------------------------------------------------
-- Competitions, seasons, teams
-- ---------------------------------------------------------------------------
create table core.competitions (
  id                uuid primary key default gen_random_uuid(),
  name              text not null check (length(trim(name)) > 0),
  country           text not null,                 -- 'World' for international
  competition_type  core.competition_type not null,
  level             smallint check (level between 1 and 10),
  gender            core.gender not null default 'MEN',
  age_group         core.age_group not null default 'SENIOR',
  timezone          text not null default 'UTC',   -- for sources giving local kick-off times
  is_active         boolean not null default false,
  has_15min_market  boolean not null default false,
  coverage_tier     smallint check (coverage_tier between 1 and 3),
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  unique (country, name, gender, age_group)
);
create trigger competitions_touch before update on core.competitions
  for each row execute function core.touch_updated_at();

create table core.competition_seasons (
  id              uuid primary key default gen_random_uuid(),
  competition_id  uuid not null references core.competitions (id),
  label           text not null check (label ~ '^\d{4}(-\d{2})?$'),   -- '2025-26' or '2025'
  start_date      date,
  end_date        date,
  is_current      boolean not null default false,
  unique (competition_id, label),
  check (end_date is null or start_date is null or end_date >= start_date)
);
create unique index competition_seasons_one_current
  on core.competition_seasons (competition_id) where is_current;

create table core.teams (
  id          uuid primary key default gen_random_uuid(),
  name        text not null check (length(trim(name)) > 0),
  short_name  text,
  country     text not null,
  gender      core.gender not null default 'MEN',
  is_national boolean not null default false,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create trigger teams_touch before update on core.teams
  for each row execute function core.touch_updated_at();

create table core.team_aliases (
  id                uuid primary key default gen_random_uuid(),
  team_id           uuid not null references core.teams (id),
  alias             text not null,
  normalized_alias  text not null check (normalized_alias = lower(normalized_alias) and length(normalized_alias) > 0),
  country           text not null,
  source_id         smallint references core.sources (id),
  created_at        timestamptz not null default now(),
  unique (country, normalized_alias)
);

create table core.provider_entity_map (
  id            uuid primary key default gen_random_uuid(),
  source_id     smallint not null references core.sources (id),
  entity_type   core.entity_type not null,
  provider_key  text not null check (length(provider_key) > 0),
  entity_id     uuid not null,
  method        core.mapping_method not null,
  created_at    timestamptz not null default now(),
  unique (source_id, entity_type, provider_key)
);
create trigger provider_entity_map_append_only before update or delete on core.provider_entity_map
  for each row execute function core.forbid_update_delete();

create table core.entity_review_queue (
  id                  uuid primary key default gen_random_uuid(),
  source_id           smallint not null references core.sources (id),
  entity_type         core.entity_type not null,
  provider_key        text not null,
  provider_name       text not null,
  context             jsonb not null default '{}'::jsonb,
  status              core.review_status not null default 'OPEN',
  resolved_entity_id  uuid,
  created_at          timestamptz not null default now(),
  resolved_at         timestamptz,
  unique (source_id, entity_type, provider_key),
  check ((status = 'RESOLVED') = (resolved_entity_id is not null)),
  check ((status = 'OPEN') = (resolved_at is null))
);

-- ---------------------------------------------------------------------------
-- Matches (trusted)
-- ---------------------------------------------------------------------------
create table core.matches (
  id                     uuid primary key default gen_random_uuid(),
  competition_season_id  uuid not null references core.competition_seasons (id),
  kickoff_utc            timestamptz not null,
  kickoff_date           date generated always as ((kickoff_utc at time zone 'UTC')::date) stored,
  home_team_id           uuid not null references core.teams (id),
  away_team_id           uuid not null references core.teams (id),
  status                 core.fixture_status not null,
  round                  text,
  ft_home                smallint check (ft_home >= 0),
  ft_away                smallint check (ft_away >= 0),
  ht_home                smallint check (ht_home >= 0),
  ht_away                smallint check (ht_away >= 0),
  h15_home               smallint check (h15_home >= 0),
  h15_away               smallint check (h15_away >= 0),
  goal_before_15         boolean,
  first_goal_minute      smallint check (first_goal_minute between 1 and 130),
  first_goal_extra       smallint check (first_goal_extra between 1 and 30),
  first_goal_team_id     uuid references core.teams (id),
  data_status            core.data_status not null default 'PENDING',
  reconciliation_status  core.reconciliation_status not null default 'SINGLE_SOURCE',
  scores_source_id       smallint references core.sources (id),
  goals_source_id        smallint references core.sources (id),
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),

  unique (home_team_id, away_team_id, kickoff_date),
  check (home_team_id <> away_team_id),
  -- score pairs: both set or both null
  check ((ft_home is null) = (ft_away is null)),
  check ((ht_home is null) = (ht_away is null)),
  check ((h15_home is null) = (h15_away is null)),
  -- ordering 15' <= HT <= FT
  check (ht_home is null or ft_home is null or (ht_home <= ft_home and ht_away <= ft_away)),
  check (h15_home is null or ht_home is null or (h15_home <= ht_home and h15_away <= ht_away)),
  -- finished matches have a full-time score
  check (status <> 'FINISHED' or ft_home is not null),
  -- unplayed matches carry no scores and are PENDING
  check (status not in ('SCHEDULED', 'POSTPONED', 'CANCELLED') or (ft_home is null and data_status = 'PENDING')),
  -- goal_before_15 consistent with the 15' score
  check (h15_home is null or goal_before_15 = (h15_home + h15_away > 0)),
  -- data_status consistency (docs/DATA-MODEL.md section 7)
  check (
    (data_status in ('COMPLETE', 'DERIVED') and h15_home is not null and goal_before_15 is not null)
    or (data_status = 'PARTIAL_DRAW_UNKNOWN' and h15_home is null and goal_before_15 is true)
    or (data_status in ('INCOMPLETE', 'PENDING') and h15_home is null and goal_before_15 is null)
  ),
  -- first goal fields
  check ((first_goal_minute is null) = (first_goal_team_id is null)),
  check (first_goal_extra is null or first_goal_minute is not null),
  check (first_goal_team_id is null or first_goal_team_id in (home_team_id, away_team_id))
);
create index matches_home_kickoff on core.matches (home_team_id, kickoff_utc desc);
create index matches_away_kickoff on core.matches (away_team_id, kickoff_utc desc);
create index matches_season_kickoff on core.matches (competition_season_id, kickoff_utc);
create index matches_status_kickoff on core.matches (status, kickoff_utc);

create table core.match_revisions (
  id           bigint generated always as identity primary key,
  match_id     uuid not null,
  operation    text not null check (operation in ('UPDATE', 'DELETE')),
  old_row      jsonb not null,
  changed_at   timestamptz not null default now()
);
create trigger match_revisions_append_only before update or delete on core.match_revisions
  for each row execute function core.forbid_update_delete();

create function core.record_match_revision() returns trigger
language plpgsql as $$
begin
  insert into core.match_revisions (match_id, operation, old_row) values (old.id, tg_op, to_jsonb(old));
  if tg_op = 'DELETE' then
    return old;
  end if;
  new.updated_at := now();
  return new;
end $$;
create trigger matches_revision before update or delete on core.matches
  for each row execute function core.record_match_revision();

-- ---------------------------------------------------------------------------
-- Per-source match records (append-only)
-- ---------------------------------------------------------------------------
create table core.match_source_records (
  id                 uuid primary key default gen_random_uuid(),
  source_id          smallint not null references core.sources (id),
  provider_match_key text not null check (length(provider_match_key) > 0),
  payload_id         uuid not null references raw.payloads (id),
  match_id           uuid references core.matches (id),
  provider_competition_key text,
  kickoff_utc        timestamptz,
  home_name          text not null,
  away_name          text not null,
  home_team_id       uuid references core.teams (id),
  away_team_id       uuid references core.teams (id),
  status             core.fixture_status not null,
  ft_home            smallint check (ft_home >= 0),
  ft_away            smallint check (ft_away >= 0),
  ht_home            smallint check (ht_home >= 0),
  ht_away            smallint check (ht_away >= 0),
  goals              jsonb not null default '[]'::jsonb check (jsonb_typeof(goals) = 'array'),
  has_goal_minutes   boolean not null,
  parser_version     text not null,
  parsed_at          timestamptz not null default now(),
  unique (source_id, provider_match_key, payload_id),
  check ((ft_home is null) = (ft_away is null)),
  check ((ht_home is null) = (ht_away is null)),
  check (ht_home is null or ft_home is null or (ht_home <= ft_home and ht_away <= ft_away))
);
create index msr_match on core.match_source_records (match_id);
create index msr_unlinked on core.match_source_records (source_id) where match_id is null;
-- Append-only, except linking match_id / home_team_id / away_team_id once each (null -> value).
create function core.msr_guard() returns trigger
language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'core.match_source_records is append-only (DELETE not allowed)';
  end if;
  if (to_jsonb(new) - 'match_id' - 'home_team_id' - 'away_team_id')
       = (to_jsonb(old) - 'match_id' - 'home_team_id' - 'away_team_id')
     and (old.match_id is null or new.match_id = old.match_id)
     and (old.home_team_id is null or new.home_team_id = old.home_team_id)
     and (old.away_team_id is null or new.away_team_id = old.away_team_id) then
    return new;
  end if;
  raise exception 'core.match_source_records is append-only (only linking match/team ids once is allowed)';
end $$;
create trigger msr_append_only before update or delete on core.match_source_records
  for each row execute function core.msr_guard();

-- ---------------------------------------------------------------------------
-- Trusted goals and lineups
-- ---------------------------------------------------------------------------
create table core.match_goals (
  id             uuid primary key default gen_random_uuid(),
  match_id       uuid not null references core.matches (id) on delete cascade,
  sequence       smallint not null check (sequence >= 1),
  team_id        uuid not null references core.teams (id),   -- team credited (own goals: benefiting team)
  minute         smallint not null check (minute between 1 and 130),
  extra_minute   smallint check (extra_minute between 1 and 30),
  is_own_goal    boolean not null default false,
  is_penalty     boolean not null default false,
  player_name    text,
  source_id      smallint not null references core.sources (id),
  unique (match_id, sequence)
);

create table core.match_lineups (
  id          uuid primary key default gen_random_uuid(),
  match_id    uuid not null references core.matches (id),
  team_id     uuid not null references core.teams (id),
  formation   text,
  players     jsonb not null check (jsonb_typeof(players) = 'array'),
  source_id   smallint not null references core.sources (id),
  payload_id  uuid references raw.payloads (id),
  fetched_at  timestamptz not null default now()
);
create index match_lineups_match on core.match_lineups (match_id, fetched_at desc);

-- ---------------------------------------------------------------------------
-- Bookmakers and odds (append-only)
-- ---------------------------------------------------------------------------
create table core.bookmakers (
  id                  uuid primary key default gen_random_uuid(),
  name                text not null unique,
  first15_cutoff      text not null default '14:59' check (first15_cutoff ~ '^\d{2}:\d{2}$'),
  own_goals_count     boolean not null default true,
  is_users_bookmaker  boolean not null default false,
  created_at          timestamptz not null default now()
);

create table core.odds_snapshots (
  id                    uuid primary key default gen_random_uuid(),
  match_id              uuid not null references core.matches (id),
  bookmaker_id          uuid not null references core.bookmakers (id),
  source_id             smallint not null references core.sources (id),
  payload_id            uuid references raw.payloads (id),
  market                core.market_code not null,
  provider_market_name  text not null,
  selection             text not null check (selection ~ '^[A-Z0-9_.+-]+$'),
  line                  numeric(5,2),
  odds                  numeric(8,3) not null check (odds > 1.0 and odds < 1000),
  snapshot_type         core.snapshot_type not null,
  captured_at           timestamptz not null
);
create index odds_match_market on core.odds_snapshots (match_id, market, captured_at desc);
create trigger odds_append_only before update or delete on core.odds_snapshots
  for each row execute function core.forbid_update_delete();

-- ---------------------------------------------------------------------------
-- Data quality
-- ---------------------------------------------------------------------------
create table core.data_quality_issues (
  id           uuid primary key default gen_random_uuid(),
  entity_type  text not null,
  entity_id    uuid,
  source_id    smallint references core.sources (id),
  issue_code   text not null check (issue_code ~ '^[A-Z][A-Z0-9_]*$'),
  severity     core.severity not null,
  detail       jsonb not null default '{}'::jsonb,
  status       core.review_status not null default 'OPEN',
  created_at   timestamptz not null default now(),
  resolved_at  timestamptz,
  check ((status = 'OPEN') = (resolved_at is null))
);
create index dq_open on core.data_quality_issues (issue_code) where status = 'OPEN';

-- ---------------------------------------------------------------------------
-- Point-in-time retrieval functions
-- ---------------------------------------------------------------------------
create function core.team_history(
  p_team_id uuid, p_category core.history_category, p_as_of timestamptz, p_limit integer default 10
) returns setof core.matches
language sql stable as $$
  select m.*
  from core.matches m
  join core.competition_seasons cs on cs.id = m.competition_season_id
  join core.competitions c on c.id = cs.competition_id
  where m.status = 'FINISHED'
    and m.kickoff_utc < p_as_of
    and m.reconciliation_status <> 'CONFLICT'
    and c.competition_type <> 'FRIENDLY'
    and case p_category
          when 'GENERAL' then p_team_id in (m.home_team_id, m.away_team_id)
          when 'HOME'    then m.home_team_id = p_team_id
          when 'AWAY'    then m.away_team_id = p_team_id
        end
  order by m.kickoff_utc desc, m.id
  limit least(greatest(p_limit, 1), 50)
$$;

create function core.h2h(
  p_team_a uuid, p_team_b uuid, p_as_of timestamptz, p_limit integer default 10,
  p_since timestamptz default null
) returns setof core.matches
language sql stable as $$
  select m.*
  from core.matches m
  join core.competition_seasons cs on cs.id = m.competition_season_id
  join core.competitions c on c.id = cs.competition_id
  where m.status = 'FINISHED'
    and m.kickoff_utc < p_as_of
    and m.kickoff_utc >= coalesce(p_since, p_as_of - interval '5 years')
    and m.reconciliation_status <> 'CONFLICT'
    and c.competition_type <> 'FRIENDLY'
    and ((m.home_team_id = p_team_a and m.away_team_id = p_team_b)
      or (m.home_team_id = p_team_b and m.away_team_id = p_team_a))
  order by m.kickoff_utc desc, m.id
  limit least(greatest(p_limit, 1), 50)
$$;

create function core.competition_matches(p_competition_season_id uuid, p_as_of timestamptz)
returns setof core.matches
language sql stable as $$
  select m.*
  from core.matches m
  where m.competition_season_id = p_competition_season_id
    and m.status = 'FINISHED'
    and m.kickoff_utc < p_as_of
    and m.reconciliation_status <> 'CONFLICT'
  order by m.kickoff_utc, m.id
$$;

-- ---------------------------------------------------------------------------
-- Security: backend only (service role). RLS on, no policies.
-- ---------------------------------------------------------------------------
do $$
declare t record;
begin
  for t in select schemaname, tablename from pg_tables where schemaname in ('raw', 'core') loop
    execute format('alter table %I.%I enable row level security', t.schemaname, t.tablename);
  end loop;
  if exists (select 1 from pg_roles where rolname = 'anon') then
    execute 'revoke all on schema raw, core from anon, authenticated';
    execute 'revoke all on all tables in schema raw, core from anon, authenticated';
    execute 'revoke all on all functions in schema raw, core from anon, authenticated';
  end if;
end $$;
