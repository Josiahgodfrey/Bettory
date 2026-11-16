# BETTORY — Research Notes for Phase 4 (tuning, validation, Gate B)

> Collected 2026-11-16 from academic papers, statistics references and practitioner sources, as preparation for
> the next build: the **tuning and validation tool** and the real **Gate B** decision. Each section ends with
> what it means for BETTORY. Sources are listed in §10; their reliability varies and is marked.
>
> Reliability marks: **[P]** peer-reviewed / preprint / primary documentation · **[S]** secondary summary or
> textbook-style reference · **[B]** blog, vendor or forum: a hint to check, never a fact on its own.

---

## 0. The eight findings that change what we build

1. **The market usually already knows what a team model knows.** On 7,220 Serie A matches the best weight of a
   Dixon-Coles model pooled with the closing price was **0.000**; even a shots-on-target model that added
   information to the goals model added nothing to the market [P, §4]. → Our `w_team / w_mkt` blend must be
   *tuned*, and we should expect the market weight to win where Model C exists.
2. **So BETTORY's possible edge is not "beating the market" — it is a derived-market mispricing**: soft bookmakers
   pricing the 15' markets less carefully than their own main markets. Historical main-market odds can test the
   *model*; only **real 15' quotes** (paper betting, Phase 7) can test the *edge* (§4).
3. **Our current profit evidence is noise.** +3.1 % ROI on 218 validation bets is z = 0.70. At our odds (~1.46,
   hit rate ~72 %) the 95 % interval after 300 bets is still ±7.4 points. "ROI > 0" with ~300 bets is not a
   meaningful gate (§6).
4. **Every variation we try must be counted.** Trying settings until one works inflates results; the fix is a
   small, pre-declared set of variations, one selection rule fixed in advance, and an unseen period touched
   **once** (§2).
5. **Compare models with paired tests, not two numbers.** Brier 0.2009 vs 0.2013 says nothing without a paired
   confidence interval on the per-match difference (§3.4).
6. **Calibration can be tested without bins** (Spiegelhalter z) and the Brier score decomposes into
   reliability − resolution + uncertainty; our model's weakness so far is **resolution** (it separates matches
   weakly), not reliability (§3).
7. **Goal rates rise during each half; the opening minutes are among the quietest** [P/S, §5]. Stoppage-time
   rule changes since 2023-24 lengthened matches and so change the *share* of goals falling in 0–15' (`s`) —
   per-season baselines matter.
8. **Data-source facts verified**: football-data.co.uk pre-match Pinnacle odds are collected **Friday afternoons
   (weekend) and Tuesday afternoons (midweek)**, closing odds carry a **C** (`PSCH`, `PC>2.5`) — our parser reads
   them correctly. The site's certificate chain also failed verification from a clean network (§7).

---

## 1. What we are about to build (context)

The next tool (DEVELOPMENT-PLAN 4.5) runs the backtest over **pre-declared parameter variations** on a
**tuning period**, selects one by a **fixed rule**, then validates it **once** on an **unseen period**, tests the
pre-registered hypotheses (Grade B, predictions ≥ 76 %), and writes a new params version plus a Gate B report.
Data: ENG1 / GER1 / AUT1, 2022-23 to 2025-26, as the daily backfill completes ([DAILY-BACKFILL.md](DAILY-BACKFILL.md)).

---

## 2. Backtest validity: leakage, tuning and overfitting

### 2.1 Leakage
- The most common error is information from after the bet time — e.g. testing with closing data a model meant
  for earlier prices [B]. Features built from windows can silently include future values even when the split is
  right [S].
- **BETTORY status:** handled — point-in-time reader with a second guard; analysis at the session `run_start`;
  results read only afterwards; a test proves a changed result changes no verdict. Keep it that way: tuning code
  must call the same `run_backtest`, never a shortcut.

### 2.2 Splits for time-ordered data
- Random k-fold leaks the future; use **expanding-window (walk-forward)** splits: train/tune on the past,
  evaluate on the next period [S]. Tuning and evaluating on the same period is optimistically biased; the remedy
  is **nested** validation — an inner loop for tuning, an outer loop for evaluation — and a **final holdout
  touched once** [S].
