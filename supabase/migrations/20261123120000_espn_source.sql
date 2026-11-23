-- ESPN as a goal-minute source (docs/ESPN.md): ESPN's public scoreboard / team schedule / match summary feeds
-- (no key). Cross-checked 2026-11-23 against 881 complete 2025-26 matches (ENG1, GER1, AUT1): 877 identical
-- goal lists from the match summary; ESPN's few gaps (a missing goal) are caught by the goals-vs-score check.
--
-- FIXTURE_PAIRING: an ESPN team id linked to a BETTORY team because an ESPN match and a stored match of the same
-- competition agree on date, kick-off and score (and the other team is already linked, or both names agree).

insert into core.sources (id, code, name, kind, base_url) values
  (5, 'ESPN', 'ESPN', 'API', 'https://site.api.espn.com/apis/site/v2/sports/soccer')
on conflict (id) do nothing;

alter type core.mapping_method add value if not exists 'FIXTURE_PAIRING';
