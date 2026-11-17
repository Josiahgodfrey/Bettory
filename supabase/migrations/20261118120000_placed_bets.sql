-- Real bets placed by the owner (docs/API.md step 3). Owner decisions 2026-11-17:
--   - a bet that breaks a rule is recorded and flagged (rule_breaches), never refused: the money is staked;
--   - a recorded bet is never edited: a mistake is cancelled (betting.bet_cancellations, with a reason) and
--     recorded again;
--   - one currency for all bets (config/betting.yaml: TZS).
-- Settlement is not stored: it is read from the match's stored 15' result every time (WIN / LOSE / VOID /
-- PENDING), so a corrected score is reflected at once.

create schema if not exists betting;

create table betting.placed_bets (
  id              uuid primary key default gen_random_uuid(),
  recorded_at     timestamptz not null,                  -- set by the trigger (database clock)
  match_id        uuid not null references core.matches (id),
  session_id      text not null check (session_id ~ '^\d{4}-\d{2}-\d{2}-(NIGHT|NOON)$'),
  prediction_id   uuid references paper.predictions (id), -- the frozen report row the bet follows (null: none)
  market          text not null check (market in ('UNDER05', 'DRAW')),
  odds            numeric(8,3) not null check (odds > 1 and odds < 1000),
  stake           numeric(14,2) not null check (stake > 0),
  currency        text not null check (currency ~ '^[A-Z]{3}$'),
  bookmaker       text not null check (length(trim(bookmaker)) > 0),
  placed_at       timestamptz not null,                   -- when the owner placed it (given by the owner)
  min_odds        double precision check (min_odds > 1),  -- the frozen minimum odds of the market, if listed
  rule_breaches   text[] not null default '{}',           -- e.g. NOT_LISTED, BELOW_MIN_ODDS, BOTH_MARKETS
  note            text
);
create index placed_bets_match on betting.placed_bets (match_id);
create index placed_bets_placed on betting.placed_bets (placed_at desc);

create function betting.seal_bet() returns trigger
language plpgsql as $$
begin
  new.recorded_at := clock_timestamp();
  if new.placed_at > new.recorded_at + interval '5 minutes' then
    raise exception 'placed_at % is in the future', new.placed_at;
  end if;
  return new;
end $$;
create trigger placed_bets_seal before insert on betting.placed_bets
  for each row execute function betting.seal_bet();
create trigger placed_bets_append_only before update or delete on betting.placed_bets
  for each row execute function core.forbid_update_delete();
create trigger placed_bets_no_truncate before truncate on betting.placed_bets
  for each statement execute function core.forbid_update_delete();

create table betting.bet_cancellations (
  id            uuid primary key default gen_random_uuid(),
  bet_id        uuid not null unique references betting.placed_bets (id),  -- a bet is cancelled at most once
  cancelled_at  timestamptz not null default clock_timestamp(),
  reason        text not null check (length(trim(reason)) >= 3)
);
create trigger bet_cancellations_append_only before update or delete on betting.bet_cancellations
  for each row execute function core.forbid_update_delete();
create trigger bet_cancellations_no_truncate before truncate on betting.bet_cancellations
  for each statement execute function core.forbid_update_delete();

alter table betting.placed_bets enable row level security;
alter table betting.bet_cancellations enable row level security;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    execute 'revoke all on schema betting from anon, authenticated';
    execute 'revoke all on all tables in schema betting from anon, authenticated';
  end if;
end $$;
