# BETTORY — Data Health

> **Lean-free pass (2026-11-25):** parts of this document describe machinery that was removed or replaced.
> Read [LEAN-FREE.md](LEAN-FREE.md) for the delta before relying on endpoint or screen lists here.

> The checks that run by themselves after every data load, the gate that keeps untrusted data away from bets,
> and where to see the metrics. Settings: [config/data_quality.yaml](../backend/config/data_quality.yaml).
> Built 2026-11-16 (branch `data-pipeline`).

**Principle:** the system detects, measures, blocks and reports. It never changes model parameters or Gate B
decisions by itself, and it never guesses — a judgement call is shown to the owner as a finding.

---

## 1. What happens after every ingestion run

```
run ends (successfully or not)
  ├─ every competition-season the run wrote to is cross-checked  → core.crosscheck_results
  └─ the run is audited (expected vs delivered)                    → raw.ingestion_runs.audit_status / audit
```

API-Football runs additionally **plan themselves inside the day's quota** before the first request
(`/status`, which API-Football does not count): budget = min(requested, limit − used − `api_quota.reserve_requests`).
No request left → the run fails visibly (`QuotaError`) without using any request.

---

## 2. Cross-checks and the bet gate

For every match with two or more sources (DEVELOPMENT-PLAN 3.6):

| Value | Compared |
|---|---|
| FT | full-time scores of all sources |
| HT | half-time scores of all sources |
| 15' | the 15' score derived from each goal-minute source (needs **two** goal-minute sources) |
| 15' vs HT | each goal-minute source's 15' score must fit every other source's HT score |

Judging (config `crosscheck.*`):

| Status | When |
|---|---|
| `PASS` | every value compared on ≥ `min_compared` matches agrees at ≥ `min_agreement` (98 %) |
| `FAIL` | at least one judged value is below `min_agreement` |
| `NOT_MEASURABLE` | no value compared on enough matches (e.g. a single source) |

`fifteen_verified` = 15' results were compared between two goal-minute sources and passed (the Phase 3 exit criterion).

**Gate (decision layer L1, check `SOURCE_AGREEMENT`):** a fixture cannot qualify when the **latest** cross-check
of its competition-season, or of any older season pooled into its baseline, is an unaccepted `FAIL` → verdict
`INSUFFICIENT_DATA` with the reason. Single disagreeing matches are already excluded (`CONFLICT`); a failing
season means the sources disagree often enough that its single-source matches are not trusted either.

**Owner review:** look at the disagreements, then either fix the data or accept:
`python -m app.cli crosscheck-accept <id> --note "what was reviewed"` (or `POST /data-health/crosschecks/{id}/accept`).
Only the latest check counts: new data that fails again blocks again.

The gate reads the **current** check, not the one valid at `as_of` (also in the backtest): like a match's
CONFLICT status, it describes whether stored data can be trusted, not anything that happened in a match.

Limit: team-history matches from **other** competitions (cups) are not gated by their own season's check.

---

## 3. Run audit

| Level | Code | Meaning |
|---|---|---|
| ERROR | `RUN_FAILED` | the run failed (error text) |
| WARNING | `NO_PAYLOADS` | nothing was fetched |
| WARNING | `SOURCE_CONFLICTS` | matches where sources disagree (excluded until resolved) |
| WARNING | `TEAMS_TO_REVIEW` | team names sent to review / records waiting for them |
| WARNING / INFO | `SKIPPED_RECORDS` | skipped records; WARNING above `run_audit.max_skipped_share` |
| WARNING | `MINUTES_MISSING` | goal minutes requested for N fixtures, delivered for fewer |
| WARNING | `CROSSCHECK_FAILED` / `CROSSCHECK_ERROR` | a touched season failed its check / the check could not run |
| INFO | `BUDGET_REACHED` | request budget reached: run again (resumes) |
| INFO | `RATE_LIMIT_RETRIES` | requests retried after the per-minute limit |
| INFO | `DATA_ISSUES` | data issues recorded per match |
| INFO | `QUOTA_LOW` | API requests left today ≤ the reserve |

Run status: `ERROR` if any error, `WARNING` if any warning, otherwise `OK`.

---

## 4. Seeing the metrics

### Command line

```bash
python -m app.cli health --runs 10 --quota      # coverage, blocking checks, runs + findings, open items, quota
python -m app.cli health --json                  # the same as JSON
python -m app.cli crosscheck --comp ENG1 --season 2025-26 --show 10
python -m app.cli crosscheck-accept <id> --note "..."
```

### API (owner only)

| Endpoint | Shows |
|---|---|
| `GET /data-health` | everything at once |
| `GET /data-health/coverage` | per competition-season: finished, usable 15' (exact / HT 0-0), unknown, conflicts, sources with goal minutes, current check |
| `GET /data-health/crosschecks` / `{id}` | current checks; one check with its disagreements |
| `POST /data-health/crosschecks` | run a check now `{competition, season}` |
| `POST /data-health/crosschecks/{id}/accept` | accept after review `{note}` |
| `GET /data-health/runs` | latest runs with audit findings and statistics |
| `GET /data-health/quota` | API-Football requests used today |
| `GET /data-health/backtests` / `{name}` | stored backtests: decisions, calibration, models, ROI, Gate B |

### First real snapshot (2026-11-16)

| Competition | Season | Finished | Usable 15' | Unknown | Check |
|---|---|---|---|---|---|
| Austria 1 | 2025-26 | 195 | 195 (100 %) | 0 | NOT_MEASURABLE (one source) |
| Premier League | 2024-25 | 380 | 160 (42 %) | 220 | PASS (FT/HT 380/380) |
| Premier League | 2025-26 | 380 | 380 (100 %) | 0 | PASS |
| Bundesliga | 2025-26 | 306 | 306 (100 %) | 0 | PASS |

`fifteen_verified` is "no" everywhere: no season has two goal-minute sources yet (openfootball has minutes only
for 2025-26, API-Football only for the 2024-25 backfill so far).

---

## 5. Backfill order

The API-Football backfill fetches fixtures whose 15' result is **unknown** first; fixtures already known
(half-time 0-0, or minutes from another source) come last — for them a request only adds a second source for
the cross-check. On the free plan this makes about 23 % more requests count.
