# BETTORY — Phase 0 Findings

**Verification of assumptions · started 2026-11-14**

Deliverable of [DEVELOPMENT-PLAN.md](DEVELOPMENT-PLAN.md) Phase 0. Raw responses are in `backend/phase0/` (not committed).

---

## 1. API-Football (free plan) — tested 2026-11-14

Tool: `backend/scripts/probe_api_football.py` + targeted checks. About 15 requests used.

### 1.1 Results

| # | Test | Result | Detail |
|---|---|---|---|
| T0 | Account | ℹ️ | Plan **Free**, 100 requests/day |
| T1 | Current season by league/season | ❌ **FAIL** | "Free plans do not have access to this season, try from 2022 to 2024." |
| T2 | Past seasons by league/season | ⚠️ Partial | 2024 ✅ (380 fixtures); 2025 ❌; only **2022–2024** allowed |
| T3 | Goal events with minutes | ✅ PASS | `/fixtures/events?fixture=` and `/fixtures?id=` work, also for current-season matches; each goal has `time.elapsed` + `time.extra` |
| T4 | Bulk 20 fixtures by `ids` | ❌ **FAIL** | "Free plans do not have access to the Ids parameter." → 1 request per match |
| T5 | Fixtures by date | ⚠️ Partial | Works (1,013 fixtures, 213 competitions on 2026-11-14, with HT and FT scores) but **only dates 2026-11-13 to 2026-11-15** (yesterday–tomorrow) |
| T6 | Lineups | ✅ PASS | |
| T7 | Odds | ✅ PASS (major finding) | 338 markets; **15-minute markets exist** (see 1.2); odds by date paginated, 10 fixtures/page (42 pages on 2026-11-15) |
| T8 | Coverage (Premier League) | ✅ | Events + lineups covered 2023–2026; odds covered for current season |

### 1.2 Markets relevant to BETTORY in the odds feed

| Market name | Use |
|---|---|
| **`1x2 - 15 minutes`** | 15' result → **DRAW** market odds directly |
| **`Goal in 1-15 minutes`** | 15' goal / no goal → **UNDER 0.5** market odds directly |
| `DC - 15 minutes`, `Double Chance 0-15m` | Related 15' markets |
| **`Goals Over/Under First Half`** | Model C (HT totals) — 9 bookmakers on the sample page |
| `Goals Over/Under` | Model C (FT totals) |
| `First 10 min Winner`, `1x2 - 20/30 minutes`, `Goal in 16-30 minutes`… | Other intervals (future use) |

Example (fixture 1492312, Marathonbet, `1x2 - 15 minutes`): Home 10.50 · **Draw 1.36** · Away 3.65.
Bookmaker coverage for 15' markets on the sample page was thin (1 bookmaker) — to be measured across a full day.

### 1.3 Verdict

| Question | Answer |
|---|---|
| Does API-Football have the data BETTORY needs? | ✅ **Yes** — fixtures, HT/FT, goal minutes, lineups, and even **15-minute market odds** |
| Is the **free plan** enough to be the main source? | ❌ **No** — no current/last season history, no bulk `ids`, dates limited to ±1 day |

**Free-plan arithmetic:** goal minutes cost 1 request per match. Building the last-10 general/home/away history for one league season (~380 matches) would take ~4 days of the whole quota, and current/last season history is blocked entirely.

### 1.4 Recommendation

| Stage | Plan |
|---|---|
| Development (Phases 1–2, early 3) | **Free plan** + openfootball + football-data.co.uk; use 2022–2024 seasons and ±1-day dates for parser development |
| Back-fill and live operation (Phase 3 onward) | **API-Football paid plan** (Pro tier: 7,500 requests/day, all seasons, bulk `ids`) — confirm current price on the pricing page before buying |

With a paid plan the daily budget is comfortable: fixtures by date (1–2), finished matches in bulk (≈5–10), odds pages for the day (≈40–90), lineups (≈30–60), plus history back-fill (≈20 per league season).

