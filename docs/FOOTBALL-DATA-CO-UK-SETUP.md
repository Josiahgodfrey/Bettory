# football-data.co.uk — Pending Setup (do this on another network)

> **Status (2026-11-16): code built, never run on real data.** The development network blocks the site, so this source
> is parked. This document is the complete checklist for finishing it on a network that does not intercept HTTPS.

---

## 1. Why this source matters

football-data.co.uk is the only **free** source of historical **main-market odds** (Pinnacle), and the backtest needs them:

| Without these odds | With them |
|---|---|
| Model C (market-implied price) unavailable in every backtested fixture | Model C active: team and market prices blended (formula v1.4) |
| EV threshold 8 % (no market check) | 5 % with the market check, and minimum odds ≥ Model C fair odds |
| Gate B ROI criterion not evaluable ([BACKTEST-REPORT.md](BACKTEST-REPORT.md) §2.2) | Estimated-value analysis possible |
| No "is BETTORY closer to the outcome than the market?" check (pricing doc 15) | Possible |

It also gives a third independent source of FT and HT scores for the cross-check.

It does **not** provide goal minutes: those still come from openfootball / API-Football.

---

## 2. What already exists

| Piece | File | State |
|---|---|---|
| Download client | [ingestion/football_data_co_uk.py](../backend/app/ingestion/football_data_co_uk.py) | `/mmz4281/{season}/{division}.csv`, 1 request per second, certificate checks **on** |
| CSV parser | [normalisation/parsers/football_data_co_uk.py](../backend/app/normalisation/parsers/football_data_co_uk.py) | FT/HT scores, kick-off (UK time → UTC), Pinnacle **closing** odds 1X2 (`PSCH/PSCD/PSCA`) and O/U 2.5 (`PC>2.5`, `PC<2.5`) |
| Test file | `backend/tests/fixtures/football_data_co_uk_E0_2526_SYNTHETIC.csv` | Hand-made from the documented layout — **not a real file** |
| Pipeline job | `pipeline.football_data_co_uk` | Raw file stored, parsed, matched to teams, loaded, reconciled |
| CLI | `python -m app.cli ingest football-data-co-uk --comp ENG1 --season 2025-26` | |
| API | `POST /jobs/ingest/football-data-co-uk` with `{competition, season, reparse}` | |
| League mapping | [config/competitions.yaml](../backend/config/competitions.yaml) | 8 of 9 leagues (below) |

| Competition | Division file |
|---|---|
| ENG1 Premier League | `E0` |
| ENG2 Championship | `E1` |
| GER1 Bundesliga | `D1` |
| ESP1 La Liga | `SP1` |
| ITA1 Serie A | `I1` |
| FRA1 Ligue 1 | `F1` |
| NED1 Eredivisie | `N1` |
| POR1 Primeira Liga | `P1` |
| AUT1 Austrian Bundesliga | **none** — not in the main files (see §5.5) |

Season folder: `2025-26` → `2526`, `2024-25` → `2425` (`fdcouk_season_code` in [registry.py](../backend/app/domain/registry.py)).

---

## 3. The network block (why it is parked)

Found in Phase 0 ([PHASE-0-FINDINGS.md](PHASE-0-FINDINGS.md) §4, 2026-11-14) and still true on 2026-11-16:

- The development network runs **Sophos TLS interception**: the certificate presented for `*.football-data.co.uk` is
  issued by `Sophos SSL CA_...`, not a public certificate authority. Certificate verification fails, and the site
  does not open in the browser either.
- **Certificate verification must stay ON.** Turning it off would hide the interception, and the same setting would
  then expose API keys and database traffic to anything in the middle. This is a project rule, not a workaround to
  try.

---

## 4. A design problem to fix at the same time: closing odds are invisible to the backtest

The parser stores only **closing** odds, stamped `captured_at = kick-off` (correct: they exist only at kick-off).
The backtest analyses each session at its report time — hours **before** kick-off — and the point-in-time reader
returns only odds captured **before** that moment. So even after loading, **the backtest would never see these odds**
and Model C would stay unavailable.

This is the no-look-ahead rule working as intended: live, closing odds are never known when the report is made
(they already contain late news such as line-ups). It means the data doc's plan to use **closing** odds
([DATA-SOURCES-AND-MARKET-MODEL.md](DATA-SOURCES-AND-MARKET-MODEL.md) §9) conflicts with point-in-time correctness.

**Planned fix:** also read Pinnacle's **pre-match** odds, which the files carry in separate columns, as
`snapshot_type = ANALYSIS` with a conservative `captured_at`:

| Columns (expected — verify, §5.2) | Market |
|---|---|
| `PSH`, `PSD`, `PSA` | FT 1X2 |
| `P>2.5`, `P<2.5` | FT O/U 2.5 |