- **For BETTORY:** our "training" is only the rolling 10-match windows and pooled baselines, which are already
  point-in-time. What we *tune* are a handful of global parameters, so a simple scheme is enough:
  - tuning period: 2022-23 → 2024-25 (walk-forward, every session point-in-time)
  - holdout: 2025-26, evaluated **once** with the chosen params; the result is reported whatever it is
  - optional robustness: also report each tuning season separately (does the winner win in every season?)

### 2.3 Multiple testing ("garden of forking paths")
- Every reasonable-looking analysis choice made after seeing data is an implicit extra test; together they
  inflate false positives even without bad intent. Pre-registration — committing to the analysis before seeing
  the data — is the main antidote; a multiverse / specification report (show all variations) complements it [S].
- In finance the same problem is quantified: the **Probability of Backtest Overfitting (PBO)** via
  combinatorially symmetric cross-validation [P], and the **Deflated Sharpe Ratio**, which corrects the best
  result for the number of trials (the expected *luckiest* result grows with the number of variants) [P].
  Hold-out alone was found unreliable for investment backtests when many variants are tried [P].
- **For BETTORY:**
  - declare the variation grid in a file **before** running it (version-controlled, dated);
  - keep it small (≈ 10–30 combinations), report **all** results, and record the trial count in the report;
  - the two hypotheses from the first run (Grade B, P ≥ 76 %) are already written down in BACKTEST-REPORT §2.5 —
    test exactly those on the new seasons, nothing added afterwards counts as confirmed;
  - with many variants, compute a PBO-style check (CSCV over seasons/blocks) as a warning signal.

---

## 3. Evaluating probabilities

### 3.1 Brier score decomposition (Murphy 1973)
`Brier = REL − RES + UNC` [S/P]:
- **REL** (reliability) — penalty when predicted probabilities differ from observed frequencies (calibration);
- **RES** (resolution) — reward for forecasts that vary *with* outcomes (discrimination);
- **UNC** (uncertainty) — base-rate variance, the Brier score of always predicting the average.
- **Brier skill score** `BSS = 1 − Brier / Brier_ref`; with the climatology reference it equals (RES − REL)/UNC.
  Read it next to the Brier score: a low Brier on an easy base rate can still be no skill [S].
- Empirical REL/RES depend on the binning [S].
- **For BETTORY:** report REL, RES, UNC and **BSS against the competition-only baseline** for every model. The
  first run's numbers (model 72.3 % vs actual 72.2 %, Brier barely below the league baseline) mean REL is small
  and RES is small: the model is honest but not discriminating. Tuning should aim at RES without losing REL.

### 3.2 Which score to optimise
- For football 1X2 the Ranked Probability Score was proposed (outcomes are ordered) [P], but later work found
  the **ignorance score (log loss)** outperforms RPS and Brier and is the only local proper score [P]. Log loss
  also has a betting interpretation (staking on the wrong outcome is ruin) [P].
- Our markets are **binary** (Under 0.5 yes/no; Draw yes/no), so RPS does not apply.
- **For BETTORY:** select parameters by **log loss on the tuning period** (pre-declared), report Brier and its
  decomposition alongside. Never choose by ROI: ROI is far noisier (§6) and rewards luck.

### 3.3 Calibration tests
- **Calibration-in-the-large z**: `z = Σ(o_i − p_i) / sqrt(Σ p_i(1 − p_i))` — is the *average* right? (The vendor
  page cited below calls this "Spiegelhalter z"; that is a mislabel.) First run by hand: 485 priced fixtures,
  72.4 % actual vs 72.1 % predicted → z ≈ 0.15.
- **Spiegelhalter z (1986)**, as implemented in standard validation software:
  `z = Σ(o_i − p_i)(1 − 2p_i) / sqrt(Σ(1 − 2p_i)² p_i(1 − p_i))` — Brier-score based, bin-free. Both are in the
  report since step 1 (2026-11-16).
- Hosmer–Lemeshow is fragile to binning; bin-free "moderate calibration" tests (bridge test) and e-value
  versions exist [P].
