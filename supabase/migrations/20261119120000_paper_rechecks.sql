-- The re-check of a listed match about an hour before kick-off (ARCHITECTURE.md 12.7, docs/PAPER-BETTING.md 10).
-- Owner decisions 2026-11-17:
--   - the re-check runs the full verification again with everything known at that moment (fresh odds, the
--     lineups stored); a match that no longer qualifies is WITHDRAWN. Lineups are stored and shown; there is no
--     lineup-specific rule until one is defined and tested;
--   - a withdrawn match's paper bet is settled VOID.
-- One-way: a re-check can only withdraw (QUALIFIED -> WITHDRAWN), never add. Append-only; each row is tied to the
-- sealed prediction by its hash, stamped by the database clock and refused at or after kick-off; nothing can be
-- recorded for a prediction after its withdrawal.

create table paper.rechecks (
  id                uuid primary key default gen_random_uuid(),
  prediction_id     uuid not null references paper.predictions (id),
  prediction_hash   text not null,
  checked_at        timestamptz not null,                 -- set by the trigger (database clock)
  status            text not null check (status in ('CONFIRMED', 'LINEUPS_PENDING', 'WITHDRAWN')),
  lineups_home      boolean not null,                     -- a lineup of the team was stored before the check
  lineups_away      boolean not null,
  decision          text not null,                        -- the verdict of the re-verification
  failed_checks     text[] not null default '{}',
  checks            jsonb not null default '[]'::jsonb check (jsonb_typeof(checks) = 'array'),
  p_under05         double precision check (p_under05 between 0 and 1),
  p_draw            double precision check (p_draw between 0 and 1),
  min_odds_under05  double precision check (min_odds_under05 > 1),
  min_odds_draw     double precision check (min_odds_draw > 1),
  check ((status = 'WITHDRAWN') = (decision <> 'QUALIFIED'))
);
create index rechecks_prediction on paper.rechecks (prediction_id, checked_at desc);

create function paper.seal_recheck() returns trigger
language plpgsql as $$
declare
  p_hash   text;
  p_kick   timestamptz;
  p_dec    text;
begin
  select row_hash, kickoff_utc, decision into p_hash, p_kick, p_dec
    from paper.predictions where id = new.prediction_id;
  if new.prediction_hash is distinct from p_hash then
    raise exception 'prediction hash does not match the sealed prediction';
  end if;
  if p_dec <> 'QUALIFIED' then
    raise exception 'only a listed (QUALIFIED) prediction is re-checked, this one is %', p_dec;
  end if;
  new.checked_at := clock_timestamp();
  if new.checked_at >= p_kick then
    raise exception 're-check at % is not before kick-off %', new.checked_at, p_kick;
  end if;
  if exists (select 1 from paper.rechecks r where r.prediction_id = new.prediction_id and r.status = 'WITHDRAWN') then
    raise exception 'the prediction was already withdrawn: a withdrawal is final';
  end if;
  return new;
end $$;
create trigger rechecks_seal before insert on paper.rechecks
  for each row execute function paper.seal_recheck();
create trigger rechecks_no_change before update or delete on paper.rechecks
  for each row execute function paper.refuse_change();
create trigger rechecks_no_truncate before truncate on paper.rechecks
  for each statement execute function paper.refuse_change();

alter table paper.rechecks enable row level security;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    execute 'revoke all on all tables in schema paper from anon, authenticated';
  end if;
end $$;
