# BETTORY — Football Match Verification Engine

**System documentation · v0.1 (design stage) · 2026-11-14**

---

## Table of contents

1. [Purpose and scope](#1-purpose-and-scope)
2. [The two betting markets](#2-the-two-betting-markets)
3. [System overview and flow](#3-system-overview-and-flow)
4. [Data requirements](#4-data-requirements)
5. [Data model (database schema)](#5-data-model-database-schema)
6. [Data rules](#6-data-rules)
7. [Verification formula](#7-verification-formula)
8. [Worked example](#8-worked-example)
9. [Staking and bankroll rules](#9-staking-and-bankroll-rules)
10. [Validation and performance tracking](#10-validation-and-performance-tracking)
11. [Calibration parameters](#11-calibration-parameters)
12. [Decisions log](#12-decisions-log)
13. [Open questions](#13-open-questions)
14. [Research sources](#14-research-sources)

---

## 1. Purpose and scope

BETTORY is a **Football Match Verification Engine**. For every upcoming fixture it:

1. Collects recent historical data for both teams.
2. Calculates first-15-minute statistics.
3. Estimates the probability that no goal is scored in the first 15 minutes.
4. Compares that probability to the bookmaker's odds.
5. Outputs a **BET / SKIP** decision with the supporting evidence.
6. Records the actual result so the system's performance can be proven over time.

**Out of scope (v1):** in-play betting, other markets, automatic bet placement.

> **Disclaimer:** The system estimates probabilities from historical data. It does not guarantee profit. Historical frequencies are not guaranteed future probabilities.

---

## 2. The two betting markets

| Market | Wins when the score at the end of the 0–15 min period is… |
|---|---|
| **Result (0–15 min): DRAW** | Level: 0-0, 1-1, 2-2… |
| **Total (0–15 min): UNDER 0.5** | 0-0 only |

### 2.1 Correlation warning (important)

- Every time UNDER 0.5 wins, DRAW also wins.
- Any single goal makes **both** lose.
- A 1-1 within 15 minutes happens in only ~1–2% of matches.

The two markets are therefore **almost the same bet**: *"no goal before 15 minutes."* Betting both doubles the stake on one outcome; it is not diversification.

**System rule:** for each qualifying fixture, recommend **only the market with the higher expected value (EV)**. Never combine both in an accumulator or bet builder.

### 2.2 Period boundary

Bookmakers define the first 15 minutes differently (examples: `00:00–14:59`, `1–15`, `00:00–15:00`). Data providers label a goal at 14:30 as minute "15'".

**System rule:** the boundary is a **configurable setting per bookmaker**, and the 15-minute score is always derived from goal event timestamps, not taken as a pre-computed value.

---

## 3. System overview and flow

```
 1. Fetch fixtures         → today's upcoming matches
 2. Fetch history          → per team: last 10 general, 10 home, 10 away (with goal events)
 3. Validate data          → completeness check, before-fixture rule
 4. Calculate statistics   → stored "as of" the fixture date
 5. Apply formula          → probability, grade, reasons
 6. Capture odds           → bookmaker + benchmark (Pinnacle) odds
 7. Value check            → EV; BET / SKIP
 8. Output                 → daily list with evidence
 9. Settle                 → record actual 15' result, profit/loss, closing odds
10. Performance dashboard  → win rate, ROI, CLV, calibration, by league/criterion
```

---

## 4. Data requirements

### 4.1 Historical selection (per team, per fixture)

| Category | Matches |
|---|---|
| GENERAL | Last 10 matches, any venue |
| HOME | Last 10 matches at home |
| AWAY | Last 10 matches away |

**Decision:** fetch all three groups for **both** teams (up to 30 records per team before deduplication). For a given fixture the formula *uses*:

- Home team → GENERAL + HOME
- Away team → GENERAL + AWAY

### 4.2 Data per historical match

- Match date (UTC kickoff timestamp)
- Home team, away team
- **Goal events** (minute, extra minute, scoring team, own goal flag) — from which are derived:
  - First-15-minute score
  - Half-time score
  - Full-time score
  - First goal minute
- Competition (to filter which competitions count)
- Data completeness status

### 4.3 League data

- Average home goals and away goals per match
- Early share: % of goals scored in the 0–15 period
- League rate of 0-0 at 15 minutes

### 4.4 Odds data

- Bookmaker odds at bet time for both markets
- Benchmark (Pinnacle) odds, when available
- Closing odds (for CLV)

---

## 5. Data model (database schema)

**Principle:** raw football data is stored separately from calculated statistics, so everything can be recalculated when formulas improve, without fetching data again.

### 5.1 Tables

| Table | Type | Purpose |
|---|---|---|
| `sources` | reference | Data providers |
| `leagues` | reference | Competitions, with provider ID mapping |
| `teams` | reference | Teams, with provider ID mapping |
| `fixtures` | raw | Upcoming matches |
| `historical_matches` | raw | Played matches and derived scores |
| `match_goals` | raw | Goal events (minute-level) |
| `team_match_history` | link | Connects historical matches to GENERAL / HOME / AWAY views |
| `league_baselines` | calculated | League averages per season |
| `team_statistics` | calculated | Team metrics, snapshot per fixture |
| `odds_snapshots` | raw | Odds captured over time |
| `verification_results` | output | Formula output and decision |
| `bet_settlements` | output | Actual results and profit/loss |
| `data_ingestion_logs` | operational | Every fetch operation |

### 5.2 Field definitions

**fixtures**

| Field | Type | Notes |
|---|---|---|
| id | UUID | |
| provider_fixture_id | VARCHAR | |
| league_id | UUID | → leagues |
| home_team_id | UUID | → teams |
| away_team_id | UUID | → teams |
| kickoff_utc | TIMESTAMPTZ | replaces date + time + timezone |
| status | VARCHAR | SCHEDULED / LIVE / FINISHED / POSTPONED / ABANDONED |

**historical_matches**

| Field | Type | Notes |
|---|---|---|
| id | UUID | |
| provider_fixture_id | VARCHAR | |
| league_id | UUID | |
| competition_type | VARCHAR | LEAGUE / CUP / FRIENDLY |
| kickoff_utc | TIMESTAMPTZ | |
| home_team_id, away_team_id | UUID | |
| home_goals_15, away_goals_15 | SMALLINT | derived from match_goals |
| home_goals_ht, away_goals_ht | SMALLINT | |
| home_goals_ft, away_goals_ft | SMALLINT | |
| first_goal_minute | SMALLINT NULL | NULL only when no goal and data complete |
| first_goal_extra_minute | SMALLINT NULL | stoppage time, e.g. 45+**2** |
| data_status | VARCHAR | COMPLETE / INCOMPLETE / UNVERIFIED |
| source_id | UUID | → sources |
| fetched_at | TIMESTAMPTZ | |

**match_goals**

| Field | Type | Notes |
|---|---|---|
| id | UUID | |
| historical_match_id | UUID | |
| team_id | UUID | team credited with the goal |
| minute | SMALLINT | |
| extra_minute | SMALLINT NULL | |
| is_own_goal | BOOLEAN | |

**team_match_history**

| Field | Type | Notes |
|---|---|---|
| id | UUID | |
| fixture_id | UUID | the upcoming fixture this history belongs to |
| team_id | UUID | |
| historical_match_id | UUID | |
| category | VARCHAR | GENERAL / HOME / AWAY |
| sequence_number | SMALLINT | 1 = most recent |

**league_baselines**

| Field | Type |
|---|---|
| league_id, season | UUID, VARCHAR |
| matches_count | INT |
| avg_home_goals, avg_away_goals | NUMERIC |
| early_share | NUMERIC (share of goals in 0–15) |
| first15_zero_zero_rate | NUMERIC |
| calculated_at | TIMESTAMPTZ |

**team_statistics** (one row per fixture × team × category — never overwritten)

| Field | Description |
|---|---|
| fixture_id, team_id, category | snapshot key |
| sample_size, complete_sample_size | matches used / with complete data |
| first15_zero_zero_count / rate | 0-0 at 15' |
| first15_draw_count / rate | level score at 15' (0-0, 1-1…) |
| first15_goal_count / rate | at least one goal before 15' |
| avg_first15_goals_scored / conceded | from the team's perspective |
| avg_ft_goals_scored / conceded | used by Model A |
| early_goals_total, ft_goals_total | used for team early share |
| calculated_at | |

**odds_snapshots**

| Field | Description |
|---|---|
| fixture_id, bookmaker, market, selection | e.g. `FIRST15_TOTAL`, `UNDER_0.5` |
| odds | decimal |
| snapshot_type | OPENING / BET_TIME / CLOSING |
| captured_at | |

**verification_results**

| Field | Description |
|---|---|
| fixture_id, formula_version | |
| p_model_a, p_model_b, p_under, p_draw | probabilities |
| lambda15_home, lambda15_away | expected early goals |
| ev_under, ev_draw | |
| market_implied_prob | |
| grade | A / B / SKIP |
| decision, chosen_market | BET / SKIP / INSUFFICIENT_DATA |
| reasons | JSON list of passed/failed checks |
| created_at | |

**bet_settlements**

| Field | Description |
|---|---|
| verification_result_id | |
| actual_home_15, actual_away_15 | |
| won | BOOLEAN |
| stake, odds_taken, profit | |
| closing_odds, clv | |

---

## 6. Data rules

1. **Historical ordering:** use only the latest 10 *completed* matches before the fixture's kickoff. Never include the fixture itself or anything played after it. (This also makes honest backtesting possible.)
2. **Data completeness:** a match without reliable goal-event data is flagged `INCOMPLETE`. It is **never** assumed to be 0-0.
3. **Sample size transparency:** every percentage is displayed with its count (e.g. `70% (7/10)`). 10 matches is a small sample with large uncertainty.
4. **Competition filter:** friendlies are excluded. (League vs cup inclusion — see Open questions.)
5. **Statistics snapshots:** statistics are stored per fixture and never overwritten, so every decision can be traced to the exact numbers used.

---

## 7. Verification formula

**Formula version: v1.0**

### Why this design

- Professional models (Poisson / Dixon-Coles) estimate goals from team **attack × defence** strength with home advantage.
- Only ~10–13% of goals happen in minutes 0–15, so a team's 10 matches contain just 3–4 early goals — too few to trust alone. The same 10 matches contain ~25 full-time goals, a much stronger signal.
- Small samples regress to the mean, so raw rates must be **shrunk toward the league average**.
- Value only exists when the probability beats the odds (**EV**).

The formula therefore combines two independent models and a value check.

### Layer 1 — Data quality gate

- Each view used must have **≥ 8 of 10 matches with complete data**.
- League baseline must exist.
- Otherwise → `INSUFFICIENT_DATA`.

### Layer 2 — Model A: strength model (Poisson)

Blend each rating 60% venue view / 40% general view:

```
HomeAttack  = 0.6 × (home team's goals scored at home   / league avg home goals)
            + 0.4 × (home team's goals scored (general)  / league avg goals per team)

AwayDefence = 0.6 × (away team's goals conceded away    / league avg home goals)
            + 0.4 × (away team's goals conceded (general)/ league avg goals per team)

AwayAttack  = 0.6 × (away team's goals scored away      / league avg away goals)
            + 0.4 × (away team's goals scored (general)  / league avg goals per team)

HomeDefence = 0.6 × (home team's goals conceded at home / league avg away goals)
            + 0.4 × (home team's goals conceded (general)/ league avg goals per team)

λ_home_FT = league avg home goals × HomeAttack × AwayDefence
λ_away_FT = league avg away goals × AwayAttack × HomeDefence
```

Early share (shrunk team tendency):

```
s_team = (team's early goals + 20 × s_league) / (team's FT goals + 20)
s      = (s_home_team + s_away_team) / 2
```

(*early goals / FT goals* count both goals scored and conceded in the team's matches.)

```
λ15_home = s × λ_home_FT
λ15_away = s × λ_away_FT
λ15      = λ15_home + λ15_away

P_A(0-0 at 15') = e^(−λ15)
```

### Layer 3 — Model B: shrunk empirical rate

For each team, blend the 15' 0-0 rate (home team: HOME view; away team: AWAY view) 60/40 with the GENERAL view, then shrink toward the league:

```
p_shrunk = (zero_zero_count + 10 × p_league) / (n + 10)
λ_team   = −ln(p_shrunk)

P_B(0-0) = e^( −(λ_home_team + λ_away_team) / 2 )
```

Averaging (not adding) is used because each team's rate already includes both its attacking and defending.

### Layer 4 — Final probability and value

```
P(UNDER 0.5) = 0.5 × P_A + 0.5 × P_B
P(1-1)       = e^(−λ15) × λ15_home × λ15_away
P(DRAW)      = P(UNDER 0.5) + P(1-1)

Fair odds    = 1 / P
EV           = bookmaker odds × P − 1
Market prob  = 1 / bookmaker odds   (or Pinnacle no-vig probability when available)
```

**BET conditions — all must pass:**

| # | Condition |
|---|---|
| 1 | EV ≥ **+5%** |
| 2 | \|P_A − P_B\| ≤ **10 percentage points** |
| 3 | \|P − market prob\| ≤ **12 percentage points** (a larger gap usually means the market knows something) |
| 4 | Only the market with the **higher EV** is selected |

**Confidence grade:**

| Grade | Rule |
|---|---|
| **A** | Models within 5 pts and full 10-match samples |
| **B** | Models within 10 pts |
| **SKIP** | Anything else |

### Layer 5 — Context red flags (any one → SKIP)

- Either team priced below **1.50** to win the match.
- A team that must score early (trailing on aggregate in a cup, must-win final day).
- Late lineup news adding attackers, or sharp odds movement toward an early goal.
- Either team had a goal before 15' in **3+ of its last 5** matches.

---

## 8. Worked example

**League baseline:** 1.45 home goals, 1.15 away goals, early share 12%, 15' 0-0 rate 73.2%.

**Team data:**

| | Venue scored | Venue conceded | General scored | General conceded | 15' 0-0 (venue-blended) |
|---|---|---|---|---|---|
| Home team (at home) | 1.2 | 0.9 | 1.1 | 1.0 | 8/10 |
| Away team (away) | 0.8 | 1.3 | 1.0 | 1.1 | 7/10 |

**Calculation:**

| Step | Result |
|---|---|
| HomeAttack 0.835 × AwayDefence 0.876 → λ_home_FT | 1.06 |
| AwayAttack 0.725 × HomeDefence 0.778 → λ_away_FT | 0.65 |
| λ15 = 0.12 × 1.71 (λ15_home 0.127, λ15_away 0.078) | 0.205 |
| **P_A** = e^−0.205 | **81.5%** |
| Home shrunk (8 + 7.32)/20 | 76.6% |
| Away shrunk (7 + 7.32)/20 | 71.6% |
| **P_B** | **74.0%** |
| **P(UNDER 0.5)** = average | **77.8%** (fair odds 1.29) |
| **P(DRAW)** = 77.8% + 0.8% | **78.6%** (fair odds 1.27) |
| UNDER 0.5 @ 1.38 → EV | **+7.3% ✅** |
| DRAW @ 1.33 → EV | +4.5% ❌ |
| Model gap 7.5 pts | Grade **B** |
| Market gap 77.8% vs 72.5% = 5.3 pts | ✅ |
| **Decision** | **BET UNDER 0.5 — 1% stake** |

---

## 9. Staking and bankroll rules

- **Flat stake: 1% of bankroll** per bet during testing and early live use.
- **Maximum 3–5 bets per day.**
- Never chase losses; never stake both markets on one match.
- Move to **¼ Kelly** only after live results confirm positive ROI and CLV:
  `f = (b·p − q) / b × 0.25`, where `b = odds − 1`, `p = model probability`, `q = 1 − p`.
- Bankroll = money set aside specifically for betting that you can afford to lose.

---

## 10. Validation and performance tracking

### 10.1 Backtest before real money

Run the formula on past fixtures using only data available before each fixture (Rule 1). Require **300+ backtested bets** before live use.

### 10.2 Metrics

| Metric | Question it answers | Target |
|---|---|---|
| **Calibration / Brier score** | When the model says 78%, does it win ~78%? | Predicted ≈ actual per probability bucket |
| **Win rate vs break-even** | Win rate > 1 / average odds? | Above break-even |
| **ROI** | Profit / total staked | > 0 |
| **CLV** | Did we beat the closing odds? | Positive on average |
| **Breakdown** | By league, grade, market, criterion | Find what works, drop what doesn't |

### 10.3 Expectations

At ~75% win rate, losing streaks of 3–4 will occur regularly. Judge the system on 100+ live bets, never on a few days.

---

## 11. Calibration parameters

Starting values — to be tuned by backtest and stored with the formula version.

| Parameter | Start value |
|---|---|
| Venue / general weight | 0.6 / 0.4 |
| Model A / Model B weight | 0.5 / 0.5 |
| Shrinkage strength — 0-0 rate | 10 matches |
| Shrinkage strength — early share | 20 goals |
| Minimum EV | +5% |
| Max model gap | 10 pts |
| Max market gap | 12 pts |
| Minimum complete matches per view | 8 of 10 |
| Favourite odds cutoff | 1.50 |

---

## 12. Decisions log

| # | Decision |
|---|---|
| D1 | Markets: 0–15 min DRAW and 0–15 min UNDER 0.5 |
| D2 | History: 10 general + 10 home + 10 away per team; calculation sample = 10 |
| D3 | Fetch all three groups for both teams |
| D4 | Raw data separated from calculated statistics |
| D5 | Database: PostgreSQL |
| D6 | Store goal events; derive 15', HT, FT scores and first goal minute |
| D7 | 15-minute boundary is configurable per bookmaker |
| D8 | Statistics stored as snapshots per fixture |
| D9 | Formula v1.0: Model A (Poisson strength) + Model B (shrunk rate) + EV check + red flags |
| D10 | Only one market (higher EV) per fixture |
| D11 | Flat 1% staking until validated |

---

## 13. Open questions

1. **Data provider** — must supply goal minutes (candidates: API-Football, Sportmonks, Football-Data). Depends on budget and league coverage.
2. **Bookmaker settlement rule** — confirm exact 15-minute boundary and stoppage-time handling.
3. **Competitions** — include cup matches in history, or league only?
4. **Odds source** — which bookmaker(s) and whether Pinnacle benchmark odds are available.
5. **Interface** — web dashboard, daily report, or both.
6. **League list** — which leagues to cover in v1.

---

## 14. Research sources

**Match modelling**
- [On the dependence in football match outcomes (Dixon-Coles and alternatives)](https://arxiv.org/pdf/2103.07272)
- [Langseth — Beating the bookie: statistical models](https://folk.idi.ntnu.no/helgel/papers/LangsethSCAI14.pdf)
- [Kucharski — Can we predict who will win a football match?](https://kucharski.substack.com/p/can-we-predict-who-will-win-a-football)
- [Volf et al. — time-dependent goal intensity](https://ro.utia.cas.cz/files/Soutez_09/Volf/VOLF_IMAMAN2009.pdf)

**Goal timing**
- [SoccerStats — goals by 15-minute segment](https://www.soccerstats.com:443/stats.asp?page=10)
- [Goal intensity rises over the match (arXiv 2312.04338)](https://arxiv.org/pdf/2312.04338)
- [Suspense and Surprise in European Football — league-specific minute rates](https://arxiv.org/pdf/2506.21253)

**Small samples and shrinkage**
- [Marcus & Smith — regression to the mean in football betting](https://zendy.io/title/10.1002/bdm.418)
- [Regression to the mean — lecture notes](https://saberpowers.github.io/teaching/smgt430/lecture/regression_to_the_mean.pdf)
- [Bayesian state-space models of team strength](https://projecteuclid.org/download/pdfview_1/euclid.aoas/1542078053)

**Value, CLV and staking**
- [EV betting with Pinnacle benchmark](https://oddspapi.io/blog/?p=2906)
- [Closing line value](https://thestatsapi.com/odds-api/clv)
- [Optimal sports betting strategies in practice (fractional Kelly)](https://arxiv.org/pdf/2107.08827.pdf)

**xG**
- [xG-based betting models study](https://jsr.org/index.php/path/article/download/1116/906)
- [Leicester 2015/16 through xG](https://arxiv.org/html/2602.15673v1)

**Market rules**
- [Stoiximan — minute range markets](https://stoiximan.zendesk.com/hc/en-cy/articles/360018748718)
- [MSport — goal interval markets](https://msportsupportgh.zendesk.com/hc/en-us/articles/4407923074327-Goals-Interval-Race-to-Nth-Number-of-Goals)
