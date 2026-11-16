# BETTORY — 0–15 Minute Odds Calculation

**Pricing engine specification · v1.0 · 2026-11-14**

Companion to [SYSTEM-DOCUMENTATION.md](SYSTEM-DOCUMENTATION.md) and [DATA-SOURCES-AND-MARKET-MODEL.md](DATA-SOURCES-AND-MARKET-MODEL.md).

This document defines **how BETTORY calculates its own odds for the 0–15 minute markets for every match**, and how those odds are compared with the bookmaker's prices.

---

## Table of contents

1. [What the pricing engine produces](#1-what-the-pricing-engine-produces)
2. [How professionals price time-interval markets (research)](#2-how-professionals-price-time-interval-markets-research)
3. [Pricing pipeline overview](#3-pricing-pipeline-overview)
4. [Step 1 — Full-match goal expectancies](#4-step-1--full-match-goal-expectancies)
5. [Step 2 — The time profile (share of goals in 0–15)](#5-step-2--the-time-profile-share-of-goals-in-015)
6. [Step 3 — 0–15 goal expectancies](#6-step-3--015-goal-expectancies)
7. [Step 4 — Probabilities for every 0–15 outcome](#7-step-4--probabilities-for-every-015-outcome)
8. [Step 5 — Fair odds](#8-step-5--fair-odds)
9. [Step 6 — The bookmaker's price: margin and de-vigging](#9-step-6--the-bookmakers-price-margin-and-de-vigging)
10. [Step 7 — Value decision](#10-step-7--value-decision)
11. [Deriving goal expectancies from market odds (Model C in detail)](#11-deriving-goal-expectancies-from-market-odds-model-c-in-detail)
12. [Settlement conventions](#12-settlement-conventions)
13. [Full worked example](#13-full-worked-example)
14. [Data model](#14-data-model)
15. [Validation](#15-validation)
16. [Limitations](#16-limitations)
17. [Decisions](#17-decisions)
18. [Sources](#18-sources)

---

## 1. What the pricing engine produces

For each fixture, the engine outputs:

| Output | Description |
|---|---|
| λ15_home, λ15_away | Expected goals for each team in the 0–15 period |
| P(0-0), P(Under 0.5), P(Over 0.5) | Total goals in 0–15 |
| P(Home leads), P(Draw), P(Away leads) | Result at the end of 0–15 |
| **Fair odds** for each outcome | 1 / P — BETTORY's own price, no margin |
| Bookmaker margin | Measured from the bookmaker's own 15' market |
| Bookmaker's de-vigged probability | What the bookmaker "really" thinks |
| **EV** for 15' UNDER 0.5 and 15' DRAW | Value of the offered price against BETTORY's price |
| **Minimum acceptable odds** | Lowest price worth betting |

---

## 2. How professionals price time-interval markets (research)

| Finding | Implication for BETTORY |
|---|---|
| Bookmakers and modellers price small markets by **first estimating each team's goal expectancy**, then building a **score probability grid**; every derivative market (correct score, Asian lines, intervals) is read off that grid. ([penaltyblog](https://penaltyblog.readthedocs.io/en/master/models/goal_expectancy.html), [OddsPapi Poisson guide](https://oddspapi.io/blog/?p=3120)) | Use one engine: goal expectancy → grid → all 0–15 outcomes. |
| Goal expectancies are **inferred from market odds** by removing the margin and solving for λ (root-finding / optimisation). Over/Under fixes the **total**; 1X2 is needed to **split** home vs away. ([penaltyblog](https://penaltyblog.readthedocs.io/en/master/models/goal_expectancy.html), [R-bloggers: expected goals from O/U odds](https://www.r-bloggers.com/2020/09/expected-goals-from-over-under-odds/), [Clegg et al.](https://arxiv.org/html/2605.16066)) | Under 0.5 needs only the total; DRAW (1-1) also needs the split. |
| Goal intensity is **not constant** through a match: a constant-rate Poisson model cannot reproduce market prices; a **time trend** is necessary. Scoring rate rises as the match progresses. ([Risk-neutral in-play pricing](https://arxiv.org/pdf/1811.03931), [Hoog thesis](https://lup.lub.lu.se/luur/download?func=downloadFile&recordOId=4253194&fileOId=4253196), [Dixon & Robinson via Volf](https://ro.utia.cas.cz/files/Soutez_09/Volf/VOLF_IMAMAN2009.pdf)) | Do **not** use 15/90 of the goals. Use the real share of goals in 0–15 (~12.5%, not 16.7%). |
| A Weibull model of goal times finds shape γ ≈ 1.13–1.17 (> 1 = rising hazard), calibrated to Betfair pre-match 1X2 and O/U 0.5–4.5 prices. ([Clegg, Song & Cartlidge](https://arxiv.org/html/2605.16066)) | Confirms the early period is quieter than average; used as a sanity check. |
| Leading/trailing changes intensities (Dixon & Robinson: home scoring falls and away rises when home leads). ([Volf et al.](https://ro.utia.cas.cz/files/Soutez_09/Volf/VOLF_IMAMAN2009.pdf)) | Small effect inside 15 minutes; optional adjustment for P(1-1). |
| Margin removal methods differ: **multiplicative** spreads margin evenly; **power** and **Shin** account for bookmakers loading more margin onto longshots (favourite–longshot bias). ([De-vigging methods](https://sharpery.vercel.app/education/devigging-methods), [penaltyblog implied](https://penaltyblog.readthedocs.io/en/master/implied/index.html), [Clarke et al.](https://researchbank.swinburne.edu.au/file/2069085d-5d5a-4f9c-9f1c-0c52472396cb/1/PDF%20(Published%20version).pdf)) | 15' Under 0.5 is the favourite side → use the **power method** by default. |
| Bookmakers apply **much larger margins on minor/derivative markets** than on main markets. ([Correct score margins](https://football-bets-tips.com/articles/football-correct-score-betting/)) | Expect 6–10% overround on 15' markets; measure it, don't assume it. |
| Bookmaker interval boundaries differ (00:00–14:59 / 1–15 / 00:00–15:00). ([Stoiximan](https://stoiximan.zendesk.com/hc/en-cy/articles/360018748718), [MSport](https://msportsupportgh.zendesk.com/hc/en-us/articles/4407923074327-Goals-Interval-Race-to-Nth-Number-of-Goals)) | The cutoff is a per-bookmaker setting. |

---

## 3. Pricing pipeline overview

```
 Step 1  Full-match goal expectancies  μ_home, μ_away
           (from team data — Models A / L / B v2 — and from market odds — Model C)
              │
 Step 2  Time profile  s = share of goals scored in 0–15 (per competition)
              │
 Step 3  0–15 expectancies   λ15_home = s × μ_home × early_index_home
                             λ15_away = s × μ_away × early_index_away
              │
 Step 4  Score grid for 0–15 → P(0-0), P(Draw), P(Home leads), P(Away leads)
              │
 Step 5  Fair odds = 1 / P
              │
 Step 6  Bookmaker odds → margin → de-vigged bookmaker probability
              │
 Step 7  EV, minimum odds, BET / SKIP
```

---

## 4. Step 1 — Full-match goal expectancies

Two independent sources produce μ_home and μ_away (expected full-time goals):

| Source | Method | Section |
|---|---|---|
| **Team model** | Model A: attack × defence ratings from FT goals (A_FT) and HT goals (A_HT / Model L) | Main doc §7; Data doc §14 |
| **Market model** | Model C: solved from 1X2 + Over/Under odds | §11 below |

**Blend at the goal-expectancy level** (so every 0–15 outcome is consistent):

```
μ_home = w_team × μ_home_team + w_mkt × μ_home_mkt
μ_away = w_team × μ_away_team + w_mkt × μ_away_mkt

Starting weights: w_team = 0.5, w_mkt = 0.5
No market odds entered → w_team = 1.0
```

Model B v2 (competition-adjusted early index) is applied in Step 3, because it measures **early-period tendency**, not full-match strength.

---

## 5. Step 2 — The time profile (share of goals in 0–15)

### 5.1 Why not 15/90

A uniform rate gives 15 / 90 ≈ 16.7% of goals in 0–15 (or 15 / 94 ≈ 16% with stoppage time). Real data shows **~12.5%**. Using the uniform rate would **overestimate early goals by ~30%** and make Under 0.5 look worse than it is.

### 5.2 Empirical share per competition (default)

```
s_comp_raw = goals with minute ≤ T  /  all goals          (from match_goals, own goals included)

Shrunk toward the global share:
s_comp = (goals_≤T + K × s_global) / (all_goals + K)       K = 300 goals
```

- `T` = bookmaker cutoff (§12).
- `s_global` = share across all stored competitions (expected ≈ 0.12–0.13).
- Pool up to 3 seasons per competition.

### 5.3 Two equivalent ways to use it

| From | Share | Formula |
|---|---|---|
| Full-time expectancy | `s` = share of **all** goals in 0–15 | λ15 = s × μ_FT |
| Half-time expectancy | `r` = share of **first-half** goals in 0–15 | λ15 = r × μ_HT |

Both are stored in `competition_baselines`; `s` is used when μ comes from FT data/odds, `r` when it comes from HT data/odds.

### 5.4 Weibull sanity check

With the published shape γ ≈ 1.15, the hazard in the opening minutes is clearly below the match average — consistent with s ≈ 0.125. If a competition's measured `s` is above **0.16** or below **0.09**, flag it for review (data error or truly unusual league).

---

## 6. Step 3 — 0–15 goal expectancies

```
λ15_home = s × μ_home × I_home
λ15_away = s × μ_away × I_away
λ15      = λ15_home + λ15_away
```

`I_home`, `I_away` = **early-tendency indexes** from Model B v2 (Data doc §16), normalised so that 1.0 = normal for the competition:

```
I_team = (team index)^w_I        w_I = 0.5  (partial trust — starting value)
```

- Index 0.78 with w_I = 0.5 → factor 0.88 (12% fewer early goals than strength alone predicts).
- If Model B v2 data is insufficient → I = 1.0.

---

## 7. Step 4 — Probabilities for every 0–15 outcome

### 7.1 Score grid

Independent Poisson for each team in the 0–15 period, grid 0–4 goals each (more is negligible):

```
P(h, a) = [e^(−λ15_home) × λ15_home^h / h!] × [e^(−λ15_away) × λ15_away^a / a!]
```

### 7.2 Outcomes

| Market | Formula |
|---|---|
| **Under 0.5** = 0-0 | P(0,0) = e^(−λ15) |
| Over 0.5 | 1 − e^(−λ15) |
| **Draw** | Σ P(i, i) for i = 0…4 |
| Home leads | Σ P(h, a) for h > a |
| Away leads | Σ P(h, a) for h < a |

### 7.3 Optional state-dependence adjustment (DRAW only)

After an early goal, the trailing team pushes and the leading team relaxes, which slightly raises the chance of an equaliser:

```
P(1,1)_adj = P(1,1) × (1 + δ)        δ = 0 by default; calibrated by backtest
(the extra probability is taken proportionally from 1-0 and 0-1)
```

Under 0.5 is **not affected** (it only depends on the first goal).

---

## 8. Step 5 — Fair odds

```
Fair odds = 1 / P
```

This is BETTORY's own price with **zero margin**. A bet has positive expected value only if the offered odds are **above** the fair odds.

```
Minimum acceptable odds = (1 + EV_min) / P          EV_min = 0.05
```

---

## 9. Step 6 — The bookmaker's price: margin and de-vigging

### 9.1 Collect both sides

For each candidate match, enter **both sides** of the bookmaker's 15' total market (Under 0.5 **and** Over 0.5), and all three sides of the 15' result market if available. One side alone cannot reveal the margin.

### 9.2 Margin (overround)

```
Two-way:   m = 1/o_under + 1/o_over − 1
Three-way: m = 1/o_home + 1/o_draw + 1/o_away − 1
```

### 9.3 De-vigging methods

| Method | Formula | Notes |
|---|---|---|
| **Multiplicative** | p_i = (1/o_i) / Σ(1/o_j) | Simple; spreads margin evenly |
| **Power** (default) | find k such that Σ (1/o_i)^k = 1, then p_i = (1/o_i)^k | Loads more margin onto the longshot (Over 0.5); fits favourite–longshot bias |
| Shin | insider-trading model (iterative) | Optional; results close to power for two-way markets |

The **power method is the default** because 15' Under 0.5 is the favourite side.

### 9.4 Expected bookmaker price

What a fair bookmaker *would* offer for our probability, given its measured margin (multiplicative approximation):

```
Expected price = 1 / (P × (1 + m))
```

If the offered price is well above this, either the bookmaker disagrees with us or we have found value — check §10 condition 3.

---

## 10. Step 7 — Value decision

```
EV = offered odds × P − 1
```

| # | Condition for BET |
|---|---|
| 1 | EV ≥ **+5%** (offered odds ≥ minimum acceptable odds) |
| 2 | \|P − P_bookmaker_devig\| ≤ **10 pts** (a bigger disagreement usually means the bookmaker knows something) |
| 3 | \|P_team − P_market\| ≤ **10 pts** (team model and market model agree) |
| 4 | Only the market with the higher EV (Under 0.5 or Draw) is selected |
| 5 | Layer 0, 1 and 5 checks (league screen, data gate, red flags) all passed |

---

## 11. Deriving goal expectancies from market odds (Model C in detail)

### 11.1 Inputs (any subset)

| Market | Gives |
|---|---|
| FT 1X2 | Home/away split |
| FT O/U 1.5 / 2.5 / 3.5 | Total |
| HT O/U 0.5 / 1.5 | First-half total (closest to the 0–15 period) |
| HT Asian 1.0 | First-half total (with push) |

### 11.2 Single line → total λ (root-finding)

De-vig the line (power method), then solve for λ:

```
P(total ≤ n) = Σ_{k=0..n} e^(−λ) λ^k / k!        = de-vigged Under probability
```

| Line | Equation |
|---|---|
| 0.5 | e^(−λ) = p_under → λ = −ln(p_under) (closed form) |
| 1.5 | e^(−λ)(1 + λ) = p_under |
| 2.5 | e^(−λ)(1 + λ + λ²/2) = p_under |

Solve with **bisection** on λ ∈ [0.01, 6] (the left side always decreases as λ grows), to precision 0.0001.

### 11.3 Asian whole line (e.g. HT 1.0)

Under 1.0 wins on 0 goals, **push** on 1 goal, loses on 2+. Over 1.0 wins on 2+, push on 1. De-vig the two prices, then solve:

```
e^(−λ) / (1 − λe^(−λ)) = de-vigged Under share
```

(the push outcome is removed from both sides before comparing).

### 11.4 Several markets → μ_home and μ_away (optimisation)

When 1X2 and totals are both available, find μ_home, μ_away that minimise the squared error between the model's probabilities (from the FT score grid, Dixon–Coles optional) and the de-vigged market probabilities:

```
minimise  Σ_markets (p_model − p_market)²
over      log μ_home, log μ_away   (keeps μ positive; bounds μ ∈ [0.05, 6])
method    L-BFGS-B (or Powell)
```

This is the approach used by penaltyblog's `goal_expectancy` and by the market-calibrated model of Clegg et al. (1X2 + O/U 0.5–4.5).

**HT lines:** convert HT λ to an FT equivalent using `μ_FT = μ_HT / ht_share` before blending, or use them directly with `r` (§5.3) — the engine uses the HT line directly when it is available, because it is closer in time to the 0–15 period.

### 11.5 Consistency check

If totals implied by different lines differ by more than **20%**, mark `MARKET_INCONSISTENT` and set w_mkt = 0.

---

## 12. Settlement conventions

| Setting | Default | Notes |
|---|---|---|
| Cutoff `T` | 00:00–14:59 | A provider minute label "15'" = 14:00–14:59 → **inside** the period; "16'" → outside |
| Own goals | Count | Credited to the benefiting team |
| Disallowed (VAR) goals | Do not count | Use final official events |
| Stoppage time | Irrelevant to 0–15 | |
| Abandoned before 15:00 | Void | Bookmaker rules decide |

Each bookmaker record stores its own `T` and rules; the engine recalculates `s` for that cutoff.

---

## 13. Full worked example

> **All results in this example are for the 0–15 minute period (00:00–14:59)** — not the first half and not the whole match. Only the inputs μ_home and μ_away are whole-match values.
>
> | Same match, different periods | Expected goals | P(no goal) | Fair odds |
> |---|---|---|---|
> | **0–15 min Under 0.5** (this example) | 0.31 | **73.2%** | **1.37** |
> | First half Under 0.5 | ≈ 1.10 | ≈ 33% | ≈ 3.00 |
> | Whole match Under 0.5 | 2.50 | ≈ 8% | ≈ 12 |

### Inputs

| Input | Value |
|---|---|
| Blended μ_home, μ_away (**whole-match** expected goals) | 1.40, 1.10 |
| Competition share s (0–15) | 0.125 |
| Early indexes I_home, I_away | 1.00, 1.00 (for simplicity) |
| Bookmaker A: 15' Under 0.5 / Over 0.5 | 1.30 / 3.20 |
| Bookmaker B: 15' Under 0.5 | 1.45 |

### Steps 3–5

| Step | Result |
|---|---|
| λ15_home = 0.125 × 1.40 | 0.175 |
| λ15_away = 0.125 × 1.10 | 0.1375 |
| λ15 | 0.3125 |
| **P(0-0 at 15') = P(15' Under 0.5)** = e^−0.3125 | **73.2%** → fair odds **1.37** |
| P(1-1 at 15') = 0.732 × 0.175 × 0.1375 | 1.8% |
| **P(15' Draw)** = 73.2% + 1.8% + ~0.0% | **74.9%** → fair odds **1.33** |
| P(Home leads at 15') | 14.2% (14.16%) → fair 7.06 |
| P(Away leads at 15') | 10.9% → fair 9.17 |
| Minimum acceptable odds, 15' Under 0.5 = 1.05 / 0.732 | **1.44** |

### Step 6 — Bookmaker A

| Item | Result |
|---|---|
| Margin = 1/1.30 + 1/3.20 − 1 | **8.2%** |
| Multiplicative de-vig, Under | 71.1% |
| Power de-vig (k ≈ 1.154), Under | 73.9% |
| Expected price for our P = 1 / (0.732 × 1.082) | 1.26 |
| EV at 1.30 = 1.30 × 0.732 − 1 | **−4.9% → SKIP** |

### Step 7 — Bookmaker B

| Item | Result |
|---|---|
| EV at 1.45 = 1.45 × 0.732 − 1 | **+6.1%** ✅ |
| Gap to bookmaker A's de-vigged view (power) | 0.7 pts ✅ |
| Draw at the same bookmaker | compare EV; choose the higher |
| **Decision** | **BET Under 0.5 at bookmaker B** (if other layers pass) |

**Lesson:** at typical 15' margins, most offered prices are **below** fair odds. Value usually appears only when bookmakers disagree, or when BETTORY's team data sees something the market does not. Most matches will be SKIP — that is expected.

---

## 14. Data model

### 14.1 New table `fifteen_min_prices`

| Field | Description |
|---|---|
| fixture_id, pricing_version | |
| mu_home, mu_away | Blended FT expectancies |
| mu_source | TEAM / MARKET / BLEND |
| share_s, share_r, cutoff_T | Time profile used |
| index_home, index_away | Early-tendency factors applied |
| lambda15_home, lambda15_away | |
| p_00, p_over05, p_draw, p_home_lead, p_away_lead | |
| fair_under05, fair_over05, fair_draw, fair_home, fair_away | |
| min_odds_under05, min_odds_draw | |
| delta_state_adj | δ used |
| created_at | |

### 14.2 New table `bookmaker_15min_quotes`

| Field | Description |
|---|---|
| fixture_id, bookmaker_id | |
| snapshot_type | ANALYSIS / BET_TIME / CLOSING |
| odds_under05, odds_over05 | |
| odds_home, odds_draw, odds_away | |
| overround_total, overround_result | |
| devig_method | POWER / MULTIPLICATIVE / SHIN |
| p_under05_devig, p_draw_devig | |
| ev_under05, ev_draw | |
| captured_at, entry_method | |

### 14.3 New table `bookmakers`

| Field | Description |
|---|---|
| id, name | |
| cutoff_T | e.g. `14:59` |
| own_goal_rule, var_rule, abandon_rule | |
| typical_overround_15min | Running average, updated automatically |

---

## 15. Validation

| Test | What it checks |
|---|---|
| **Calibration** | Group predictions in 2-point buckets (70–72%, 72–74%…); actual 0-0 rate should match each bucket |
| **Brier score / log loss** | Overall accuracy vs a baseline (competition average only) |
| **vs bookmaker** | Is BETTORY's P closer to actual outcomes than the bookmaker's de-vigged P? |
| **CLV** | Do our bet prices beat closing prices on average? |
| **Time profile** | Compare predicted vs actual goals in 0–15 per competition, each season |
| **Parameter tuning** | w_team / w_mkt, w_I, δ, K, EV_min — tuned by backtest, recorded in `pricing_version` |

---

## 16. Limitations

- Independent Poisson is an approximation; early-goal dependence is small but non-zero.
- `s` varies by competition and season; small competitions rely on shrinkage.
- Model C depends on manually entered odds; entry errors propagate — validate odds (implied margin between 0% and 15%, else reject).
- Bookmaker margins on 15' markets are high; real value is rare.
- Historical 15' bookmaker odds are not available for free, so EV backtests are estimates (Data doc §9).
- No model guarantees profit.

---

## 17. Decisions

| # | Decision |
|---|---|
| P1 | Price 0–15 markets via **goal expectancy → time share → score grid** |
| P2 | Blend team and market at the **goal-expectancy (μ) level**, 50/50 to start |
| P3 | Time profile = **empirical share per competition**, shrunk (K = 300 goals); never 15/90 |
| P4 | Early tendency from Model B v2 applied as a partial factor (w_I = 0.5) |
| P5 | De-vig with the **power method** by default; always collect both sides of the bookmaker market |
| P6 | BET requires EV ≥ +5% and agreement checks (§10) |
| P7 | Optional state-dependence δ for DRAW, default 0 |
| P8 | Settlement cutoff and rules stored per bookmaker |

---

## 18. Sources

**Goal expectancy and score grids**
- [penaltyblog — Inferring goal expectancies from bookmaker odds](https://penaltyblog.readthedocs.io/en/master/models/goal_expectancy.html)
- [R-bloggers — Expected goals from over/under odds](https://www.r-bloggers.com/2020/09/expected-goals-from-over-under-odds/)
- [OddsPapi — Poisson soccer model: reconstruct correct-score odds](https://oddspapi.io/blog/?p=3120)
- [Aalborg — Assessing the number of goals in soccer matches](https://projekter.aau.dk/projekter/files/14466581/AssessingTheNumberOfGoalsInSoccerMatches.pdf)

**Time-varying goal intensity and in-play pricing**
- [Clegg, Song & Cartlidge — Market-calibrated accelerated failure time model (arXiv 2605.16066)](https://arxiv.org/html/2605.16066)
- [Risk-neutral pricing and hedging of in-play football bets (arXiv 1811.03931)](https://arxiv.org/pdf/1811.03931)
- [Hoog — Modelling prices of in-play football betting markets (Lund)](https://lup.lub.lu.se/luur/download?func=downloadFile&recordOId=4253194&fileOId=4253196)
- [Volf et al. — time-dependent intensity, discussion of Dixon & Robinson (1998)](https://ro.utia.cas.cz/files/Soutez_09/Volf/VOLF_IMAMAN2009.pdf)
- [Andersen — Risk-neutral derivation of in-play prices (Aalborg)](https://projekter.aau.dk/projekter/files/334148300/Masters_Thesis_MortenAndersen_Group1_211c.pdf)
- [Goal intensity rises over time (arXiv 2312.04338)](https://arxiv.org/pdf/2312.04338)
- [SoccerStats — goals by 15-minute segment](https://www.soccerstats.com:443/stats.asp?page=10)

**Margin removal**
- [De-vigging methods overview](https://sharpery.vercel.app/education/devigging-methods)
- [penaltyblog — implied probabilities](https://penaltyblog.readthedocs.io/en/master/implied/index.html)
- [Clarke et al. — adjusting bookmaker odds (Swinburne)](https://researchbank.swinburne.edu.au/file/2069085d-5d5a-4f9c-9f1c-0c52472396cb/1/PDF%20(Published%20version).pdf)
- [Pinnacle no-vig price guide](https://sports-models-kb.pages.dev/de-vigging-pinnacle-nvp)

**Market margins and rules**
- [Correct score margins](https://football-bets-tips.com/articles/football-correct-score-betting/)
- [Stoiximan — minute range markets](https://stoiximan.zendesk.com/hc/en-cy/articles/360018748718)
- [MSport — goal interval markets](https://msportsupportgh.zendesk.com/hc/en-us/articles/4407923074327-Goals-Interval-Race-to-Nth-Number-of-Goals)
