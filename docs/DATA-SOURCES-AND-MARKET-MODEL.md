# BETTORY — Data Sources & Market-Implied Model

**Addendum to [SYSTEM-DOCUMENTATION.md](SYSTEM-DOCUMENTATION.md) · v0.4 · 2026-11-14**

This document details the topics that were only touched on (or not covered) in the main system documentation:

- Free data sources, their limits, and how the system uses them
- How to stay inside free API quotas
- How odds are collected (including the 15-minute market gap)
- **Model C** — estimating 15-minute odds from Half-Time and Full-Time totals odds
- Updated formula (v1.1), schema additions and backtest method
- **v0.3 additions:** team form & head-to-head data, league-wide download and daily calendar, recovering 15' scores from the first-goal minute, and the league-average timeline model (Model L) → formula v1.2
- **v0.4 additions:** competition adjustment for teams playing in several competitions (Model B v2) → formula v1.3

---

## Table of contents

1. [Summary of decisions](#1-summary-of-decisions)
2. [Free data sources](#2-free-data-sources)
3. [API quota strategy](#3-api-quota-strategy)
4. [Odds collection](#4-odds-collection)
5. [Scraping policy](#5-scraping-policy)
6. [Model C — market-implied 15-minute probability](#6-model-c--market-implied-15-minute-probability)
7. [Updated verification formula (v1.1)](#7-updated-verification-formula-v11)
8. [Schema additions](#8-schema-additions)
9. [Updated backtest method](#9-updated-backtest-method)
10. [Daily operating routine](#10-daily-operating-routine)
11. [Team form & head-to-head data](#11-team-form--head-to-head-data)
12. [League-wide download & daily match calendar](#12-league-wide-download--daily-match-calendar)
13. [Recovering 15-minute scores from the first-goal minute](#13-recovering-15-minute-scores-from-the-first-goal-minute)
14. [Model L — league-average timeline model](#14-model-l--league-average-timeline-model)
15. [Updated verification formula (v1.2)](#15-updated-verification-formula-v12)
16. [Competition adjustment — Model B v2](#16-competition-adjustment--model-b-v2)
17. [Updated verification formula (v1.3)](#17-updated-verification-formula-v13)
18. [Items to verify before building](#18-items-to-verify-before-building)
19. [Sources](#19-sources)

---

## 1. Summary of decisions

| # | Decision |
|---|---|
| D12 | Primary data API: **API-Football free plan** |
| D13 | Backup / cross-check: **football-data.org free plan** |
| D14 | Historical seed data: **openfootball** + **football-data.co.uk** |
| D15 | Build a **league-wide local database**; never fetch history team by team |
| D16 | No scraping as part of the core pipeline |
| D17 | 15-minute market odds are **entered manually** (no free source exists) |
| D18 | Add **Model C**: market-implied probability derived from HT / FT totals odds |
| D19 | Formula upgraded to **v1.1** (three-model blend) |
| D20 | The Odds API is **optional**, used only for occasional main-market checks |
| D21 | Collect **team form (win / conceded) and head-to-head** data for both teams (§11) |
| D22 | **Download league-wise data**, parse it and arrange matches into a **daily calendar** (§12) |
| D23 | Fill the 15' historical data gap using the **first-goal minute + half-time score** (§13) |
| D24 | Add **Model L**: league average HT goals × 0–15 share, applied per match (§14) |
| D25 | Formula upgraded to **v1.2** (§15) |
| D26 | Each historical match is compared against **its own competition's average** (team index); cup matches are included, friendlies excluded (§16) |
| D27 | Formula upgraded to **v1.3** — Model B replaced by Model B v2 (§17) |

This settles **Open question #1 (data provider)** and partially **#4 (odds source)** from the main document.

---

## 2. Free data sources

### 2.1 Overview

| Need | Source | Type | Cost / limits | Provides |
|---|---|---|---|---|
| Fixtures, goal minutes, HT/FT scores, lineups | **API-Football** | Official REST API | Free: 100 requests/day, resets 00:00 UTC | Worldwide leagues; events include goal minute and stoppage minute |
| Backup & cross-check | **football-data.org** | Official REST API | Free: 10 requests/min, 12 competitions, delayed scores | Match `goals[]` with `minute` and `injuryTime` |
| Historical goal minutes | **openfootball** | Open data (GitHub JSON) | Free, public domain, no key | Results and goal minutes for main leagues |
| Historical results + odds | **football-data.co.uk** | Free CSV files | Free download | FT/HT scores; 1X2, O/U 2.5, AH odds incl. **Pinnacle closing odds** |
| Parser testing | **StatsBomb Open Data** | Open data (GitHub JSON) | Free, attribution required | Full event data, limited competitions |
| Main-market live odds (optional) | **The Odds API** | Official REST API | Free: 500 credits/month | 1X2 and totals from many bookmakers; **no 15-minute markets** |

### 2.2 Role of each source in the system

```
                 ┌──────────────────────┐
 openfootball ──►│                      │
 football-data   │   Local PostgreSQL   │◄── API-Football (daily updates,
 .co.uk (CSV) ──►│   database           │     fixtures, lineups, new leagues)
                 │                      │◄── football-data.org (cross-check)
                 └──────────┬───────────┘
                            │
                            ▼
              Statistics, league baselines, formula
                            ▲
                            │
       Manual odds entry (15-min markets, HT/FT totals)
       The Odds API (optional main-market checks)
```

### 2.3 Data available by need

| Data | Free? | Source |
|---|---|---|
| Fixtures | ✅ | API-Football |
| Goal minutes → 15' / HT / FT scores | ✅ | API-Football, openfootball, football-data.org |
| League baselines | ✅ | Calculated from the local database + football-data.co.uk |
| Lineups (~1h before kick-off) | ✅ | API-Football |
| Historical main-market odds (benchmark) | ✅ | football-data.co.uk |
| Upcoming main-market odds | ✅ (limited) | The Odds API / manual |
| HT totals odds (O/U 0.5, 1.0, 1.5) | ⚠️ manual | Bookmaker site |
| **15-minute market odds** | ❌ | **Manual entry** |

---

## 3. API quota strategy

### 3.1 The problem

One fixture needs up to 30 historical matches per team → ~60 matches per fixture. Fetching each match's events separately would exhaust 100 requests/day after one or two fixtures.

### 3.2 The solution: league-wide database

1. **Season fixture list** — 1 request per league per season.
2. **Bulk fixture details** — API-Football accepts up to **20 fixture IDs per request** (`ids` parameter), returning events. A 380-match season ≈ **19 requests**.
3. **Daily incremental update** — fetch only yesterday's finished matches per league (a few requests).
4. **All team histories** (general / home / away) are then queried from the **local database** — zero API calls.

### 3.3 Example daily budget (API-Football, 100 requests)

| Task | Requests |
|---|---|
| Today's fixtures (all covered leagues) | 1–10 |
| Yesterday's finished matches (bulk, 20 per call) | 5–15 |
| Lineups for candidate matches | 5–10 |
| Back-filling one new league season (spread over days) | 20–40 |
| Safety reserve | 20+ |
| **Total** | **≤ 100** |

**Seeding history:** load past seasons from openfootball and football-data.co.uk first; use the API quota only for leagues/seasons they do not cover.

### 3.4 The Odds API credits

The free plan gives **500 credits per month**. Credits per request are roughly:

```
credits = number of markets × number of regions
```

| Request | Credits |
|---|---|
| 1 league, `h2h`, 1 region | 1 |
| 1 league, `h2h` + `totals`, 1 region | 2 |
| 1 league, `h2h` + `totals`, 2 regions | 4 |

- One request returns **all upcoming matches** in that league.
- Listing sports is usually free; **historical odds cost much more**.
- Example: 10 leagues × totals only × 1 region × 30 days = **300 credits** (fits). With h2h too = 600 (does not fit).

**Use in BETTORY:** optional, only for the Over/Under check on matches that already pass the filters.

---

## 4. Odds collection

### 4.1 The gap

No free API was found offering **0–15 min Result** or **0–15 min Total** odds. Free odds APIs cover main markets only (1X2, totals, handicaps, sometimes first half).

### 4.2 Manual entry process

Only matches that pass the statistical filters need odds — typically **5–10 per day**.

| When | Enter |
|---|---|
| Analysis time | HT O/U 0.5, HT O/U 1.5 (optional HT Asian 1.0), FT O/U 2.5 |
| Bet time | 15' Under 0.5 and 15' Draw odds at your bookmaker |
| Just before kick-off | 15' odds again (**closing odds**, for CLV) |

### 4.3 Consequences

- **Live profit and CLV** are measured from manual records.
- **Historical backtest** cannot use real 15-minute odds; it uses *estimated* 15-minute fair odds from Model C (see §9).

---

## 5. Scraping policy

Sites such as Flashscore, Sofascore, FotMob, FBref and bookmaker pages contain the data (including 15-minute odds), and scraping is technically possible. **It is not used in the core pipeline** because:

- Most of these sites' **terms of use prohibit scraping**.
- They **block IPs** and change their page structure often, making scrapers break.
- Silent pipeline failures would violate the data-completeness rule (never assume 0-0).

Free official APIs and open data cover everything except 15-minute odds, which are handled by manual entry.

---

## 6. Model C — market-implied 15-minute probability

### 6.1 Idea

Professionals price small markets by **deriving them from larger, more accurate markets**. First-half totals odds tell us how many goals the market expects before half-time; from that we estimate the chance of no goal before minute 15.

### 6.2 Method (from HT Over/Under 0.5)

**Step 1 — Remove the bookmaker margin**
```
p_over  = 1 / odds_over
p_under = 1 / odds_under
P(no goal in 1st half) = p_under / (p_over + p_under)
```

**Step 2 — Expected first-half goals**
```
λ_HT = −ln( P(no goal in 1st half) )
```

**Step 3 — 0–15 share of first-half goals**
```
r   = (league goals in 0–15) / (league goals in 1st half)      ≈ 0.125 / 0.44 ≈ 0.28
λ15 = r × λ_HT
```
`r` is calculated **per league** from the local goal-event database (stored in `league_baselines.first15_share_of_ht`).

**Step 4 — Probability and fair odds**
```
P_C(Under 0.5 at 15') = e^(−λ15)
Fair odds             = 1 / P_C
```

### 6.3 Worked example

HT Over 0.5 @ **1.40**, HT Under 0.5 @ **2.80**

| Step | Result |
|---|---|
| Implied: 0.714 + 0.357 = 1.071 (7.1% margin) | |
| No-vig P(0-0 at HT) = 0.357 / 1.071 | **33.3%** |
| λ_HT = −ln(0.333) | **1.10** |
| λ15 = 0.28 × 1.10 | **0.31** |
| P_C(0-0 at 15') = e^−0.31 | **73.5%** |
| **Fair 15' Under 0.5 odds** | **1.36** |
| Typical bookmaker price (6–8% margin) | ~1.26–1.28 |

If the bookmaker offers **1.38**, it is above the market-implied fair price → value signal.

### 6.4 Other lines

| Line | How it is used |
|---|---|
| **HT O/U 1.5** | Solve λ_HT from `P(≤1 goal) = e^(−λ)(1 + λ)` (numerical). If it agrees with the 0.5 line, average both; a large disagreement lowers confidence. |
| **HT Asian 1.0** | Under wins on 0 goals, push on 1 goal. Solve λ_HT numerically from the no-vig price. Optional, later phase. |
| **FT O/U 2.5** | Solve λ_FT from `P(≤2 goals) = e^(−λ)(1 + λ + λ²/2)`, then `λ15 = early_share × λ_FT`. Less direct, but widely available and **available historically** (football-data.co.uk). |

**Line priority:** HT O/U 0.5 → HT O/U 1.5 → FT O/U 2.5.

**Consistency check:** if λ_HT values from different lines differ by more than **20%**, flag `MARKET_INCONSISTENT` and reduce Model C's weight to 0.

### 6.5 Limitations

- Assumes goals follow a Poisson process (close, not exact).
- `r` varies by league and slightly by team.
- 15-minute markets usually carry a **larger margin** than HT markets, so real prices are typically below fair odds.
- Model C reflects **market opinion**. BETTORY's edge comes from cases where Models A and B disagree with the market **and are proven right** by live tracking.
- The 15-minute boundary must match the bookmaker's settlement rule.

---

## 7. Updated verification formula (v1.1)

### 7.1 Three-model blend

```
P(UNDER 0.5) = w_A × P_A  +  w_B × P_B  +  w_C × P_C

Starting weights:  w_A = 0.35,  w_B = 0.30,  w_C = 0.35
If Model C unavailable or MARKET_INCONSISTENT:  w_A = 0.5, w_B = 0.5, w_C = 0  (= v1.0)
```

`P(DRAW) = P(UNDER 0.5) + P(1-1)` as in v1.0.

### 7.2 Updated BET conditions

| # | Condition | Change from v1.0 |
|---|---|---|
| 1 | EV ≥ +5% (using bookmaker's 15' odds and blended P) | unchanged |
| 2 | \|P_A − P_B\| ≤ 10 pts | unchanged |
| 3 | \|P_blend − P_C\| ≤ 10 pts | **replaces** comparison with margin-included 15' odds |
| 4 | Bookmaker 15' odds ≥ Model C fair odds | **new** — price must beat the market-implied fair price |
| 5 | Only the higher-EV market selected | unchanged |

### 7.3 Updated confidence grade

| Grade | Rule |
|---|---|
| **A** | All three models within 5 pts, full samples |
| **B** | All available models within 10 pts |
| **SKIP** | Otherwise |

Layers 1 (data gate) and 5 (red flags) are unchanged.

---

## 8. Schema additions

### 8.1 `odds_snapshots` — new market values

| market | selections |
|---|---|
| `FIRST15_TOTAL` | `UNDER_0.5`, `OVER_0.5` |
| `FIRST15_RESULT` | `HOME`, `DRAW`, `AWAY` |
| `HT_TOTAL_0.5` | `OVER`, `UNDER` |
| `HT_TOTAL_1.5` | `OVER`, `UNDER` |
| `HT_ASIAN_TOTAL_1.0` | `OVER`, `UNDER` |
| `FT_TOTAL_2.5` | `OVER`, `UNDER` |

New field: `entry_method` — `MANUAL` / `API` / `CSV_IMPORT`.

### 8.2 `league_baselines` — new fields

| Field | Description |
|---|---|
| `ht_share` | Share of goals scored in the first half |
| `first15_share_of_ht` | `r` — share of first-half goals scored in 0–15 |

### 8.3 `verification_results` — new fields

| Field | Description |
|---|---|
| `p_model_c` | Market-implied probability |
| `lambda_ht_market` | λ_HT derived from odds |
| `model_c_source_line` | `HT_0.5` / `HT_1.5` / `FT_2.5` |
| `market_consistency` | `OK` / `MARKET_INCONSISTENT` / `UNAVAILABLE` |
| `fair_odds_model_c` | 1 / P_C |
| `formula_version` | `v1.1` |

### 8.4 New table: `sources` (seed values)

| name | type |
|---|---|
| API-Football | API |
| football-data.org | API |
| openfootball | OPEN_DATA |
| football-data.co.uk | CSV |
| StatsBomb Open Data | OPEN_DATA |
| The Odds API | API |
| Manual entry | MANUAL |

---

## 9. Updated backtest method

| Phase | What it proves | Data |
|---|---|---|
| **1. Calibration backtest** | Are Model A/B probabilities accurate? (Brier score, predicted vs actual buckets) | Historical goal minutes |
| **2. Estimated-value backtest** | Approximate profit vs market | Historical **Pinnacle FT O/U 2.5 closing odds** (football-data.co.uk) → Model C estimate → assumed bookmaker 15' price = fair odds × (1 − 7% margin) |
| **3. Live tracking** | Real profit and CLV | Manual 15' odds at bet time and closing |

Rules:
- Point-in-time only: every backtested decision uses data available before that fixture.
- Phase 2 profit is an **estimate**, never treated as proof.
- Real-money staking starts only after Phases 1–2 look positive on **300+ bets**; Kelly staking only after Phase 3 confirms.

---

## 10. Daily operating routine

| Time | Step |
|---|---|
| Morning | System updates yesterday's results and fetches today's fixtures (API-Football) |
| Morning | Models A and B run → candidate list (pass data gate + red flags) |
| Before bet | You enter HT/FT totals odds and 15' odds for candidates → Model C, EV, decision |
| ~60 min before kick-off | Lineups fetched; red flags re-checked |
| Just before kick-off | Enter closing 15' odds |
| After match | System settles result; profit and CLV recorded |
| Weekly | Review performance by league, grade, market, model |

---

## 11. Team form & head-to-head data

### 11.1 What is collected (per fixture)

| Team | Data |
|---|---|
| **HOME team** | Win / draw / loss record (home view + general view), goals conceded (FT, HT, 15'), head-to-head vs opponent |
| **AWAY team** | Win / draw / loss record (away view + general view), goals conceded (FT, HT, 15'), head-to-head vs opponent |

**Head-to-head (H2H):** last **up to 10 meetings** between the two teams, within the last **5 years**, any venue — plus the same-venue subset (this home team at home vs this away team).

H2H statistics: meetings count, home wins / draws / away wins, 15' 0-0 count and rate, early-goal count, average HT and FT goals, last meeting date.

**No extra API calls:** all of this is calculated from the local league-wide database (§12). H2H meetings in cups are only included if those competitions are stored.

### 11.2 How it is used

| Data | Use in the system |
|---|---|
| Win rate | **Favourite detection** when match odds are missing: home team home win rate ≥ 70% **and** away team away win rate ≤ 20% → treated like a "favourite < 1.50" red flag |
| Goals conceded | Already feeds Model A defence ratings; now also shown on the match card |
| H2H | **Context and confidence only** (no direct weight in v1.2) |

**Why H2H gets no formula weight:** samples are tiny (often 2–6 meetings), squads and coaches change, and research shows small samples regress to the mean. It is used as:

- **Red flag:** H2H has ≥ 4 meetings **and** a goal before 15' in ≥ 50% of them → SKIP.
- **Bonus note:** ≥ 4 meetings with 15' 0-0 in ≥ 80% → shown as supporting evidence (does not change probability).
- After backtesting, H2H may earn a small weight if it proves predictive.

### 11.3 Schema additions

**`team_statistics`** — new fields: `win_count`, `draw_count`, `loss_count`, `win_rate`, `avg_ht_goals_scored`, `avg_ht_goals_conceded`.

**New table `h2h_statistics`**

| Field | Description |
|---|---|
| fixture_id | Upcoming fixture |
| scope | `ALL_VENUES` / `SAME_VENUE` |
| meetings_count | |
| home_wins, draws, away_wins | From the fixture's home team perspective |
| first15_zero_zero_count, first15_goal_count | |
| avg_ht_goals, avg_ft_goals | |
| last_meeting_date | |
| calculated_at | |

---

## 12. League-wide download & daily match calendar

### 12.1 Pipeline

```
1. Download   → per league, per season (CSV / open data / API bulk calls)
2. Parse      → convert every source into one standard match format
3. Normalise  → map team & league names to internal IDs; derive 15'/HT/FT
4. Store      → historical_matches + match_goals (stored once, deduplicated)
5. Arrange    → build the daily match calendar
6. Calculate  → league baselines, team statistics, H2H for each day's fixtures
```

### 12.2 "All leagues" — phased coverage

Downloading every league at once is not possible on free quotas. Coverage is built in tiers:

| Tier | Leagues | Source | Speed |
|---|---|---|---|
| **1** | Leagues in openfootball and football-data.co.uk (~20–25 European leagues) | Free files | Immediate |
| **2** | Other leagues where **your bookmaker offers 15-minute markets** | API-Football bulk back-fill | ~1 league per day (2 seasons ≈ 40 requests) |
| **3** | Remaining leagues | API-Football | Only if needed |

Rules:
- Each league needs the **current and previous season** (early in a season, a team has fewer than 10 home matches).
- Priority: leagues with **low early-goal rates** (from Model L screening, §14) and leagues your bookmaker covers.
- Only leagues that offer the 15-minute markets matter — no market, no bet.

### 12.3 Daily match calendar

New view/table **`daily_match_calendar`**:

| Field | Description |
|---|---|
| match_date, kickoff_utc | |
| league, home_team, away_team | |
| status | SCHEDULED / LIVE / FINISHED / POSTPONED |
| data_ready | History complete for both teams (Y/N) |
| league_eligible | League active, has 15' market, enough matches played |
| candidate | Passed data gate + Model L screen + red flags |
| decision | BET / SKIP / PENDING_ODDS |

**New fields on `leagues`:** `is_active`, `has_15min_market`, `coverage_tier`.

---

## 13. Recovering 15-minute scores from the first-goal minute

### 13.1 Idea

Many sources provide the **half-time score** and the **minute of the first goal** even when full goal timelines are missing. Combined, they recover the 15' result for almost every match.

### 13.2 Decision table

`T` = 15-minute cutoff from the bookmaker setting (provider label "15'" = 14:00–14:59, so a goal labelled 15' counts as inside 00:00–14:59).

| First goal minute | HT score | 15' score | UNDER 0.5 | DRAW |
|---|---|---|---|---|
| None (no goals) | 0-0 | **0-0** | ✅ WIN — known | ✅ WIN — known |
| > T | any | **0-0** | ✅ WIN — known | ✅ WIN — known |
| ≤ T | 1 goal total (1-0 / 0-1) | **1-0 / 0-1** | ❌ LOSE — known | ❌ LOSE — known |
| ≤ T | Only one team scored (2-0, 3-0, 0-2…) | That team ≥ 1, other 0 | ❌ LOSE — known | ❌ LOSE — known |
| ≤ T | Both teams scored (1-1, 2-1…) | ≥ 1 goal, 1-1 possible | ❌ LOSE — known | ⚠️ **UNKNOWN** unless the second goal minute is available |

**Result:** the UNDER 0.5 outcome (and the 0-0 rate) is **always** recoverable. DRAW is unknown only in the rare case "early goal + both teams scored before HT".

### 13.3 Data status values

| Status | Meaning |
|---|---|
| `COMPLETE` | Full goal timeline available |
| `DERIVED` | 15' score recovered from first-goal minute + HT score |
| `PARTIAL_DRAW_UNKNOWN` | UNDER 0.5 known; DRAW unknown |
| `INCOMPLETE` | No first-goal minute — excluded (never assumed 0-0) |

### 13.4 Effect on statistics

- **0-0 rate / UNDER 0.5 rate:** uses COMPLETE + DERIVED + PARTIAL_DRAW_UNKNOWN.
- **DRAW rate:** excludes PARTIAL_DRAW_UNKNOWN from the denominator.
- **Early goals scored/conceded:** in DERIVED matches with a single first-half goal, the scoring team is known from the HT score. In ambiguous cases, count **1 goal** (lower bound) and flag it.
- Stoppage time does not affect the 0–15 period.

**Required field:** `historical_matches.first_goal_team_id` (when the source provides it).

---

## 14. Model L — league-average timeline model

### 14.1 Idea

Calculate the league's **average goals scored up to HT**, then use the share of those goals that fall in the 0–15 timeline to estimate the probability for the league — and, adjusted by team strength, for **each match**.

**Big advantage:** HT scores are available **free for decades** (football-data.co.uk), even where goal minutes are missing. HT goals give a sample ~3.5× larger than 15' goals.

### 14.2 League level (screening)

```
league_avg_ht_goals = total first-half goals / matches
r                   = goals in 0–15 / first-half goals      (from goal events, §6.2)

λ15_league = league_avg_ht_goals × r
P_L_league = e^(−λ15_league)
Fair odds  = 1 / P_L_league
```

### 14.3 Match level (per fixture)

Same structure as Model A, but using **HT goals** instead of FT goals (60% venue / 40% general blend):

```
HTAttack, HTDefence ratings = team HT goals scored/conceded ÷ league HT averages

λ_HT_home = league avg HT home goals × HTAttack_home × HTDefence_away
λ_HT_away = league avg HT away goals × HTAttack_away × HTDefence_home

λ15   = r × (λ_HT_home + λ_HT_away)
P_L   = e^(−λ15)
```

### 14.4 Worked example

League: avg HT goals 1.15 (home 0.64, away 0.51), r = 0.28.

| Step | Result |
|---|---|
| League λ15 = 1.15 × 0.28 | 0.322 |
| **League P(0-0 at 15')** | **72.5%** (fair 1.38) |
| λ_HT_home = 0.64 × 0.90 × 0.95 | 0.547 |
| λ_HT_away = 0.51 × 0.80 × 0.85 | 0.347 |
| Match λ15 = 0.28 × 0.894 | 0.250 |
| **Match P_L** | **77.9%** (fair 1.28) |
| Bookmaker 15' Under 0.5 @ 1.36 → EV | 1.36 × 0.779 − 1 = **+5.9%** |

### 14.5 How Model L is used

| Use | Rule |
|---|---|
| **Layer 0 — league screen** | League P_L ≥ **70%** (configurable) → league eligible for daily candidates |
| **League ranking** | Rank leagues by P_L to prioritise downloads (§12.2) |
| **Part of Model A** | Model A becomes the average of the FT-based estimate (A_FT) and the HT-based estimate (A_HT = Model L match level) |
| **Fallback** | Where goal minutes are missing for a league, Model L still works from HT scores (r taken from similar leagues until real data exists) |

**New fields on `league_baselines`:** `avg_ht_goals`, `avg_ht_home_goals`, `avg_ht_away_goals`, `p_l_league`.
**New fields on `verification_results`:** `p_model_l`, `lambda15_model_l`.

---

## 15. Updated verification formula (v1.2)

```
Layer 0  League screen        → league P_L ≥ 70% and league has 15' markets
Layer 1  Data quality gate    → ≥ 8/10 matches COMPLETE or DERIVED per view
Layer 2  Model A              → P_A = 0.5 × A_FT + 0.5 × A_HT (Model L match level)
Layer 3  Model B              → shrunk 15' 0-0 rate (uses DERIVED data too)
Layer 4  Model C              → market-implied from HT/FT totals odds
         Blend                → P = 0.35 × P_A + 0.30 × P_B + 0.35 × P_C
                                (no Model C → 0.5 / 0.5 / 0)
         Value                → EV ≥ +5%, odds ≥ Model C fair odds, model gaps ≤ 10 pts
Layer 5  Red flags            → favourite (odds or win-rate proxy), must-score,
                                lineups, H2H early-goal flag (§11.2)
```

All weights and thresholds remain **starting values** to be calibrated by backtest.

> Superseded by v1.3 (§17).

---

## 16. Competition adjustment — Model B v2

### 16.1 The problem

One team can play in several competitions — domestic league, domestic cup, continental competition. Its last 10 matches may mix them, for example:

- 7 domestic league matches (low early-goal level)
- 2 cup matches against weaker lower-division teams (higher early-goal level)
- 1 continental match (different level again)

Simply counting "goals before 15'" across all 10 lets the cup matches **distort** the team's numbers.

### 16.2 The rule

**Every historical match is compared against the average of the competition it was played in.** An early goal in a high-scoring competition counts for less than one in a low-scoring competition. This also settles open question #3 of the main document: **cup matches are included, but normalised** to their own competition. Friendlies remain excluded.

### 16.3 Team index

```
expected_i = λ15 of match i's competition (and season)     ← competition_baselines
actual_i   = goals in the first 15' of match i (both teams)

Team index = (Σ actual + k) / (Σ expected + k)              k = 3  (shrinkage toward 1.0)
```

- Index **1.0** = the team's matches have a normal number of early goals for their competitions.
- Index **< 1.0** = fewer early goals than normal (good for UNDER 0.5).
- `k` pulls small samples toward 1.0 — the same shrinkage principle as before, in one formula.

The index is calculated separately for each view (GENERAL, HOME, AWAY), using matches with status `COMPLETE` or `DERIVED` (§13.3). Early goals in ambiguous matches count as 1 (lower bound, flagged).

### 16.4 Applying it to the fixture

```
λ15_fixture_comp = λ15 of the upcoming fixture's competition

Index_home = 0.6 × home team HOME index + 0.4 × home team GENERAL index
Index_away = 0.6 × away team AWAY index + 0.4 × away team GENERAL index

λ15_B = λ15_fixture_comp × (Index_home + Index_away) / 2
P_B   = e^(−λ15_B)
```

Each team's index already reflects both its attacking and defending, so the two indexes are averaged.

### 16.5 Worked example

| Matches | Competition λ15 | Expected early goals |
|---|---|---|
| 7 league | 0.32 | 2.24 |
| 3 cup | 0.40 | 1.20 |
| **Total** | | **3.44** |

Actual early goals across the 10 matches: **2**

```
Index = (2 + 3) / (3.44 + 3) = 0.78   → 78% of normal early goals
Fixture in the league: λ15 contribution = 0.78 × 0.32 = 0.25
```

### 16.6 Which baseline the fixture uses

| Fixture type | Baseline used |
|---|---|
| Domestic league match | That league's baseline |
| Domestic cup / continental match | The competition's own baseline, **if** it has ≥ 50 matches (pooled seasons) |
| Same, but < 50 matches | **Average of the two teams' domestic league baselines** |

### 16.7 Handling rules

| Issue | Rule |
|---|---|
| Small cup samples | Pool up to **3 seasons** for the competition baseline; below **50 matches**, fall back to the team's domestic league baseline for that match's `expected_i` |
| Mismatched cup ties (top team vs lower division) | Partly absorbed by the competition average; opponent-strength adjustment is a later improvement |
| Squad rotation in cups | `competition_type` stored; backtest decides whether cup matches need a lower weight |
| Team changed league (promotion/relegation) | Each match uses the baseline of the competition **and season** it was played in |
| Friendlies | Excluded |

### 16.8 Schema changes

| Change | Detail |
|---|---|
| `league_baselines` → **`competition_baselines`** | Key: `competition_id` + `season`; fields as before plus `lambda15`, `matches_count`, `pooled_seasons` |
| `leagues` → **`competitions`** | Add `competition_type` (LEAGUE / CUP / CONTINENTAL), `country`, `level` |
| `historical_matches` | `competition_id` replaces `league_id` |
| `team_statistics` | New: `early_goals_expected`, `early_goals_actual`, `team_index` |
| `verification_results` | New: `index_home`, `index_away`, `baseline_competition_id`, `baseline_method` (OWN / DOMESTIC_FALLBACK / AVERAGE_OF_DOMESTIC) |

---

## 17. Updated verification formula (v1.3)

```
Layer 0  League screen        → competition P_L ≥ 70% and 15' markets available
Layer 1  Data quality gate    → ≥ 8/10 matches COMPLETE or DERIVED per view
Layer 2  Model A              → P_A = 0.5 × A_FT + 0.5 × A_HT (Model L match level)
Layer 3  Model B v2           → competition-adjusted team index (§16)
Layer 4  Model C              → market-implied from HT/FT totals odds
         Blend                → P = 0.35 × P_A + 0.30 × P_B + 0.35 × P_C
                                (no Model C → 0.5 / 0.5 / 0)
         Value                → EV ≥ +5%, odds ≥ Model C fair odds, model gaps ≤ 10 pts
Layer 5  Red flags            → favourite (odds or win-rate proxy), must-score,
                                lineups, H2H early-goal flag (§11.2)
```

**Calibration parameters added:** index shrinkage `k = 3`; minimum competition sample `50 matches`; maximum pooled seasons `3`. All remain starting values for the backtest.

---

## 18. Items to verify before building

| # | Check | How |
|---|---|---|
| V1 | Which seasons the API-Football free plan unlocks | Register free key, test historical season request |
| V2 | `ids` bulk request (20 fixtures) works on free plan and includes events | Test call |
| V3 | football-data.org free tier returns `goals[]` for free competitions | Test call with free token |
| V4 | openfootball field names in current `football.json` files | Inspect sample file |
| V5 | football-data.co.uk column names for Pinnacle closing O/U 2.5 | Read `notes.txt` data key |
| V6 | Bookmaker's 15-minute boundary and stoppage-time rule | Bookmaker rules page |
| V7 | The Odds API current credit rules | the-odds-api.com |
| V8 | Which leagues your bookmaker offers 15-minute markets for | Bookmaker site — sets `leagues.has_15min_market` |
| V9 | Which sources give first-goal minute **and** scoring team | Test API-Football events, football-data.org, openfootball samples |
| V10 | How each source labels goal minutes (is "15'" = 14:00–14:59?) | Compare a few matches against video/live text |
| V11 | Cup / continental competitions available from free sources (for competition baselines) | Check API-Football competition list and free-plan access |

---

## 19. Sources

**Data APIs and datasets**
- [API-Football pricing](https://www.api-football.com/pricing)
- [API-Football on RapidAPI](https://rapidapi.com/api-sports/api/api-football/pricing)
- [TheStatsAPI vs API-Football](https://www.thestatsapi.com/blog/thestatsapi-vs-api-football)
- [football-data.org — Match resource (v4)](https://docs.football-data.org/general/v4/match.html)
- [football-data.org free tier limits](https://www.thestatsapi.com/blog/football-data-org-free-tier-limits-2026)
- [openfootball football.json](https://github.com/openfootball/football.json)
- [openfootball euro.json](https://github.com/openfootball/euro.json)
- [football-data.co.uk overview](https://sports-models-kb.pages.dev/football-data-co-uk)
- [soccerdata — MatchHistory (football-data.co.uk reader)](https://soccerdata.readthedocs.io/en/latest/datasources/MatchHistory.html)
- [StatsBomb Open Data](https://github.com/statsbomb/open-data)

**Odds APIs**
- [The Odds API](https://the-odds-api.com/)
- [OddsPapi — football](https://oddspapi.io/sports/football)
- [SharpAPI — soccer odds](https://sharpapi.io/odds/soccer)

**Market-derived pricing and benchmarks**
- [EV betting with Pinnacle benchmark](https://oddspapi.io/blog/?p=2906)
- [Closing line value](https://thestatsapi.com/odds-api/clv)
- [SoccerStats — goals by 15-minute segment](https://www.soccerstats.com:443/stats.asp?page=10)
