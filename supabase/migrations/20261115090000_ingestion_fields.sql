-- Fields needed by the ingestion pipeline (docs/DATA-MODEL.md).

-- Registry code from backend/config/competitions.yaml, e.g. 'ENG1'.
alter table core.competitions
  add column code text unique check (code ~ '^[A-Z]{3}[0-9]$'),
  -- The single source allowed to create teams for this competition: set by the first source that
  -- creates one; every other source must match existing teams or go to the review queue.
  add column team_source_id smallint references core.sources (id);

-- Each source's date and round, needed to rebuild a source's version for reconciliation.
alter table core.match_source_records
  add column kickoff_date date not null,
  add column round text;

create index msr_source_key on core.match_source_records (source_id, provider_match_key, parsed_at desc);
