-- Paper betting, steps 1-2: predictions and the odds seen, frozen before kick-off (docs/PAPER-BETTING.md).
--
-- One row per fixture, session and params version, written when the session is analysed and never changed:
--   - the database stamps the recording time with its own clock and refuses a row whose kick-off is not later;
--   - UPDATE, DELETE and TRUNCATE are refused (append-only);
--   - every row carries the SHA-256 of its content and of the previous row (a hash chain), so a change made
--     around the triggers (e.g. by a superuser) is detected by paper.verify_chain().
-- Settlement and odds live in their own tables (later steps): this table only says what was predicted, when.

create schema if not exists paper;

create table paper.predictions (
  id                uuid primary key default gen_random_uuid(),
  chain_pos         bigint not null unique check (chain_pos >= 1),   -- set by the trigger
  prev_hash         text check (prev_hash ~ '^[0-9a-f]{64}$'),       -- null only for the first row
  row_hash          text not null unique check (row_hash ~ '^[0-9a-f]{64}$'),
  recorded_at       timestamptz not null,                            -- set by the trigger (database clock)
  match_id          uuid not null references core.matches (id),
  competition_code  text not null,
  kickoff_utc       timestamptz not null,
  session_id        text not null check (session_id ~ '^\d{4}-\d{2}-\d{2}-(NIGHT|NOON)$'),
  as_of             timestamptz not null,                            -- data cut-off: the session's run_start
  params_version    text not null,
  formula_version   text not null,
  plan_file         text,                                            -- tuning plan in force when frozen
  plan_sha256       text check (plan_sha256 ~ '^[0-9a-f]{64}$'),
  code_version      text,                                            -- git commit of the code that computed it
  decision          text not null,
  grade             text not null,
  failed_checks     text[] not null default '{}',
  error             text,
  p_under05         double precision check (p_under05 between 0 and 1),
  p_draw            double precision check (p_draw between 0 and 1),
  p_model_a         double precision check (p_model_a between 0 and 1),
  p_model_b2        double precision check (p_model_b2 between 0 and 1),
  p_market          double precision check (p_market between 0 and 1),
  p_market_draw     double precision check (p_market_draw between 0 and 1),
  p_league_00       double precision check (p_league_00 between 0 and 1),
  p_league_draw     double precision check (p_league_draw between 0 and 1),
  min_odds_under05  double precision check (min_odds_under05 > 1),
  min_odds_draw     double precision check (min_odds_draw > 1),
  -- the prices known at as_of (latest snapshot per bookmaker, market, line, selection captured before as_of):
  -- the offered 15' odds next to the main-market odds Model C used, sealed with the prediction
  odds_seen         jsonb not null default '[]'::jsonb check (jsonb_typeof(odds_seen) = 'array'),
  best_under05_odds double precision check (best_under05_odds > 1),   -- "Goal in 1-15 minutes: No"
  best_under05_book text,
  best_draw_odds    double precision check (best_draw_odds > 1),      -- "1x2 - 15 minutes: Draw"
  best_draw_book    text,
  check ((best_under05_odds is null) = (best_under05_book is null)),
  check ((best_draw_odds is null) = (best_draw_book is null)),
  unique (match_id, session_id, params_version),
  check (as_of < kickoff_utc),
  check ((chain_pos = 1) = (prev_hash is null))
);
create index predictions_session on paper.predictions (session_id);

