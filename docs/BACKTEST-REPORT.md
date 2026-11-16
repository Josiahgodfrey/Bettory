# BETTORY — Backtest Report (Phase 4, Gate B)

> **Status: preliminary.** One season with goal minutes (2025-26, three leagues). Gate B is **not passed**, and
> cannot be passed on this data alone (see §4). The decision on parameters waits for the multi-season backfill.

| | |
|---|---|
| Date | 2026-11-16 |
| params_version / formula | 1.5 / v1.4 (no parameter changed for this report) |
| Data | England 1, Germany 1, Austria 1 — season 2025-26, 881 fixtures, all with goal minutes |
| Validation period | sessions from 2026-02-01 (unseen; nothing was tuned on it) |
| Main-market odds | **none** in the database → Model C unavailable, estimated-value backtest not evaluable |
| Reproduce | `python -m app.cli backtest --from 2025-08-01 --to 2026-05-31 --comp ENG1 --comp GER1 --comp AUT1 --split 2026-02-01` |

---

## 1. Method

The runner replays **sessions**, not days: for every NIGHT / NOON session (ARCHITECTURE 12.2) with fixtures, it
analyses at the moment the live system would — `as_of = run_start = min(deadline, first kick-off − 2 h) − 45 min` —
and calls the **same code as live**: point-in-time reads → Models A / L / B v2 / C → price → decision layers →
ranking and the 50-match cap. Results are read **separately, afterwards**, only to settle.

| Guarantee | How it is enforced and tested |
|---|---|
| No look-ahead | Every read goes through `PointInTimeReader` (SQL `kickoff < as_of` + a second check per row). Test: changing a fixture's result, and the result of an earlier match in the same session, changes no verdict |
| Live-identical decisions | The runner calls `AnalysisContext.evaluate`, `verify` and `rank_and_cap`; no backtest-only decision logic |
| No writes | The run is a `READ ONLY` transaction; tested |
| Reproducible | Output records params_version; rows, summary and report written to `backend/backtests/<run>/` |
| Fast, without changing results | Point-in-time reads are memoised per `as_of` (`ReadCache`); parameter variations share one cache. Tested: identical rows with and without it, a warm run reads no history, different `as_of` never share. Full season 88 s → 34 s; a further variation on cached reads ≈ 16× faster |
| Selection bias | INCOMPLETE matches are never dropped: a window containing one is INSUFFICIENT_DATA (DATA-MODEL 7.1) |

What is measured (settings in [config/backtest.yaml](../backend/config/backtest.yaml), never in code):

| Measure | On which fixtures |
|---|---|
| Calibration (2-pt buckets), Brier, log loss | All **priced** fixtures (the model) and the **listed** ones (the bets) |
| Model comparison | Final price, Model A, Model B v2, Model C, **competition-only baseline** — on the same fixtures |
| ROI at the minimum odds | Every listed fixture settled at its own minimum odds: checks the report's promise "bet only at or above these odds" |
| Estimated value (data doc 9) | Bookmaker 15' price assumed = Model C fair odds × (1 − 7 %) — needs main-market odds |

---

## 2. Results

### 2.1 Decisions

| | QUALIFIED | SKIP | INSUFFICIENT_DATA | ERROR |
|---|---|---|---|---|
| Austria 1 | 50 | 67 | 78 | 0 |
| England 1 | 127 | 53 | 200 | 0 |
| Germany 1 | 66 | 189 | 51 | 0 |
| **All** | **243** | 309 | 329 | **0** |

INSUFFICIENT_DATA is mostly the first months of the season: the team windows reach back into 2024-25, which has
no goal minutes (the selection-bias rule refuses to fill them). Mean 1.5 qualified per session, max 11; the
cap was never reached. Germany's many SKIPs are the league screen (P_L < 70 %).

### 2.2 Gate B

| Criterion | Status | Detail |
|---|---|---|
| ≥ 300 qualified selections | **FAIL** | 243 |
| Calibration: gap ≤ max(3 pts, 2 × SE) per populated bucket | **PASS** | 4 buckets judged, none outside; tightest: 74–76 %, +6.2 pts (n = 90, allowed ±9.1) |
| Beats the competition-only baseline | **PASS (barely)** | Brier 0.2009 vs 0.2013 on 378 fixtures |
| Estimated ROI > 0 on the unseen period | **NOT EVALUABLE** | no main-market odds |

### 2.3 Calibration and models

