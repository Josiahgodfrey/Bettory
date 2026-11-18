-- Telegram notifications outbox (ARCHITECTURE.md 12.8, app/notifications).
-- Every message is queued once under a stable event key (e.g. "report:2026-11-21-NOON:1.5"), so a recorder cycle
-- that runs every few minutes never sends the same message twice; delivery is retried up to 3 times and messages
-- older than a day are expired instead of being sent late. Messages hold a summary and a link only, never odds.

create schema if not exists ops;

create table ops.notifications (
  id          uuid primary key default gen_random_uuid(),
  event_key   text not null unique check (length(event_key) between 3 and 200),
  kind        text not null check (kind in ('REPORT_READY', 'NO_MATCHES', 'WITHDRAWN', 'FAILURE', 'LOGIN', 'TEST')),
  message     text not null check (length(message) between 1 and 4000),
  created_at  timestamptz not null default clock_timestamp(),
  status      text not null default 'PENDING' check (status in ('PENDING', 'SENT', 'FAILED', 'EXPIRED')),
  attempts    smallint not null default 0 check (attempts between 0 and 10),
  sent_at     timestamptz,
  last_error  text,                                    -- never contains the bot token
  check ((status = 'SENT') = (sent_at is not null))
);
create index notifications_pending on ops.notifications (created_at) where status = 'PENDING';

alter table ops.notifications enable row level security;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    execute 'revoke all on schema ops from anon, authenticated';
    execute 'revoke all on all tables in schema ops from anon, authenticated';
  end if;
end $$;
