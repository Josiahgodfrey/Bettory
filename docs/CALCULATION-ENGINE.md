# BETTORY — Calculation Engine: What Was Built

**Status:** Phase 2 (calculation engine) implemented on branch `value-calculation`, decision layer pending
**Date:** 2026-11-15 · **Formula** v1.4 (μ-level blend) · **Parameters** `params_v1.5.yaml`
**Related:** [FIFTEEN-MINUTE-ODDS-CALCULATION.md](FIFTEEN-MINUTE-ODDS-CALCULATION.md) (pricing spec), [DATA-SOURCES-AND-MARKET-MODEL.md](DATA-SOURCES-AND-MARKET-MODEL.md) (Models L, B v2, C), [SYSTEM-DOCUMENTATION.md](SYSTEM-DOCUMENTATION.md) (Model A), [DATA-MODEL.md](DATA-MODEL.md) (data statuses, selection-bias rule), [DATA-INGESTION-IMPLEMENTATION.md](DATA-INGESTION-IMPLEMENTATION.md)

The calculation engine turns stored match data and bookmaker prices into BETTORY's own probability and fair odds for its two markets:

| Market | Meaning | Probability |
|---|---|---|
| **Total 0–15 min UNDER 0.5** | no goal before 15' | P(0-0) = e^(−λ15) |
| **Result 0–15 min DRAW** | level at 15' (0-0, 1-1, …) | sum of the score grid's diagonal; needs λ15 split home / away |

Everything below exists to estimate **λ15_home and λ15_away** (expected goals of each team in minutes 0–15) as accurately as possible.

---

## 1. Rules every module follows

| Rule | How |
|---|---|
| **Pure** | `app/calculation` has no database, network or file access (import-linter contract, checked in tests) |
| **Point in time** | Inputs are matches **before** `as_of` (kick-off); a later match raises an error |
| **Fail closed** | Impossible values raise (`InvariantError`); a model without the inputs it needs raises `ModelUnavailableError` with the reason; nothing is silently defaulted |
| **Selection-bias rule** | A window or baseline containing an INCOMPLETE match is `INSUFFICIENT_DATA` for 15' values — the match is never dropped ([DATA-MODEL.md](DATA-MODEL.md) §7.1) |
| **Counts with rates** | Every rate keeps its sample (e.g. `7/10`) |
| **One source of parameters** | All model parameters come from the versioned params file (§3) |

---

## 2. Pipeline

```
competition matches ──► baselines.py ──► s, r, λ15_comp, league averages, Model L league screen
team histories ───────► team_stats.py ─► GENERAL / HOME / AWAY windows, H2H
                          │
                          ├─► Model A  (FT strength + Model L match level) ──► μ_team, P_A
                          ├─► Model B v2 (competition-adjusted early index) ─► index factors I, P_B
main-market odds ───────► Model C  (HT/FT totals + 1X2, de-vigged) ─────────► μ_market, P_C
                          │
                          ▼
                     pricing.py:  μ = w_team·μ_team + w_mkt·μ_market
                                  λ15 = s × μ × I   →  score grid  →  P(Under 0.5), P(Draw), leads
                                  fair odds = 1/P,  minimum odds = (1 + EV_min)/P
bookmaker 15' odds ──► ev.py:     de-vig, EV = odds × P − 1, expected price, gap to bookmaker
```

---

## 3. Parameters: one versioned file

| Item | Detail |
|---|---|
| File | [backend/config/params_v1.5.yaml](../backend/config/params_v1.5.yaml) (active, `PARAMS_FILE` setting) |
| Model | [app/domain/params.py](../backend/app/domain/params.py) — strict: unknown keys, missing keys and out-of-range values are rejected (weights must sum to 1, min < max, valid Model C lines…) |
| Use | Every top-level calculation function receives `params` explicitly. There are **no parameter copies in the code**; a test fails if one appears |
| Traceability | Every price records `params_version` and `pricing_version` |
| Changes | Only by copying to a new version (`params_v1.6.yaml`); a version that produced results is never edited. `Params.with_values({...})` gives validated variants for tests and backtest tuning |
| Not parameters | Mathematical/technical constants stay in code: grid tolerance 0.001, solver interval λ ∈ [0.01, 6], μ bounds [0.05, 6] |

**Changes from 1.4 to 1.5:** added `model_a.ft_weight`, `model_a.rating_prior_matches` (new, §5.4), `model_c.input_lines`, `h2h.*`, `red_flags.recent_window`, `value.overround_min`, `value.devig_method`; removed `data_gate.min_complete_per_view` (superseded by the selection-bias rule). The 15' cutoff used by ingestion (`settlement.cutoff_label_minute`) is also read from the file.

---

## 4. Modules

All paths under `backend/app/calculation/`.