### 1.5 Design impact

- **Model C and EV can be automatic** from API-Football odds — manual odds entry becomes a fallback.
- The report can compare directly against **real 15' odds** where a bookmaker offers them.
- Odds records must store **bookmaker name**; 15' coverage per bookmaker must be measured (some bookmakers may not offer it).

---

## 2. football-data.org (free tier) — tested 2026-11-14

Tool: `backend/scripts/probe_football_data_org.py`. 8 requests used.

### 2.1 Results

| # | Test | Result | Detail |
|---|---|---|---|
| F1 | Competitions | ✅ PASS | **13**: BSA, ELC, PL, CL, EC, FL1, BL1, SA, DED, PPL, CLI, PD, WC |
| F2 | Current season | ✅ | Premier League 2026-08-21 → 2027-05-30, matchday 11 |
| F3 | Season access | ✅ PASS | **2026 (current), 2025, 2024, 2023** all accessible; 380 matches each; list includes **HT and FT scores** |
| F4 | Goals with minutes | ❌ **FAIL** | Match detail has **no `goals` field** on the free tier (fields: score, homeTeam/awayTeam, referees, odds) |
| F4b | Odds | ❌ | `"Activate Odds-Package in User-Panel to retrieve odds."` (paid add-on) |
| F5 | Matches by date range | ✅ | Works (0 matches yesterday–today for these competitions on 2026-11-14) |
| F6 | Rate limit | ℹ️ | 10 requests/minute (headers `x-requests-available-minute`, `x-requestcounter-reset`) |

### 2.2 Verdict