| Model (Under 0.5, 378 fixtures) | Mean predicted | Actual | Brier |
|---|---|---|---|
| Final price | 72.3 % | 72.2 % | 0.2009 |
| Model A | 71.8 % | 72.2 % | 0.2009 |
| Model B v2 | 72.4 % | 72.2 % | 0.2013 |
| Competition only | 71.7 % | 72.2 % | 0.2013 |

- **On average the model is right** (72.1 % predicted vs 72.4 % actual over 485 priced fixtures; Draw 74.0 % vs 74.2 %).
- **It separates fixtures only weakly.** 80 % of predictions fall between 68 % and 76 %, and the final price beats the
  competition average by only 0.0004 Brier — too small to call significant on 378 fixtures.
- **The most confident predictions are over-confident:** fixtures priced ≥ 76 % went 0-0 in 65.4 %, against 77.3 %
  predicted (n = 52, −11.9 pts, about 2 standard errors). This matches the earlier 180-fixture finding.

### 2.4 ROI at the minimum odds (listed fixtures)

| Period | Market | Bets | Hit rate | Break-even | ROI |
|---|---|---|---|---|---|
| validation | Under 0.5 | 218 | 69.7 % | 67.8 % | **+3.1 %** |
| validation | Draw | 218 | 72.5 % | 69.4 % | **+4.6 %** |
| all | Under 0.5 | 243 | 72.0 % | 67.8 % | +6.4 % |

If the probabilities were exact, ROI at the minimum odds would be about +8 % (the no-market EV threshold). The
validation result is positive but below that: the listed fixtures are slightly over-estimated.
These are **minimum-odds** results: they assume the bookmaker offered at least the minimum odds on every
listed fixture, which is unknown (§4).

### 2.5 Slices worth testing on new seasons (found after looking — hypotheses, not results)

| Slice (listed) | n | Predicted | Actual | ROI at min odds |
|---|---|---|---|---|
| Grade A | 205 | 72.9 % | 75.1 % | +11.1 % |
| Grade B | 38 | 74.8 % | **55.3 %** | −18.8 % |
| P ≥ 76 % | 42 | 77.4 % | **61.9 %** | −13.5 % |
| P < 76 % | 201 | 72.3 % | 74.1 % | +10.6 % |

Grade B (models disagree by 5–10 pts) and the highest predictions did badly. Both are plausible mechanisms
(disagreement = uncertainty; extreme predictions = noise in 10-match windows), but they were found **after**
seeing the data, on one season. Changing parameters now would tune on the validation period and make it
worthless. They are written down here **before** the next seasons are loaded, so the next run tests them honestly.

---

## 3. Findings about the method itself

1. **The estimated-value scenario can never produce a bet under formula v1.4.** v1.4 requires offered odds ≥ Model C's
   fair odds; the scenario assumes the bookmaker offers Model C's fair odds minus a margin, which is always lower.
   The scenario is implemented and tested, and will correctly report 0 bets when odds exist. Gate B's ROI criterion
   therefore needs a different value estimate — options in §5.
2. **A flat ±3 pts tolerance was tighter than the noise.** With n = 90 in a bucket the standard error of the actual
   rate is 4.6 pts, so a perfectly calibrated model fails "±3 pts" in about half of such buckets. ±3 pts is a fair
   tolerance only from roughly 900 fixtures per bucket; with ~4 populated buckets and ~160 priced fixtures per
   league-season that is about 20 league-seasons with goal minutes (e.g. 3 seasons × 7 leagues).
   **Decided 2026-11-16 (owner):** a bucket passes when |gap| ≤ max(3 pts, 2 × SE)
   (`calibration.noise_multiplier: 2` in [config/backtest.yaml](../backend/config/backtest.yaml)). With the old flat
   rule the same run failed on the 74–76 % bucket (+6.2 pts).
   Caveat: buckets with fewer than 50 fixtures are still not judged, so the over-confident top end (76–78 %:
   −12.6 pts, n = 42) is not yet tested by the gate — it stays open as the §2.5 hypothesis.

### 3.1 Step 1 measurements (2026-11-16, same data, same params 1.5)

The report now adds probability-quality statistics and paired intervals
([RESEARCH-PHASE4-TUNING.md](RESEARCH-PHASE4-TUNING.md) §3, §6). Under 0.5, 378 fixtures with every model:

| Model | Brier | Reliability | Resolution | Skill vs competition-only | Mean z | Spiegelhalter z | Slope |
|---|---|---|---|---|---|---|---|
| Final price | 0.2009 | 0.00663 | 0.00648 | +0.18 % | −0.04 | +0.19 | **0.38** |
| Model A | 0.2009 | 0.00365 | 0.00323 | +0.17 % | +0.17 | −0.09 | **0.20** |
| Model B v2 | 0.2013 | 0.00961 | 0.00931 | +0.01 % | −0.09 | +0.33 | **0.26** |

| Paired (model − competition-only, 95 %, sessions resampled) | Brier difference | Verdict |
|---|---|---|
| Final price, Under 0.5 | −0.0004 [−0.0036, +0.0030] | not distinguishable |
| Model A, Under 0.5 | −0.0003 [−0.0018, +0.0011] | not distinguishable |
| Final price, Draw | −0.0003 [−0.0023, +0.0020] | not distinguishable |

What this says:
- **The average is right** (both z ≈ 0), **the spread is not**: a recalibration slope of 0.38 means real
  differences between fixtures are about a third of what the model predicts. What the model gains by separating
  matches (resolution) it loses to over-confident differences (reliability) — net skill ≈ 0.
- The earlier "Beats competition-only baseline: PASS" was a lower Brier by 0.0004, **not statistically
  distinguishable from zero**. Gate B criterion 3 must use the paired interval (decision 1, pending).
- Direction for tuning (to be tested, not concluded): much stronger shrinkage of team ratings / indexes, or a
  recalibration of P toward the competition level. Both belong in the pre-registered grid.
- Observed 15' share of goals: s = 13.9 % (AUT1), 11.6 % (ENG1), 11.1 % (GER1) in 2025-26 — per-league baselines
  are justified.
- ROI at the minimum odds, validation: Under 0.5 +3.1 % with interval [−6.4 %, +12.1 %]; Draw +4.6 %
  [−4.7 %, +13.3 %] — both include zero, as §6 of the research notes predicted.

---

## 4. Why Gate B cannot be decided yet

| Missing | Effect | Remedy |
|---|---|---|
| Goal minutes before 2025-26 | One season; early-season fixtures INSUFFICIENT_DATA; 243 selections | API-Football Pro backfill: 3+ seasons × pilot leagues |
| Main-market odds | No Model C → EV threshold 8 % instead of 5 %; ROI criterion not evaluable | football-data.co.uk Pinnacle **pre-match** odds (closing odds are invisible at report time) — blocked on this network; see [FOOTBALL-DATA-CO-UK-SETUP.md](FOOTBALL-DATA-CO-UK-SETUP.md) |
| Real 15' bookmaker prices | Whether the bookmaker offers ≥ minimum odds is unknown | Paper betting in Phase 7 (live 15' quotes) |

---

## 5. Owner decisions

All decided on 2026-11-16 and fixed in the pre-registered plan
[config/tuning_v1.yaml](../backend/config/tuning_v1.yaml) (`python -m app.cli tuning-plan`):

1. **Calibration criterion:** |gap| ≤ max(3 pts, 2 × SE) per populated bucket (§3).
2. **Beating the baseline:** the paired Brier difference with the competition-only baseline must have the upper end
   of its 95 % interval (sessions resampled) below 0 — a lower average alone is not enough (§3.1).
3. **Profit:** no historical profit criterion. ROI at the minimum odds is reported with its interval as a
   consistency check; profit is proven in paper betting (Phase 7) with sequential e-values
   ([RESEARCH-PHASE4-TUNING.md](RESEARCH-PHASE4-TUNING.md) §3.6, §6).
4. **Variation grid:** rating shrinkage 20 / 40 / 80 / 160 prior matches × early-goal index trust 0 / 0.25 / 0.5 ×
   team weight 1.0 / 0.5 / 0.25 = 36 variations; selection by lowest log loss of P(Under 0.5) on 2022-23 – 2024-25;
   2025-26 is the holdout, evaluated once by the selected variation and by params 1.5 (= variation v08).
5. **Grade B:** if hypothesis H1 is confirmed, Grade B fixtures are no longer listed in reports (a decision
   setting; probabilities are not retuned for it).

## 6. Pre-registration

The plan was written and committed **before any model result on the tuning seasons (2022-23 to 2024-25) was
computed**. *Correction (2026-11-17):* an earlier version of this sentence, and tuning_v1's header, said "before the
tuning seasons were loaded"; in fact part of ENG1 2024-25 was already in the database through the goal-minute
backfill (only coverage counts had been looked at). Once a real tuning run uses a plan, its SHA-256 is recorded in
`config/tuning_lock.yaml` and the tool refuses any later edit; a changed plan must be a new file that supersedes it,
and every report lists every plan and every variation that was run.