- **For BETTORY:** keep the 2-pt buckets with the noise-aware tolerance (owner decision 2026-11-16) for the gate,
  and add the Spiegelhalter z plus a calibration **slope/intercept** (logistic recalibration fit) to the report:
  a slope < 1 is exactly the "top buckets over-confident" pattern we saw.

### 3.4 Comparing two models (is A really better than B?)
- Form the per-match difference `d_i = score_A,i − score_B,i`; test its mean with a **Diebold–Mariano**-type
  statistic (HAC variance for serial dependence) or a **paired bootstrap** of `d_i` [P]. When observations are
  dependent, resample **blocks** (e.g. whole sessions or matchdays) rather than single matches [P].
- **For BETTORY:** every "model vs baseline" and "variation vs current params" comparison gets a paired 95 %
  interval, bootstrapped by **session** (matches in one session share a matchday and an `as_of`). Gate B's
  "beats the competition-only baseline" should require the interval to exclude 0, not just a lower number.

### 3.5 Recalibration (if the model is mis-calibrated)
- **Platt scaling** (logistic, 2 parameters) beats **isotonic regression** on small calibration sets (below
  ~1–2 thousand cases) because isotonic overfits; evidence is mixed at larger sizes [P/B].
- **For BETTORY:** if the tuning period shows a calibration slope ≠ 1, a Platt-style recalibration of P (fitted on
  the tuning seasons only) is the safe option; it would be a new, versioned parameter pair, validated once.

### 3.6 Monitoring live results without cheating (Phase 7)
- Classical tests are invalid if you look repeatedly and stop when significant. **E-values** for comparing
  forecasts allow **optional stopping**: monitor continuously, stop any time, still valid [P].