| Question | Answer |
|---|---|
| Goal minutes (15' data)? | ❌ Not on the free tier |
| Odds? | ❌ Paid add-on |
| Current + recent seasons with HT/FT scores? | ✅ **Yes — for 13 competitions, which API-Football's free plan cannot give** |

### 2.3 Role in BETTORY

- **Cross-check source** for HT/FT scores and fixtures (13 competitions).
- **During development:** the only free source of **current and last season HT/FT scores** for the top leagues → usable for **Model L** (HT-based) and competition baselines (avg HT/FT goals) while API-Football stays on the free plan.
- **Not** a source of goal minutes or odds.

---

## 3. openfootball (football.json on GitHub) — tested 2026-11-14

Tool: `backend/scripts/probe_openfootball.py` + validation checks. No key; public files.

### 3.1 Inventory

- 292 JSON files, seasons **2010-11 → 2026-27**.
- 2025-26: 23 files (England 1–4, Germany 1–2, Spain 1–2, France 1–2, Italy 1–2, Netherlands, Portugal, Belgium, Austria 1–2, Scotland, Greece, Turkey).
- Calendar-year leagues 2025: Argentina, Brazil 1–2, China, Colombia, Japan, MLS, Copa Libertadores.
- Current 2026-27: 8 leagues (England 1–2, Germany, Spain, France, Italy, Netherlands, Portugal) — in progress.

### 3.2 Two file types

| File | Content |
|---|---|
| Standard `xx.1.json` | date, time, round, team1, team2, **HT and FT scores** — no goal minutes |
| **`xx.1-full.json`** | Everything above **plus goals with minutes** (`goals1`/`goals2`: name, minute, `penalty`, `owngoal`), lineups, bookings, referees, ground, attendance |

`-full` files exist only for **2025-26: England (en.1), Germany (de.1), Austria (at.1)**.

### 3.3 Data quality check (`-full` files, 2025-26)

| League | Goals listed = FT score | Goals ≤ 45' = HT score | Minute format valid | Matches with a goal ≤ 15' |
|---|---|---|---|---|
| England en.1 | 380 / 380 ✅ | 380 / 380 ✅ | 1,045 / 1,045 ✅ | 107 / 380 (28.2%) |
| Germany de.1 | 306 / 306 ✅ | 306 / 306 ✅ | 990 / 990 ✅ | 93 / 306 (30.4%) |
| Austria at.1 | 195 / 195 ✅ | 195 / 195 ✅ | 527 / 527 ✅ | 62 / 195 (31.8%) |

The early-goal rates imply P(no goal before 15') ≈ 68–72%, consistent with the model baselines (~73%).

### 3.4 Parsing rules discovered

| Finding | Rule |
|---|---|
| Minutes are strings: `"37"`, `"90+4"`, `"45+2"` | Parse into `minute` + `extra_minute` |
| Own goals flagged `owngoal: true`, listed under the **benefiting** team | Count for that team (matches bookmaker rule) |
| Some scores are a plain list `[0, 0]` (no `ht`) | FT 0-0 written short; HT = 0-0 implied |
| Team names differ between files of the same source (`"Liverpool FC"` vs `"Liverpool"`) | ID mapping is needed **within** a source, not only between sources |
| `time` field is local kick-off time, no timezone | Convert with the competition's timezone (to confirm per league) |

### 3.5 Verdict and role

| Question | Answer |
|---|---|
| Goal minutes? | ✅ **Yes, high quality** — but only 3 leagues, one season (2025-26) |
| HT/FT history? | ✅ Many leagues, 2010-11 onwards (no goal minutes) |
| Current season? | ⚠️ 8 leagues, HT/FT only, updated by volunteers (timing not guaranteed) |

**Role in BETTORY:**
- **Gold-standard test data**: 881 fully verified matches with goal minutes → parser tests, 15' recovery tests, first calibration of early-goal shares.
- **HT/FT history** for Model L and competition baselines (many leagues, 15+ seasons).
- **Not** a live source (volunteer-maintained, few `-full` leagues).

---

## 4. football-data.co.uk (CSV files) — ⛔ blocked on the development network, 2026-11-14

Tool: `backend/scripts/probe_football_data_co_uk.py` (ready; checks HT columns, Pinnacle closing odds, extra-league format, fixtures file, and cross-checks HT/FT scores against openfootball).

### 4.1 What happened

| Check | Result |
|---|---|
| Python (certifi CA bundle) | ❌ `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate` |
| curl (Git Bash) | ❌ failed |
| Windows certificate store (PowerShell) | ❌ "Could not establish trust relationship" |
| Certificate presented for `*.football-data.co.uk` | Issued by **`Sophos SSL CA_C330ACGVB42RVAE`** (O=Sophos) — **not** a public certificate authority |
| Sophos root CA in Windows trust store | Not present |

**Cause:** a **Sophos firewall on the network** is intercepting HTTPS to this site (TLS inspection — likely because the site is categorised as gambling) and re-signing it with its own certificate, which this PC does not trust. The other three sources are not intercepted.

**Decision:** certificate verification is **not disabled** — downloads must be verifiably genuine (strictness principle).

### 4.2 Options

| Option | Effort | Note |
|---|---|---|
| Run the probe on another network (e.g. phone hotspot) | Minutes | Quickest way to complete V5 |
| Exclude `football-data.co.uk` from Sophos HTTPS inspection | Network admin | Fixes it permanently for this network |
| Run football-data.co.uk ingestion only on the cloud backend | None | Production host is not behind this firewall; local development uses saved copies |

---

## 5. Still to verify

| # | Item | Status |
|---|---|---|
| V3 | football-data.org free tier `goals[]` | ✅ Checked — **not available** on free tier (§2) |
| V4 | openfootball field names | ✅ Checked (§3) |
| V5 | football-data.co.uk columns (HT, Pinnacle closing O/U 2.5) | ⛔ Blocked by network TLS interception (§4) |
| V6 | Your bookmaker's 15' settlement rule | ⬜ (you) |
| V8 | Leagues where your bookmaker offers 15' markets | ⬜ (you) |
| V10 | Goal-minute labels vs live text ("15'" = 14:00–14:59?) | ⬜ |
| — | 15' odds coverage across all bookmakers for one full day | ⬜ |
