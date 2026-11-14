# BETTORY — Data Model

**Storage and retrieval of football data · v1.0 · 2026-11-14**

Companion to [ARCHITECTURE.md](ARCHITECTURE.md) and [PHASE-0-FINDINGS.md](PHASE-0-FINDINGS.md).
Implemented in `supabase/migrations/20261114120000_core_data_model.sql` and `backend/app/domain/`.

Scope: everything needed to **fetch, store, reconcile and read** match data from the four sources. Calculation results (`calc`) and decisions/bets (`output`) get their own migrations later.

---

## 1. Principles

| # | Principle |
|---|---|
| D1 | **BETTORY's own standard format.** No source's format is copied; every source is converted into it. |
| D2 | **Raw first.** Every download is stored unchanged (file in Supabase Storage + `raw.payloads` row with SHA-256). Parsing can be re-run without re-downloading. |
| D3 | **Each source's version is kept** (`core.match_source_records`, append-only). The trusted version lives in `core.matches`. |
| D4 | **One internal ID per real-world thing** (competition, team, match), linked to each source's IDs/names through `core.provider_entity_map`. Unknown names go to a review queue — never guessed. |
| D5 | **One `matches` table** for scheduled and finished matches (status field); a match keeps one ID for life. |
| D6 | **The database enforces the rules**: enums, CHECK constraints, foreign keys, unique keys, append-only triggers. |
| D7 | **Point-in-time reads**: retrieval functions take `as_of` and never return matches kicking off at or after it. |
| D8 | **Every change to a trusted match is recorded** (`core.match_revisions`). |

---

## 2. Schemas and tables

| Schema | Table | Purpose | Write rule |
|---|---|---|---|
| `raw` | `ingestion_runs` | One row per fetch job run | insert + finish update |
| `raw` | `payloads` | One row per downloaded file / API response | **append-only** |
| `core` | `sources` | The data sources | seed |
| `core` | `competitions` | Leagues, cups, continental competitions | upsert |
| `core` | `competition_seasons` | Season per competition (`2025-26`, `2025`) | upsert |
| `core` | `teams` | Teams | upsert |
| `core` | `team_aliases` | Known alternative names per team | insert |
| `core` | `provider_entity_map` | Source ID/name → internal ID | insert |
| `core` | `entity_review_queue` | Unmatched names awaiting a decision | insert + resolve |
| `core` | `matches` | **Trusted** match record | upsert (revisions logged) |
| `core` | `match_revisions` | Previous versions of each match row | **append-only** (trigger) |
| `core` | `match_source_records` | Each source's version of a match | **append-only** |
| `core` | `match_goals` | Trusted goal events | replace per match on reconciliation |
| `core` | `match_lineups` | Lineups (API-Football) | insert |
| `core` | `bookmakers` | Bookmakers and their 15' settlement rules | upsert |
| `core` | `odds_snapshots` | Odds per match / bookmaker / market / time | **append-only** |
| `core` | `data_quality_issues` | Conflicts, missing data, impossible values | insert + resolve |

---

## 3. Entity relationships

```
sources ─┬─< raw.ingestion_runs ─< raw.payloads
         │
         ├─< provider_entity_map >── competitions / teams
         │
competitions ─< competition_seasons ─< matches >── teams (home, away)
                                         │
                                         ├─< match_source_records >── sources, raw.payloads
                                         ├─< match_goals >── teams
                                         ├─< match_lineups
                                         ├─< match_revisions
                                         └─< odds_snapshots >── bookmakers, sources
teams ─< team_aliases
```

---

## 4. Key tables in detail

### 4.1 `core.matches` (trusted)

| Field | Type | Rule |
|---|---|---|
| id | uuid | |
| competition_season_id | uuid → competition_seasons | |
| kickoff_utc | timestamptz | |
| kickoff_date | date (generated, UTC) | |
| home_team_id, away_team_id | uuid → teams | must differ |
| status | `fixture_status` | SCHEDULED / LIVE / FINISHED / POSTPONED / CANCELLED / ABANDONED / AWARDED |
| round | text | |
| ft_home, ft_away | smallint | both null or both set; ≥ 0; required when FINISHED |
| ht_home, ht_away | smallint | ≤ FT |
| h15_home, h15_away | smallint | score at end of 0–15; ≤ HT |
| goal_before_15 | boolean | always known when data_status ≠ INCOMPLETE/PENDING; equals (h15 total > 0) when h15 known |
| first_goal_minute, first_goal_extra, first_goal_team_id | | |
| data_status | `data_status` | PENDING / COMPLETE / DERIVED / PARTIAL_DRAW_UNKNOWN / INCOMPLETE — consistency enforced by CHECK |
| reconciliation_status | `reconciliation_status` | SINGLE_SOURCE / AGREED / CONFLICT / MANUAL |
| scores_source_id, goals_source_id | → sources | which source supplied the trusted values |
| created_at, updated_at | | |

**Unique:** `(home_team_id, away_team_id, kickoff_date)` — the same pairing cannot be stored twice for one day.

### 4.2 `core.match_source_records` (per source, append-only)

| Field | Note |
|---|---|
| source_id, provider_match_key | e.g. API-Football fixture id; for files: `date|home|away` |
| payload_id → raw.payloads | which download it came from |
| match_id | linked after team matching; null until then |
| kickoff_utc, home_name, away_name, home_team_id, away_team_id | as given by the source + resolved IDs |
| status, ft/ht scores | |
| goals (jsonb) | list of `{side, minute, extra, own_goal, penalty, player}` |
| has_goal_minutes | does this source provide goal minutes for this match? |
| parser_version, parsed_at | |

**Unique:** `(source_id, provider_match_key, payload_id)`.

### 4.3 `core.odds_snapshots` (append-only)