`captured_at` must be **no earlier than when the odds were really collected**; a later time only makes the odds
invisible for some sessions, an earlier one would be look-ahead. From memory, `notes.txt` says pre-match odds are
collected on **Friday afternoons for weekend matches and Tuesday afternoons for midweek matches** — **unverified**:
the real `notes.txt` decides the rule. If the collection time cannot be pinned down, the fallback rule is
"captured_at = the day before kick-off, 23:59 UK time", **only** if the notes guarantee collection before that.

Closing odds stay loaded as `CLOSING` (for the closing-line-value and "BETTORY vs market" comparisons), never as
analysis input.

---

## 5. Checklist on a network that does not intercept HTTPS

### 5.1 Confirm the network is clean (2 minutes)

```bash
curl -sSI https://www.football-data.co.uk/notes.txt        # must succeed WITHOUT -k / --insecure
```

and/or open the site in a browser and check the certificate issuer is a public authority (not Sophos or another
company proxy). If it fails, stop: the network intercepts TLS too.

### 5.2 Get the real files and check the layout

Download into a new, empty folder (not inside `backend/`), e.g. `C:\bettory-data\football-data-co-uk\`:

| File | URL |
|---|---|
| Notes (column meanings, odds collection times, time zone) | `https://www.football-data.co.uk/notes.txt` |
| Pilot leagues, 4 seasons | `https://www.football-data.co.uk/mmz4281/{2223,2324,2425,2526}/{E0,D1}.csv` |
| Other leagues (later) | same pattern with `E1, SP1, I1, F1, N1, P1` |

Check, and correct the parser if anything differs:

- [ ] Required columns present: `Div, Date, HomeTeam, AwayTeam, FTHG, FTAG`; optional `Time, HTHG, HTAG`.
- [ ] Date format `dd/mm/yyyy` (older files `dd/mm/yy` — both handled).
- [ ] `Time` is **UK local time** (the parser assumes `Europe/London`) — confirm in `notes.txt`.
- [ ] Closing odds column names: `PSCH, PSCD, PSCA, PC>2.5, PC<2.5`.
- [ ] Pre-match odds column names: `PSH, PSD, PSA, P>2.5, P<2.5` and **when** they are collected.
- [ ] File encoding (UTF-8 with or without BOM; the parser decodes `utf-8-sig` strictly).
- [ ] Replace the synthetic test file with a short slice of a real file (a few rows) as the parser test fixture.

### 5.3 Code changes (small, planned)

1. **Parser:** read the pre-match columns as `ANALYSIS` odds with the verified `captured_at` rule (§4). Tests: the
   timestamp rule, missing columns, and a point-in-time test that a session's analysis sees pre-match odds but never
   closing odds.
2. **Import from file:** `python -m app.cli ingest football-data-co-uk --comp ENG1 --season 2025-26 --file <path>`
   (same job; the file is stored in the raw store like a download, so everything stays reproducible). Lets the files
   be downloaded anywhere and loaded on any machine.
3. **Docs:** update [DATA-INGESTION-IMPLEMENTATION.md](DATA-INGESTION-IMPLEMENTATION.md) §9 and this document.

### 5.4 Load, check, rerun

```bash
# per competition and season (download directly, or --file once 5.3.2 exists)
python -m app.cli ingest football-data-co-uk --comp ENG1 --season 2025-26
python -m app.cli review list                       # new team spellings -> resolve, never auto-create
python -m app.cli crosscheck --comp ENG1 --season 2025-26
python -m app.cli backtest --from 2025-08-01 --to 2026-05-31 --comp ENG1 --comp GER1 --comp AUT1 --split 2026-02-01
```

Expect the first load to send several team names to the review queue (football-data.co.uk uses short names such as
"Man United", "Nott'm Forest"); resolve them like the 7 Bundesliga names in Phase 3.

### 5.5 Austria (AUT1)

Austria is not in the main `mmz4281` files. football-data.co.uk has an "extra leagues" file for it with a
**different layout** (expected: `https://www.football-data.co.uk/new/AUT.csv`, one file for all seasons, no half-time
scores) — **verify on the site**. It needs its own small parser; until then AUT1 runs without Model C.

---

## 6. Done when

- [ ] The real column layout and `notes.txt` are checked and the parser matches them.
- [ ] Pre-match odds are loaded with a verified, conservative `captured_at`; a test proves closing odds never reach an analysis.
- [ ] ENG1 and GER1 2022-23 to 2025-26 loaded; review queue empty; cross-check FT/HT agreement ≥ 98 % with the other sources.
- [ ] Backtest rerun: Model C available on most priced fixtures; "Models" table includes Model C; [BACKTEST-REPORT.md](BACKTEST-REPORT.md) updated.

---

## 7. Alternatives if no clean network is available

| Option | Note |
|---|---|
| Run the download on the backend server (cloud host) once it exists | Not behind the office proxy; the `--file` option is then not even needed |
| Download on another network (home, mobile hotspot) and bring the files over | Needs the `--file` option (§5.3.2) |
| API-Football odds | Only for current fixtures; historical depth unverified — not a substitute for the backtest |
| Ask the network administrator to exempt `www.football-data.co.uk` from TLS inspection | Cleanest for this machine |