## 7. The tuning tool and its dry run (2026-11-17)

`python -m app.cli tune` (dry run by default; `--real` for the run that counts):
[app/backtest/tuning.py](../backend/app/backtest/tuning.py), report in
[tuning_report.py](../backend/app/backtest/tuning_report.py).

- every variation is scored on the **same** fixtures (priced in all variations, result known);
- variations with identical predictions are grouped: the report gives the **effective number of trials** and the
  grid dimensions that are **not testable**;
- the holdout is run once for the selected variation and once for the base; Gate B uses the plan's rules;
- a real run locks the plan first and writes `params_v1.6.yaml` only if a non-base variation wins (never
  overwriting); a dry run may override the periods and never locks or writes.

**Dry run on the data at hand** (tuning Aug 2025 – Jan 2026, holdout Feb – May 2026; a test of the tool, not
evidence; 55 s for 36 variations):

| Result | Value |
|---|---|
| Fixtures scored in every variation | 143 |
| Effective trials | **12 of 36** — `team_vs_market` not testable (no main-market odds) |
| Best | 0.57314 log loss = the base v08 (m20 / wI0.5); 12 variations within the tie tolerance → base kept |
| Rating shrinkage 20 → 160 | changes log loss by ≤ 0.0003: almost no effect |
| Early-goal index trust 0 / 0.25 / 0.5 | 0.5 best (+0.0025 worse at 0) — **the best value is at the edge of the grid** |
| Holdout (base) | 342 fixtures, calibration slope 0.21; paired Brier vs competition-only +0.0001 [−0.0033, +0.0036] → FAIL |
| Hypotheses | too few sessions in the short tuning window; holdout H1 Grade B −0.195 [−0.399, +0.006] (26 sessions → too few), H2 −0.133 [−0.291, +0.019] not confirmed |

Two points for the owner (see the session notes): the 2025-26 "holdout" has already been examined (first backtest,
step 1, this dry run), and the early-goal index trust is best at the edge of the registered grid.

## 8. Plan v2: the validation protocol (2026-11-17)

Owner decisions of 2026-11-17 ("yes to both recommendations"):
[config/tuning_v2.yaml](../backend/config/tuning_v2.yaml) supersedes v1 (never used by a real run). The tools use
the newest registered plan by default (`python -m app.cli tuning-plan`).

| Element | What it guarantees |
|---|---|
| **Plan history** | `supersedes` + `change_reason` are validated (the old plan must exist, have a lower version and an earlier or equal registration) and every tuning report prints the full history: nobody can quietly replace a plan |
| **Wider grid** | early-goal index trust 0 / 0.25 / 0.5 / **0.75 / 1.0** (60 variations): the v1 dry run had found the best value at the edge of its grid |
| **Honest holdout** | `holdout_status: examined_before_registration` with the note of what was examined; every report shows it as a banner and Gate B carries a **NOTE** (it never blocks, never hides) |
| **Prospective period** | 2026-11-18 → 2027-06-30, starting **after** the registration date (validated): fixtures not yet played when the plan was fixed, the truly unseen test. Every later run evaluates it automatically (selected and base params, beats-the-baseline rule) as matches are played and loaded; until then it shows PENDING |
| **One rule set** | the normal backtest report now uses the same Gate B rules as the tuning report (paired interval vs the competition-only baseline; profit = PAPER BETTING, never a historical number) |

Dry run of v2 on the data at hand (tuning Aug 2025 – Jan 2026, holdout Feb – May 2026 — a test of the tool, not
evidence; 83 s for 60 variations):

| Result | Value |
|---|---|
| Effective trials | 20 of 60 (`team_vs_market` not testable without main-market odds) |
| Selected | **v14 (m20 / wI1.0 / team0.5)**: log loss −0.0022 vs the base on the tuning months — the best value is again at the edge, but 1.0 (full trust in the index) is the natural maximum of the parameter |
| Holdout | paired Brier vs competition-only +0.0011 [−0.0040, +0.0069] → FAIL; calibration PASS (1 bucket judged) |
| Prospective | PENDING — starts 2026-11-18 |

Read with care: the tuning window here is five months of one season; the real run tunes on three seasons.