| Field | Note |
|---|---|
| match_id, bookmaker_id, source_id, payload_id | |
| market | `market_code`: FT_1X2, FT_TOTAL, HT_TOTAL, HT_ASIAN_TOTAL, FIRST15_RESULT, FIRST15_GOAL, OTHER |
| provider_market_name | e.g. `1x2 - 15 minutes`, `Goal in 1-15 minutes`, `Goals Over/Under First Half` |
| selection | e.g. HOME / DRAW / AWAY / OVER / UNDER / YES / NO |
| line | numeric (0.5, 1.5, 2.5…) or null |
| odds | numeric(8,3), > 1.0 and < 1000 |
| snapshot_type | OPENING / ANALYSIS / BET_TIME / CLOSING |
| captured_at | |

---

## 5. Source → standard format mapping (from Phase 0)

| Standard field | API-Football | football-data.org | openfootball | football-data.co.uk |
|---|---|---|---|---|
| provider_match_key | `fixture.id` | `match.id` | `date|team1|team2` | `Div|Date|HomeTeam|AwayTeam` |
| kickoff_utc | `fixture.date` (ISO, tz) | `utcDate` | `date` + `time` (local → competition timezone) | `Date` + `Time` (UK time — to confirm) |
| home / away name | `teams.home.name` | `homeTeam.name` | `team1` / `team2` | `HomeTeam` / `AwayTeam` |
| status | `fixture.status.short` (FT, AET, PEN, PST, CANC, NS…) | `status` (FINISHED, SCHEDULED…) | played if `score.ft` present | row present = played |
| FT score | `score.fulltime` | `score.fullTime` | `score.ft` or list `[h,a]` | `FTHG`, `FTAG` |
| HT score | `score.halftime` | `score.halfTime` | `score.ht` (absent for `[0,0]` → 0-0) | `HTHG`, `HTAG` |
| goals + minutes | `/fixtures/events` → `time.elapsed`, `time.extra`, `detail` (Normal Goal / Own Goal / Penalty / Missed Penalty) | ❌ free tier | `-full` files: `goals1/goals2[].minute` ("90+4"), `owngoal`, `penalty` | ❌ |
| odds | `/odds` (bookmaker, bet name, values) | ❌ add-on | ❌ | Pinnacle and market columns (to confirm, V5) |

**Status mapping (API-Football → standard):** NS/TBD → SCHEDULED; 1H/HT/2H/ET/BT/P/LIVE → LIVE; FT/AET/PEN → FINISHED; PST → POSTPONED; CANC → CANCELLED; ABD → ABANDONED; AWD/WO → AWARDED.

---

## 6. Reconciliation rules

| Field | Source priority (first available wins) |
|---|---|
| Goal minutes, first goal, 15' score | API-Football → openfootball (`-full`) |
| HT / FT scores | API-Football → football-data.org → openfootball → football-data.co.uk |
| Kick-off time | API-Football → football-data.org → others |
| Historical odds (backtest) | football-data.co.uk |
| Live odds incl. 15' markets | API-Football |

| Situation | Result |
|---|---|
| One source only | `SINGLE_SOURCE` |
| Several sources, HT/FT and goal-derived 15' agree | `AGREED` |
| Any disagreement on HT, FT or 15' | `CONFLICT` + `data_quality_issues` row; **excluded from statistics** until resolved |
| Resolved by you | `MANUAL` |

Goal-list checks before a source's goals are trusted: number of goals per side = FT score; goals with minute ≤ 45 (incl. 45+x) = HT score. A failing check marks that source's goals unusable for the match.

---

## 7. 15-minute fields

Derived from goals using the bookmaker cutoff (provider minute label ≤ 15 = inside 0–15):

| Data available | data_status | h15 | goal_before_15 |
|---|---|---|---|
| Full goal list | COMPLETE | set | set |
| First-goal minute + HT score (Data doc §13) | DERIVED | set | set |
| First goal ≤ 15 and both teams scored before HT, second goal time unknown | PARTIAL_DRAW_UNKNOWN | null | **true** |
| No minute information | INCOMPLETE | null | null |
| Not played yet | PENDING | null | null |

---

## 8. Team and competition matching

1. `(source, provider_key)` already in `provider_entity_map` → use it.
2. Normalised name (lower-case, accents removed, punctuation and suffixes like "FC", "AFC", "CF" stripped) equals a `team_aliases.normalized_alias` **within the same country** → map it (method `ALIAS`).
3. Otherwise → `entity_review_queue` (status OPEN). The match record is stored with `home_team_id`/`away_team_id` null and is not used until resolved.

No fuzzy matching is applied automatically (strictness rule). The review queue is resolved by you, and the decision is stored as a `MANUAL` mapping.

---

## 9. Retrieval functions

| Function | Returns |
|---|---|
| `core.team_history(team_id, category, as_of, limit = 10)` | Last `limit` FINISHED matches of the team before `as_of` (GENERAL / HOME / AWAY), excluding friendlies and CONFLICT matches, newest first, tie-break by id |
| `core.h2h(team_a, team_b, as_of, limit = 10, since = as_of − 5 years)` | Past meetings, any venue |
| `core.competition_matches(competition_season_id, as_of)` | Finished matches of a season before `as_of` (baselines) |

Indexes: `(home_team_id, kickoff_utc desc)`, `(away_team_id, kickoff_utc desc)`, `(competition_season_id, kickoff_utc)`, `(status, kickoff_utc)`.

---

## 10. Security

- No table is in the `public` schema; `raw` and `core` are not exposed through the Supabase REST API.
- Row-level security is **enabled on every table with no policies** → only the backend (service role) can read or write. Frontend access comes later through read-only `api` views.
- `anon` and `authenticated` roles have no privileges on `raw` or `core`.
