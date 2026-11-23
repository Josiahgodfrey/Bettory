# ESPN as a goal-minute source

> Added 2026-11-23. ESPN's public soccer feeds (the data behind espn.com's score pages) give every goal with its
> minute, for this season and past ones, for dozens of leagues, with no key and no daily quota. BETTORY uses them to
> fill the team windows that API-Football's free plan cannot (current-season goal minutes in bulk).
> Code: `backend/app/ingestion/espn.py` (fetching), `backend/app/normalisation/parsers/espn.py` (parsing),
> `backend/app/normalisation/pairing.py` (team links), `backend/app/ingestion/pipeline.py` (`espn_history`,
> `espn_days`). Migration `supabase/migrations/20261123120000_espn_source.sql`.

## 1. Why

A fixture can be analysed only if each team's last 10 matches (overall, at home, away) all have goal minutes:
BETTORY never assumes a 0-0 (DATA-MODEL.md §7). Most of those matches are now in 2026-27. On the free plan,
API-Football gives this season's goal minutes only one match per request (100 a day for everything), openfootball
has no 2026-27 minutes, and football-data.org has none at all. So most fixtures showed "Not enough data".

## 2. Checks done before building (2026-11-23)

| Check | Result |
|---|---|
| Coverage, this season (21–22 Nov 2026, scoreboard) | 212 of 212 finished matches across 30 leagues had every goal with a minute, adding up to the score |
| History (samples) | complete in every sample back to 2018: AUT1 2023 and 2025, ENG2 2022, GER1 2020, SWE1 2024, BRA1 2023, ENG1 2018 |
| Agreement with BETTORY's stored minutes: every complete 2025-26 match of ENG1, GER1, AUT1 (881) | **877 identical**. 1 wrong in the scoreboard but right in the match summary (Forest–Leeds 2025-11-09) → goals are read from the summary only. 1 goal missing in ESPN (Blau-Weiß Linz 0–2 Ried 2025-08-23) → caught by the goals-vs-score check. 2 not paired by the quick check script (ESPN calls 1. FC Köln "FC Cologne") |
| Own goals | ESPN credits the benefiting team, as BETTORY stores them |
| Season label | ESPN's season year is the start year; with the competition's season format it gives "2026-27" or "2026" |
| Other sources considered | worldfootballR (archived Sept 2025; its pre-scraped data stopped; FotMob removed for terms of use), StatsBomb open data (selected teams and tournaments only, no current season) - not usable |

## 3. What is fetched

| Feed | Request | Used for |
|---|---|---|
| scoreboard | `/{league}/scoreboard?dates=YYYYMMDD` | one league's matches of one day: fixtures, kick-offs, scores; links ESPN's team ids |
| team schedule | `/{league}/teams/{id}/schedule[?season=YYYY]` | a team's league matches of a season (ids, dates, opponents) |
| match summary | `/{league}/summary?event={id}` | the match with **every goal and its minute** (`keyEvents`), the half-time score |

Requests are paced at 1 per second. Every response is stored as a raw payload (Supabase Storage) like any other
source, so every number stays traceable to the page it came from.

### Parsing rules

- Goals only from the summary; a summary is used only if every goal has a minute and a team of the match and the
  goal count equals the full-time score. Otherwise the match is stored with its scores but **without** goal
  minutes (INCOMPLETE, as before). Reconciliation also checks the goals against the half-time score.
- Shoot-out kicks are not goals; matches decided after extra time are skipped (their score is not the 90-minute score).
- ESPN ranks **after** the established sources (API-Football, football-data.org, openfootball) for scores, goals and
  kick-offs: it fills gaps. Where another source has the match, ESPN's version takes part in the agreement check;
  any disagreement on FT, HT or the 15' score makes the match a CONFLICT (excluded from statistics).

## 4. Linking ESPN's teams to BETTORY's teams

ESPN has its own team ids and names ("FC Cologne" for 1. FC Köln). The project rule stays: **no fuzzy name
matching**. A team is linked:

1. by its **exact normalised name** (the existing alias table), or
2. **through a match both sides hold**: an ESPN match of the same competition, within 36 hours, with the same score
   when both have one, in which the **other** team is already linked (`FIXTURE_PAIRING`). When neither team is known,
   nothing is inferred.

One ESPN id per team. Teams that cannot be linked go to the existing review queue (Data → teams to review). A wrong
link could not hide: the match's values are reconciled against the stored match, and a mismatch is a CONFLICT.

## 5. When it runs

| Job | When | What |
|---|---|---|
| **Session history** (`espn_history:<session>`) | the recorder cycle, after the session's plan (07:00 / 19:00 EAT) and before its cut-off, once | the scoreboards of the session's leagues and days; each team's schedule (this season, then last season - in the same league or another registered league of the country, for promoted and relegated teams) until it has 10 home and 10 away finished matches; the summary of each of those matches not stored yet. Matches already complete from another source are skipped. The recorder's leagues first, then every other ESPN league. Up to 1,500 requests (about 25 minutes the first time, a few minutes later) |
| **Daily forward fill** (`espn_days:daily:<date>`) | the worker, 02:00 UTC | the last 3 days of every ESPN league: scoreboards, then the summaries of finished matches not stored yet |
| **The app** | by hand | Data loading → "ESPN: league match days" / "ESPN: team histories for upcoming fixtures"; the Recorder screen shows each session's history step; Settings shows the ESPN settings (FRONTEND.md §7) |
| Command line | by hand | `python -m app.cli ingest espn-history --from 2026-11-24T10:00 --to 2026-11-24T22:00` and `python -m app.cli ingest espn-days --from 2026-08-01 --to 2026-11-23 [--comp ENG1 ...]` (season to date: league baselines) |

ESPN runs are named `espn_…`, never `paper_…`: they do **not** count against the recorder's API-Football request
bound or the API-Football quota. Settings: `backend/config/paper.yaml` → `history`, `backend/config/backfill.yaml` →
`espn`.

## 6. Leagues

`backend/config/competitions.yaml` has the ESPN code of the 9 existing competitions and **22 new leagues** (each code
checked 2026-11-23): ENG3, ENG4, SCO1, GER2, ESP2, ITA2, FRA2, NED2, BEL1, TUR1, GRE1, DEN1, RUS1, NOR1, SWE1, USA1,
MEX1, BRA1, ARG1, COL1, CHI1, AUS1.

- **Owner decisions of 2026-11-23:** the bookmaker offers the 15' markets in every registered league
  (`fifteen_minute_market: true` for all 50), and **paper betting covers all 50 leagues** at once, through
  `config/tuning_v3.yaml` (supersedes v2; only the competitions and the dates change). This was decided knowingly
  **without a prior backtest** of the added leagues, against the pre-registration rule: params 1.5 were tuned on
  ENG1, GER1 and AUT1 only. Every sealed prediction records its plan file, so v3 results can be told apart from v2's
  and league by league. The data gate and Layer 0 still apply: a league without enough history or league baseline is
  not bet until it has them. The backtest of all leagues under v3 is to be run once their history is loaded.
- **Odds are now the limit**: on the free plan the recorder may spend 12 requests per session on odds (about 10
  fixtures, in kick-off order), so most fixtures of 50 leagues are analysed and sealed without odds and get no paper
  bet. API-Football Pro (60 per session, 1,000 a day in config/paper.yaml) removes that limit.
- API-Football ids of all added leagues were checked against its `/leagues` list (1 request).
- **Second batch (same day): 19 more leagues**, every other domestic league in ESPN's index with complete current goal
  minutes: KSA1, MEX2, ENG5, SCO2, ARG2, ARG3, BRA2, URU1, PER1, ECU1, VEN1, BOL1, JPN1, HON1, CRC1, GUA1, SLV1, IND1,
  IND2 - **50 competitions** in all. API-Football ids of all 41 added leagues checked against its `/leagues` list
  (1 request). Japan: ESPN files J1 2026-27 as an August–May season.