| File | Does | Doc |
|---|---|---|
| [invariants.py](../backend/app/calculation/invariants.py) | Checks: λ ≥ 0, 0 ≤ P ≤ 1, grid sums to 1 ± 0.001, fair odds > 1 | Dev plan 2.13 |
| [poisson.py](../backend/app/calculation/poisson.py) | Poisson pmf/cdf; score grid sized automatically (never renormalised); outcomes; optional draw adjustment δ; λ solvers for half lines and Asian whole lines | Pricing 7, 11.2–11.3 |
| [devig.py](../backend/app/calculation/devig.py) | Overround; power (default), multiplicative, Shin; rejects one-sided markets, prices ≤ 1 and implausible margins | Pricing 9 |
| [baselines.py](../backend/app/calculation/baselines.py) | Competition tallies, pooled seasons, s and r shrunk toward global shares (K = 300 goals), λ15_comp, Model L league level, review flag for s outside [0.09, 0.16] | Pricing 5; Data 14.2, 16 |
| [team_stats.py](../backend/app/calculation/team_stats.py) | Last-10 windows per view: W-D-L, FT/HT goals, 0-0 / level / early-goal rates, early goals scored/conceded, recent early goals; H2H (≤ 10 meetings, ≤ 5 years) | System 4–6; Data 11, 13.4 |
| [models/\_\_init\_\_.py](../backend/app/calculation/models/__init__.py) | Shared attack × defence strength (venue/general blend) with rating shrinkage | System 7; Data 14.3 |
| [models/model_l.py](../backend/app/calculation/models/model_l.py) | Model L match level: HT ratings → λ_HT → λ15 = r × λ_HT | Data 14.3 |
| [models/model_a.py](../backend/app/calculation/models/model_a.py) | Model A = ft_weight × A_FT + (1 − ft_weight) × A_HT at the λ15 level; FT-equivalent μ for the blend | System 7; Data 15/17 |
| [models/model_b2.py](../backend/app/calculation/models/model_b2.py) | Team index (actual + k)/(expected + k) per view, venue blend, baseline choice for cups, factor I = index^w_I | Data 16; Pricing 6 |
| [models/model_c.py](../backend/app/calculation/models/model_c.py) | Market-implied λ15: listed lines only, HT before FT, 1X2 split by least squares (L-BFGS-B, Powell fallback), 20% consistency check | Pricing 11; Data 6 |
| [pricing.py](../backend/app/calculation/pricing.py) | v1.4 engine: μ blend → λ15 = s × μ × I → grid → probabilities, fair and minimum odds; also P_team and P_market | Pricing 3–8 |
| [ev.py](../backend/app/calculation/ev.py) | EV, minimum odds, expected price; evaluates a bookmaker's 15' quote ("Goal in 1-15 minutes" NO, "1x2 - 15 minutes" DRAW) | Pricing 8–10 |

---

## 5. Findings from real data

### 5.1 Baselines (2025-26)

| League | s | r | Model L P(0-0) | Observed 0-0 at 15' |
|---|---|---|---|---|
| Premier League | 0.115 | 0.264 | 73.1% | 71.8% |
| Bundesliga | 0.112 | 0.253 | 69.4% | 69.6% |

Real shares are a little below the docs' starting assumptions (s ≈ 0.125, r ≈ 0.28); the engine uses the measured values.

### 5.2 Model C on real bookmaker prices

One match, 9 bookmakers (API-Football): after restricting inputs to the listed lines, all 9 are consistent — P_C(Under 0.5) 69.2–71.2%, P_C(Draw) 71.0–72.8%. Marathonbet's own 15' Draw price de-vigs to 70.4% vs Model C 71.0% from its HT/FT prices.

### 5.3 Point-in-time pricing, team model only (no odds)

Last 90 matches each of Premier League and Bundesliga 2025-26, every price using only earlier data:

| | No rating shrinkage (doc formula) | params 1.5 |
|---|---|---|
| Prediction spread | 54% – 88% | 66% – 80% |
| Brier score (league-only baseline 0.2127) | 0.2134 — worse than baseline | **0.2116 — better than baseline** |
| Mean predicted vs actual P(Under 0.5) | 73.9% vs 69.4% | 73.1% vs 69.4% |

### 5.4 Rating shrinkage (new in params 1.5)

The doc formula takes 10-match goal rates at face value. Measured on ENG1 + GER1 2025-26, the empirical-Bayes strength m = within-team variance / between-team variance is mostly 14–40 for FT goals and higher for HT goals: over 10 matches most of the spread between teams is noise. The engine now uses

```
rate = (goals + m × league rate) / (matches + m)        m = 20 (FT), 30 (HT)  — starting values
```

before forming each rating. It removes extreme predictions and the "team never scores → early goal impossible" case.

### 5.5 Open issues for the backtest (Phase 4)

1. **Level bias:** about 4 points too optimistic on Under 0.5 in this sample. 180 fixtures from the end of one season are too few to conclude or to tune on; the multi-season backtest decides.
2. **De-vig method:** power and multiplicative give HT λ about 4% apart on the same prices.
3. **All parameters** (weights, K, k, m, w_I, δ, EV_min) are starting values to tune on one period and validate on a later, unseen one.

---

## 6. Tests

| Kind | Examples |
|---|---|
| Golden (docs' worked examples, exact) | λ15 0.3125 → 73.2% / fair 1.37 / min 1.44; Draw 74.9%; Bookmaker A 8.2% margin, power 73.9%, EV −4.9%; Model L 72.5% and 77.9%; Model A 81.5%; Model B v2 index 0.78; Model C 73.5% |
| Property (Hypothesis) | P(0-0) falls as λ rises; P(Draw) ≥ P(0-0); grids sum to 1; solvers invert exactly; shrunk values lie between raw and prior |
| Unit | Every rejection path: bad odds, one-sided markets, INCOMPLETE windows, point-in-time breaches, invalid parameters |
| Guard | No parameter copies in calculation code; params file name = version; import boundaries |

All checks are green: ruff, mypy (strict), import-linter, 258 tests.