- **For BETTORY:** paper betting (Phase 7) should use a sequential e-value comparison (BETTORY vs bookmaker
  de-vigged 15' price) instead of "check the ROI every week".

---

## 4. Model versus market (our `w_team / w_mkt` and Model C)

- **Benter (1994)**, the classic professional system: fit a fundamental model, then combine its probabilities with
  the **public odds** in a second-stage logit; the combination beats both. A secondary summary reports the
  fundamental model alone improved only slightly on the public odds [P/S].
- **Serie A, 7,220 matches (2026)**: logarithmic opinion pool of Dixon-Coles and the margin-free closing price;
  the fitted model weight is **0.000**, log loss rises monotonically with it on validation *and* test data; the
  model is *better calibrated* than the market (slope 0.995 vs 1.103) but much **less sharp** — the market wins
  on discrimination [P].
- **Kaunitz, Zhong & Kreiner (2017)**: using the bookmakers' own consensus odds as the "true" probability and
  betting where one book was above it was profitable in 10-year simulation and 5 months of real money — until
  bookmakers **limited the accounts** [P].
- Favourite–longshot bias exists in many football markets; the **draw** behaves differently (several studies find
  draws relatively under-priced); exchanges show the reverse bias [P].
- **For BETTORY:**
  1. Replace the fixed 50/50 μ-blend by a **tuned log-opinion pool** weight between the team price and Model C
     (or tune `blend.team_weight` directly) — and expect it near the market side.
  2. The edge hypothesis to test is: *soft bookmakers' 15' prices deviate from what their own main-market prices
     imply.* Model C already turns main-market prices into a 15' fair price; the minimum-odds rule
     (`≥ Model C fair odds`) is exactly a Kaunitz-style "beat the consensus" rule applied to a derived market.
  3. Historical data cannot test (2) — no historical 15' odds exist. Phase 7 paper betting must record the
     offered 15' odds **and** the main-market odds at the same moment.
  4. Plan for **account limits**: several bookmakers, modest stakes; it is a known industry response to winners.

---

## 5. Goal timing and the 0–15 window

- **Dixon & Robinson (1998)**, >4,000 English matches, ~10,400 goal times: a non-homogeneous birth process;
  **scoring rates gradually increase during the match** and depend on the current score; no evidence that a team
  is most vulnerable right after scoring [P/S].
- The opening 15 minutes are usually among the lowest-scoring intervals, more goals come late in each half; but
  competitions differ (e.g. one Copa América had 24 % of goals in the first 15') [P/S].
- **Dixon & Coles (1997)** low-score correction: independent Poisson mis-states 0-0 / 1-0 / 0-1 / 1-1; a
  dependence parameter ρ (sign conventions differ between sources!) corrects it; plus time-decay weighting
  `exp(−ξ·days)` — ξ = 0.0065/day is a ~107-day half-life, 0.002/day ~347 days [P/S].
- Stoppage time: the 2023-24 Premier League change was expected to raise added time from ~8½ to ~11½ minutes
  per match; 2024-25 partly reversed it (time for goal celebrations only beyond 30 s) [B — official statements].
- **For BETTORY:**
  - Our `s` (share of FT goals before 15') is a ratio to *full-match* goals; longer matches raise FT goals
    without touching 0–15', so `s` can drift **by season and rule regime**. Check `s` per season in the tuning
    data; the HT-based ratio `r` (Model L) is less affected by second-half stoppage changes.
  - Our `state_dependence.delta` plays the Dixon-Coles ρ role for the 1-1 cell; in 15 minutes 1-1 is rare, so
    expect δ to matter little — tune it last, if at all.
  - Our 10-match windows are an implicit time decay; an exponential weight is a possible later variation, not
    for this round (keep the grid small, §2.3).

---

## 6. Betting statistics: how much evidence is enough

Per-bet return: win `o − 1` with probability `p`, lose 1 otherwise → SD = `sqrt(p(1−p))·o` [S].
At BETTORY's typical **odds 1.46, hit rate 72 %**: **SD ≈ 0.656 per unit staked**.

| Bets | SE of ROI | 95 % interval half-width |
|---|---|---|
| 218 (our validation run) | 4.4 pts | ±8.7 pts |
| 300 (Gate B minimum) | 3.8 pts | ±7.4 pts |
| 1,000 | 2.1 pts | ±4.1 pts |

| True ROI | Bets to show ROI > 0 (one-sided 95 %) | two-sided 95 % |
|---|---|---|
| 3 % | ~1,290 | ~1,840 |
| 5 % | ~470 | ~660 |
| 8 % | ~180 | ~260 |

- Our +3.1 % on 218 bets is **z = 0.70** — indistinguishable from zero.
- Practitioners note thousands of bets may not separate a ~4 % edge from noise [B]; **closing line value** is
  widely used as an earlier, less noisy signal, but I found no peer-reviewed test of "CLV predicts profit" [B].
- **Kelly staking**: growth is flat near the optimum, so fractional Kelly (e.g. ½) keeps most growth with much less
  variance; overestimating the edge is the main danger, and fractional Kelly does **not** save a bettor whose edge
  is not real [B]. Correlated bets (same matchday) call for smaller sizes [B]. → Matches the plan: flat 1 %
  stakes first, Kelly only after hundreds of confirmed live bets.
- **For Gate B (decision 2, still open)**: "ROI > 0 on ~300 bets" is too weak to mean anything. Better options:
  - (a) require the **calibration + paired Brier/log-loss improvement** (statistically supported) as the
    historical evidence, and treat ROI at the minimum odds as a consistency check;
  - (b) move the *profit* proof to paper betting with sequential e-values (§3.6), where real 15' prices exist.

---

## 7. Data sources — verified facts and open points

| Item | Finding | Status |
|---|---|---|
| football-data.co.uk odds timing | "Betting odds for weekend games are collected Friday afternoons, and on Tuesday afternoons for midweek games." Closing odds = "last odds before match starts", marked by a **C** after the bookmaker code | verified from a mirror of `notes.txt` [S] |
| Pinnacle columns | `PSH/PSD/PSA` (also `PH/PD/PA`) home/draw/away; `P>2.5`, `P<2.5`; closing `PSCH/PSCD/PSCA`, `PC>2.5`, `PC<2.5`; Asian handicap `PAHH/PAHA` | matches our parser; one Scribd page claiming `PSCH` = corners is **wrong** (corners are only stats `HC/AC`) |
| Date / time | `Date` dd/mm/yy in older files (our parser takes yy and yyyy); **time zone of `Time` not stated** in the notes | time zone still to confirm |
| football-data.co.uk TLS | fetching `notes.txt` from Anthropic's servers (not this network) failed: "unable to verify the first certificate" → the site likely sends an **incomplete certificate chain**; Python may fail even on a clean network | to diagnose with `openssl s_client`; fix = add the missing intermediate to the trust bundle, **never** `verify=False` → update [FOOTBALL-DATA-CO-UK-SETUP.md](FOOTBALL-DATA-CO-UK-SETUP.md) |
| API-Football per-minute limits | official article: Free 10, Pro 300, Ultra 450, Mega 900 per minute [B — page 403 to direct fetch, figures via search]; RapidAPI page shows conflicting numbers | set `API_FOOTBALL_REQUESTS_PER_MINUTE` from your dashboard after upgrading |
| API-Football daily reset | **00:00 UTC not confirmed** by a primary source | check the dashboard / `x-ratelimit` headers; our 00:30 UTC schedule assumes it |
| Pinnacle 15' markets | no evidence Pinnacle offers 0–15' totals (main, half, team totals only) [B] | the 15' price check must come from soft books' own main markets (Model C) |

---

## 8. Proposed design for the tuning tool (to agree before building)

1. **Pre-registration file** `config/tuning_v1.yaml` (committed before the run): the variation grid, the
   selection metric (log loss of P(Under 0.5) on the tuning period), the tuning/holdout split, the hypotheses
   (Grade B; P ≥ 76 %), and the Gate B criteria. The report quotes its commit hash.
2. **Variation grid (first proposal, ~24 runs):** `blend.team_weight` {1.0, 0.75, 0.5, 0.25} (only where Model C
   exists) × `model_a.rating_prior_matches.FT` {10, 20, 40} × `shrinkage.early_index_trust_w_I` {0.25, 0.5};
   EV threshold and grades are *decision* settings evaluated after the probability model is fixed.
3. **Runs:** every variation through the same `run_backtest` (point-in-time, sessions), tuning seasons only.
4. **Selection:** lowest tuning log loss; ties → simpler / closer to current params. All results tabulated.
5. **Holdout:** the selected params run **once** on 2025-26; current params v1.5 also run on 2025-26 for the
   paired comparison.
6. **New metrics in the report:** Brier decomposition (REL/RES/UNC), BSS vs competition-only, Spiegelhalter z,
   calibration slope/intercept, paired session-block bootstrap CIs (model vs baseline, chosen vs v1.5), ROI with
   its CI, per-season `s` and `r`, PBO-style warning when > ~10 variants.
7. **Hypothesis checks:** Grade B and P ≥ 76 % on the new seasons, with CIs, labelled confirmed / not confirmed.
8. **Output:** `config/params_v1.6.yaml` only if the chosen variation differs from 1.5 (never edit 1.5), and an
   updated BACKTEST-REPORT with the decision.
9. **Runtime:** the first backtest took minutes for 881 fixtures; ~24 variations × ~3,500 fixtures needs caching
   of point-in-time reads per session (baselines and windows do not depend on most tuned parameters).

## 9. Open questions for the owner

1. Decision 2 (Gate B profit criterion): option (a) or (b) of §6? Recommendation: **(a) + (b)**.
2. Agree the variation grid in §8.2 (or narrow it) — it must be fixed *before* the run.
3. Should Grade B stay in the product if the new seasons confirm it under-performs? (Removing it is a decision
   setting, not a model change, and can be evaluated without retuning probabilities.)

---

## 10. Sources

Backtesting and overfitting
- Bailey, Borwein, López de Prado, Zhu — The Probability of Backtest Overfitting [P]: https://papers.ssrn.com/abstract=2326253 · full text https://escholarship.org/content/qt4w1110bb/qt4w1110bb.pdf · R package `pbo`: https://archive.linux.duke.edu/cran/web/packages/pbo/readme/README.html
- Bailey, López de Prado — The Deflated Sharpe Ratio [P]: https://papers.ssrn.com/abstract=2460551 · https://en.wikipedia.org/wiki/Deflated_Sharpe_ratio
- Nested CV for time series [S]: https://temporalcv.readthedocs.io/en/latest/auto_examples/07_nested_cv_tuning.html · expanding windows https://arxiv.org/pdf/2601.06371
- Garden of forking paths / pre-registration [S]: https://forrt.org/glossary/english/garden_of_forking_paths/ · https://langcog.github.io/experimentology-dev/011-prereg.html
- Sports-model backtesting notes [B]: https://sports-models-kb.pages.dev/walk-forward-validation · https://sports-models-kb.pages.dev/overfitting-sports-models

Probability evaluation
- Brier score and decomposition [S/P]: https://en.wikipedia.org/wiki/Brier_score · https://pdf.arxiv.org/pdf/1303.6182 · Siegert (2017) https://zendy.io/title/10.1002/qj.2985
- Sequential e-values for forecast comparison [P]: https://arxiv.org/abs/2103.08402
- Paired bootstrap of Brier differences [P]: https://arxiv.org/pdf/2608.28482 · DM + block bootstrap https://arxiv.org/pdf/2609.01648 · bootstrap DM https://nbs.sk/_img/documents/publik/wp_5_2015_vavra_test_for_forecast_evaluations.pdf
- Calibration tests [P/B]: https://arxiv.org/html/2307.09713v4 · https://boris.unibe.ch/192373/1/HenziPukeETAL2023.pdf · Spiegelhalter z https://metricgate.com/docs/spiegelhalter-z-calibration-test/
- Platt vs isotonic [P]: https://arxiv.org/pdf/1207.1403 · https://arxiv.org/pdf/2509.23665
- RPS vs ignorance for football [P]: Constantinou & Fenton https://econpapers.repec.org/RePEc:bpj:jqsprt:v:8:y:2012:i:1:n:12 · Wheatcroft https://ar5iv.labs.arxiv.org/html/1908.08980 · https://eprints.lse.ac.uk/111494

Model vs market, betting markets
- Structural model vs closing price, Serie A [P]: https://arxiv.org/abs/2608.11505
- Benter 1994 [P]: https://www.Gwern.net/doc/statistics/decision/1994-benter.pdf
- Kaunitz, Zhong, Kreiner — Beating the bookies with their own numbers [P]: https://pdf.arxiv.org/pdf/1710.02824 · https://github.com/Lisandro79/BeatTheBookie
- Favourite–longshot bias and draws [P]: https://research.manchester.ac.uk/en/publications/biases-in-the-football-betting-market/ · https://econpapers.repec.org/RePEc:oup:oxecpp:v:78:y:2026:i:1:p:90-113. · https://www.karlwhelan.com/?p=2381
- De-vigging methods [P/B]: https://www.sciencepublishinggroup.com/article/10.11648/j.ajss.20170506.12 · https://penaltyblog.readthedocs.io/en/master/implied/index.html
- Do betting markets sense a goal coming? (Bundesliga) [P]: https://arxiv.org/abs/2505.21275
- ROI sample size [B]: https://comparenbet.pages.dev/guide-sample-size-significance · https://networked.substack.com/p/a-view-from-the-pinnacle
- Fractional Kelly [B]: https://harrycrane.substack.com/p/two-arguments-for-fractional-kelly

Goal timing
- Dixon & Robinson 1998 (summaries) [S]: https://soccermetrics.net/?p=598 · DOI 10.1111/1467-9884.00152
- Dixon & Coles 1997 correction and decay [S]: https://arxiv.org/pdf/2103.07272 · https://github.com/opisthokonta/goalmodel
- Goal-time distributions [P/S]: https://arxiv.org/pdf/1207.6796 · https://pmc.ncbi.nlm.nih.gov/articles/PMC6815073
- Baio & Blangiardo 2010 hierarchical model (over-shrinkage of extreme teams) [P]: https://discovery-pp.ucl.ac.uk/16040/1/16040.pdf
- Stoppage-time changes [B]: https://global.espn.com/football/story/_/id/38149110/premier-league-changes-explained-longer-games-ref-clampdown · https://www.goal.com/en-gb/lists/premier-league-games-less-stoppage-time-in-2024-25-referees-handed-new-instructions/blt77c6ab046d124d9f

Data sources
- football-data.co.uk notes (mirror) [S]: https://alexandrugris.github.io/assets/data_explanation.txt
- API-Football rate limits [B]: https://www.api-football.com/news/post/how-ratelimit-works · https://rapidapi.com/api-sports/api/api-football/pricing