- **Not on ESPN** (checked 2026-11-23: codes exist but hold no current data, or answer 400): **Switzerland, Finland,
  Poland, Czechia, Croatia, Serbia, Ukraine, Romania, Hungary, Israel, Cyprus, Ireland, Austria 2, Norway 2, Sweden 2,
  Denmark 2, Belgium 2, Portugal 2, Germany 3, Korea, Egypt, Morocco, the Gulf and most of Africa**. For these the
  goal minutes would come from API-Football (all are in its `/leagues` list), which on the free plan costs 1 request
  per match: practical only with Pro. Left out as well: Paraguay (API-Football splits Apertura / Clausura into two
  leagues), China and South Africa (no finished match in the last week to check), Uruguay 2 (stale).

## 7. Limits

- The feeds are not documented and could change; a failed page is counted, skipped and retried by the next run
  (run audit: `PAGES_FAILED`). If ESPN stopped working, BETTORY would fall back to what it has now.
- ESPN's terms of use limit reuse of its data: BETTORY uses it for this private tool only, slowly, and keeps it out
  of anything public.
- Odds still come from API-Football (1 request per match on the free plan): with goal minutes solved, prices are the
  remaining free-plan limit.

## 8. Going live

| Step | Status |
|---|---|
| Migration on Supabase (source `ESPN` id 5, link method `FIXTURE_PAIRING`) | ✅ 2026-11-23, after a dry run listing only this migration |
| First load on the live database: ENG1, 22 Nov 2026 | ✅ 2 finished matches COMPLETE, 4 teams linked by exact name |
| Season so far: ENG1, GER1, AUT1 from 2026-07-20; the other split-season leagues from 2026-07-20; the calendar-season leagues from 2026-01-20 | 🟡 started 2026-11-23 12:45 EAT from the owner's PC, in that order. Slow from the PC (≈ 3½ min per league-day: every query crosses to eu-west-1); resumable - an interrupted load is finished by running the same command again |
| Session history and nightly fill | run by themselves once the worker runs (`RUN_WORKER=true`, after deployment) |

Commands used for the season load (from `backend/`):

```
python -m app.cli ingest espn-days --comp ENG1 --comp GER1 --comp AUT1 --from 2026-07-20 --to 2026-11-22 --max-requests 5000
python -m app.cli ingest espn-days --comp ENG2 --comp ESP1 ... --from 2026-07-20 --to 2026-11-22 --max-requests 20000
python -m app.cli ingest espn-days --comp NOR1 --comp SWE1 --comp USA1 --comp BRA1 --comp ARG1 --comp COL1 --comp CHI1 --from 2026-01-20 --to 2026-11-22 --max-requests 20000
```

## 9. Sources considered and not used (2026-11-23)

| Source | Checked | Decision |
|---|---|---|
| **Sportmonks** (owner's free token) | Free plan covers ENG1, GER1, Danish Superliga, Scottish Premiership; about 180 calls per hour per entity. Goal events and half-time scores agree with ESPN (22 Nov: Hull–Brighton, Liverpool–Man United). Pre-match odds included (Pinnacle, bet365 and others), but of 202 markets none is a 15-minute market: "10 Minute Result", "First 10 Minutes Goals", first-goal brackets of 10 minutes, "Early Goal" = before 25:00 | Not added: it cannot price the 0-15 markets, and its minutes would repeat ESPN's (a single goal-minute source does not block a league: only an unaccepted FAILED cross-check does). Add it as a second opinion if the cross-check ever shows ESPN disagreeing, or ESPN stops |
| worldfootballR (R) | archived by its author 2025-09-18; its pre-scraped data stopped; FotMob removed for terms of use | Not used |
| StatsBomb open data | 80 competition-seasons, mostly one team's matches (Leverkusen, Barcelona, PSG); one full league season (Premier League 2015-16); no current season | Not used |