-- The content a row's hash covers: the previous row's hash, then every column except row_hash. Timestamps as
-- epoch seconds (independent of the session time zone); columns are listed explicitly, so a column added later
-- never changes old hashes.
create function paper.prediction_hash(p paper.predictions) returns text
language sql immutable as $$
  select encode(sha256(convert_to(coalesce(p.prev_hash, '') || '|' || jsonb_build_object(
    'id', p.id, 'chain_pos', p.chain_pos,
    'recorded_at', extract(epoch from p.recorded_at), 'kickoff_utc', extract(epoch from p.kickoff_utc),
    'as_of', extract(epoch from p.as_of),
    'match_id', p.match_id, 'competition_code', p.competition_code, 'session_id', p.session_id,
    'params_version', p.params_version, 'formula_version', p.formula_version,
    'plan_file', p.plan_file, 'plan_sha256', p.plan_sha256, 'code_version', p.code_version,
    'decision', p.decision, 'grade', p.grade, 'failed_checks', to_jsonb(p.failed_checks), 'error', p.error,
    'p_under05', p.p_under05, 'p_draw', p.p_draw, 'p_model_a', p.p_model_a, 'p_model_b2', p.p_model_b2,
    'p_market', p.p_market, 'p_market_draw', p.p_market_draw,
    'p_league_00', p.p_league_00, 'p_league_draw', p.p_league_draw,
    'min_odds_under05', p.min_odds_under05, 'min_odds_draw', p.min_odds_draw,
    'odds_seen', p.odds_seen, 'best_under05_odds', p.best_under05_odds, 'best_under05_book', p.best_under05_book,
    'best_draw_odds', p.best_draw_odds, 'best_draw_book', p.best_draw_book
  )::text, 'UTF8')), 'hex')
$$;

create function paper.seal_prediction() returns trigger
language plpgsql as $$
declare
  last_pos  bigint;
  last_hash text;
  real_kickoff timestamptz;
begin
  perform pg_advisory_xact_lock(hashtext('paper.predictions'));   -- one writer at a time: a linear chain
  new.recorded_at := clock_timestamp();
  select kickoff_utc into real_kickoff from core.matches where id = new.match_id;
  if new.kickoff_utc is distinct from real_kickoff then
    raise exception 'paper.predictions: kick-off % differs from core.matches (%)', new.kickoff_utc, real_kickoff
      using errcode = 'check_violation';
  end if;
  if new.kickoff_utc <= new.recorded_at then
    raise exception 'paper.predictions: kick-off % is not after the recording time %', new.kickoff_utc,
      new.recorded_at using errcode = 'check_violation';
  end if;
  select chain_pos, row_hash into last_pos, last_hash from paper.predictions order by chain_pos desc limit 1;
  new.chain_pos := coalesce(last_pos, 0) + 1;
  new.prev_hash := last_hash;
  new.row_hash := paper.prediction_hash(new);
  return new;
end $$;

create function paper.refuse_change() returns trigger
language plpgsql as $$
begin
  raise exception 'paper.predictions is append-only: % refused', tg_op using errcode = 'insufficient_privilege';
end $$;

create trigger predictions_seal before insert on paper.predictions
  for each row execute function paper.seal_prediction();
create trigger predictions_no_change before update or delete on paper.predictions
  for each row execute function paper.refuse_change();
create trigger predictions_no_truncate before truncate on paper.predictions
  for each statement execute function paper.refuse_change();

-- Every problem in the chain: a missing row (gap), a broken link, or content changed after recording.
-- (Rows removed from the END leave no gap: compare the head with a published anchor, docs/PAPER-BETTING.md.)
create function paper.verify_chain() returns table (chain_pos bigint, problem text)
language sql stable as $$
  select x.chain_pos, x.problem from (
    select p.chain_pos,
           case when p.chain_pos <> row_number() over w then 'a row before this one is missing'
                when p.prev_hash is distinct from lag(p.row_hash) over w then 'link to the previous row is broken'
                when p.row_hash <> paper.prediction_hash(p) then 'content changed after recording'
           end as problem
    from paper.predictions p
    window w as (order by p.chain_pos)
  ) x
  where x.problem is not null
  order by x.chain_pos
$$;

-- Security: backend only (service role), like every raw/core table. RLS on, no policies.
alter table paper.predictions enable row level security;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    execute 'revoke all on schema paper from anon, authenticated';
    execute 'revoke all on all tables in schema paper from anon, authenticated';
  end if;
end $$;
