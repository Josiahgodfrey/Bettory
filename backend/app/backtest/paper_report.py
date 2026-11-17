"""Paper betting report: the forward test of Gate B (docs/PAPER-BETTING.md §7).

Reads only sealed predictions and their latest settlements, and judges them with the SAME rules as the backtest
(app/backtest/report.py: calibration_criterion, baseline_criterion; config/backtest.yaml):

    coverage      frozen / settled / pending, sessions, the hash chain
    forecasts     Under 0.5 and Draw: n, mean P, actual, Brier, log loss, calibration buckets (all priced, settled)
    baseline      paired Brier vs the competition-only price, sessions resampled
    bets          the sealed paper bets: ROI with a session-bootstrap interval, hit rate vs break-even
    evidence      sequential e-values (app/settlement/evalues.py), valid at every look:
                    profit    the bets at their sealed odds         H0: no edge
                    forecast  BETTORY vs the bookmakers' de-vigged 15' price (Under 0.5, all priced predictions
                              with a complete sealed market)       H0: the market is right
    Gate B        qualified selections, calibration, baseline, and profit = the profit e-value reaching 1/alpha
                  (alpha in config/backtest.yaml paper_betting; at least min_bets settled bets)
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from app.backtest.config import BacktestConfig
from app.backtest.report import (
    FAIL,
    NOT_EVALUABLE,
    PASS,
    Criterion,
    Paired,
    baseline_criterion,
    calibration_criterion,
)
from app.calculation.devig import devig
from app.calculation.ev import Bet
from app.domain.params import Params
from app.settlement.clv import closing_line, clv_fair
from app.settlement.evalues import EProcess, e_process, forecast_ratio, profit_ratio
from app.settlement.performance import Bucket, Pair, Score, calibration, score
from app.settlement.statistics import Interval, block_bootstrap
from app.storage.repositories.paper import ChainStatus, ReportRow

IN_PROGRESS = "IN PROGRESS"


@dataclass(frozen=True)
class BetSummary:
    bets: int  # settled WIN / LOSE
    wins: int
    voids: int
    pending: int
    staked: float
    profit: float
    mean_odds: float | None
    break_even: float | None  # mean 1 / odds: the hit rate needed to break even
    roi_interval: Interval | None
    clv_n: int = 0  # settled bets with a usable closing line
    clv_mean: float | None = None  # mean of taken odds x fair closing P - 1 (app/settlement/clv.py)
    clv_beat: float | None = None  # share of those bets that beat the fair closing price

    @property
    def roi(self) -> float | None:
        return self.profit / self.staked if self.staked else None

    @property
    def hit_rate(self) -> float | None:
        return self.wins / self.bets if self.bets else None


@dataclass(frozen=True)
class PaperSummary:
    params_version: str | None
    chain: ChainStatus
    frozen: int
    sessions: int
    settled: int
    pending: int
    corrections: int
    listed: int
    forecasts: dict[str, tuple[Score | None, list[Bucket]]]  # market -> all priced, settled
    paired: Paired | None
    bets: BetSummary
    market_gap_pts: float | None  # mean (BETTORY - market) P(Under 0.5), points
    e_profit: EProcess
    e_forecast: EProcess
    alpha: float
    gate: list[Criterion]


def _outcome(r: ReportRow, bet: Bet) -> bool | None:
    return r.under05 if bet is Bet.UNDER05 else r.draw


def _pairs(rows: Sequence[ReportRow], p: Callable[[ReportRow], float | None], bet: Bet) -> list[Pair]:
    return [(pv, y) for r in rows if (pv := p(r)) is not None and (y := _outcome(r, bet)) is not None]


def market_p_under05(r: ReportRow, params: Params) -> float | None:
    """Mean de-vigged P(no goal before 15') over the bookmakers with a complete sealed `Goal in 1-15 minutes`."""
    books: dict[str, dict[str, float]] = defaultdict(dict)
    for o in r.odds_seen:
        if o["market"] == "FIRST15_GOAL":
            books[o["bookmaker"]][o["selection"]] = float(o["odds"])
    v = params.value
    ps = []
    for prices in books.values():
        if set(prices) != {"NO", "YES"}:
            continue
        try:
            d = devig((prices["NO"], prices["YES"]), v.devig_method, min_overround=v.overround_min,
                      max_overround=v.overround_max)
        except ValueError:  # implausible margin: not a usable market
            continue
        ps.append(d.probabilities[0])
    return sum(ps) / len(ps) if ps else None


def _paired(rows: Sequence[ReportRow], cfg: BacktestConfig) -> Paired | None:
    blocks: dict[str, list[tuple[float, float]]] = defaultdict(list)
    n = 0
    for r in rows:
        y = r.under05
        if r.p_under05 is None or r.p_league_00 is None or y is None:
            continue
        n += 1
        blocks[r.session_id].append(((r.p_under05 - y) ** 2 - (r.p_league_00 - y) ** 2, 1.0))
    if not n:
        return None
    u = cfg.uncertainty
    return Paired("Final price - Competition only", "UNDER05", n,
                  block_bootstrap(list(blocks.values()), u.bootstrap_samples, u.confidence, u.seed), None)


def _clv(rows: Sequence[ReportRow], params: Params) -> list[float]:
    out = []
    for r in rows:
        assert r.bet_market is not None and r.bet_odds is not None
        line = closing_line(r.closing, Bet(r.bet_market), r.bet_book, params)
        if line is not None:
            out.append(clv_fair(r.bet_odds, line))
    return out


def _bets(rows: Sequence[ReportRow], cfg: BacktestConfig, params: Params) -> BetSummary:
    with_bet = [r for r in rows if r.bet_market is not None]
    done = [r for r in with_bet if r.bet_outcome in ("WIN", "LOSE")]
    blocks: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for r in done:
        assert r.profit is not None and r.bet_stake is not None
        blocks[r.session_id].append((r.profit, r.bet_stake))
    u = cfg.uncertainty
    odds = [r.bet_odds for r in done if r.bet_odds is not None]
    return BetSummary(
        bets=len(done), wins=sum(r.bet_outcome == "WIN" for r in done),
        voids=sum(r.bet_outcome == "VOID" for r in with_bet), pending=sum(not r.settled for r in with_bet),
        staked=sum(r.bet_stake or 0.0 for r in done), profit=sum(r.profit or 0.0 for r in done),
        mean_odds=sum(odds) / len(odds) if odds else None,
        break_even=sum(1 / o for o in odds) / len(odds) if odds else None,
        roi_interval=block_bootstrap(list(blocks.values()), u.bootstrap_samples, u.confidence, u.seed),
        clv_n=len(clv := _clv(done, params)), clv_mean=sum(clv) / len(clv) if clv else None,
        clv_beat=sum(c > 0 for c in clv) / len(clv) if clv else None)


def _profit_criterion(b: BetSummary, e: EProcess, cfg: BacktestConfig) -> Criterion:
    pb = cfg.paper_betting
    bar = 1.0 / pb.alpha
    detail = (f"e-value now {e.value:.2f}, peak {e.peak:.2f} (bar {bar:g} = 1/alpha, alpha {pb.alpha:g}); "
              f"{b.bets} settled bets, ROI {'—' if b.roi is None else f'{b.roi:+.1%}'}")
    if b.bets < pb.min_bets:
        return Criterion("Profit (paper betting)", NOT_EVALUABLE, f"{detail}; needs >= {pb.min_bets} settled bets")
    if e.reached(pb.alpha):
        return Criterion("Profit (paper betting)", PASS, detail)
    return Criterion("Profit (paper betting)", IN_PROGRESS, f"{detail}; continues until the bar is reached")


def summarise_paper(rows: Sequence[ReportRow], chain: ChainStatus, cfg: BacktestConfig, params: Params,
                    params_version: str | None = None) -> PaperSummary:
    settled = [r for r in rows if r.settled]
    priced = [r for r in settled if r.p_under05 is not None]
    listed = [r for r in settled if r.decision == "QUALIFIED"]
    width = cfg.calibration.bucket_width_pts
    forecasts: dict[str, tuple[Score | None, list[Bucket]]] = {}
    for bet, p in ((Bet.UNDER05, lambda r: r.p_under05), (Bet.DRAW, lambda r: r.p_draw)):
        pairs = _pairs(priced, p, bet)
        forecasts[bet.value] = (score(pairs), calibration(pairs, width))

    bets = _bets(rows, cfg, params)
    ordered = sorted((r for r in rows if r.bet_outcome in ("WIN", "LOSE") and r.bet_odds is not None),
                     key=lambda r: (r.kickoff_utc, r.chain_pos))
    e_profit = e_process([profit_ratio(r.bet_odds or 0.0, r.bet_outcome == "WIN") for r in ordered])

    gaps: list[float] = []
    ratios: list[float] = []
    for r in sorted(priced, key=lambda r: (r.kickoff_utc, r.chain_pos)):
        m = market_p_under05(r, params)
        if m is None or r.p_under05 is None or r.under05 is None or not 0 < r.p_under05 < 1:
            continue
        gaps.append((r.p_under05 - m) * 100)
        ratios.append(forecast_ratio(r.p_under05, m, r.under05))
    e_forecast = e_process(ratios)

    paired = _paired(priced, cfg)
    need = cfg.gate_b.min_qualified
    known = sum(r.under05 is not None for r in listed)
    gate = [Criterion("Qualified selections", PASS if known >= need else NOT_EVALUABLE,
                      f"{known} settled listed predictions (need >= {need})"),
            calibration_criterion(forecasts[Bet.UNDER05.value][1], cfg.calibration),
            baseline_criterion(paired, cfg.uncertainty),
            _profit_criterion(bets, e_profit, cfg)]
    if not chain.intact:
        gate.insert(0, Criterion("Record integrity", FAIL, f"hash chain broken at {len(chain.problems)} row(s)"))
    return PaperSummary(
        params_version=params_version, chain=chain, frozen=len(rows), sessions=len({r.session_id for r in rows}),
        settled=len(settled), pending=len(rows) - len(settled), corrections=sum(r.corrections for r in rows),
        listed=len(listed), forecasts=forecasts, paired=paired, bets=bets,
        market_gap_pts=sum(gaps) / len(gaps) if gaps else None, e_profit=e_profit, e_forecast=e_forecast,
        alpha=cfg.paper_betting.alpha, gate=gate)


# --- output ----------------------------------------------------------------------------------------------------


def _pct(x: float | None, fmt: str = ".1%") -> str:
    return "—" if x is None else format(x, fmt)


def _ci(i: Interval | None, fmt: str) -> str:
    return "—" if i is None else f"{format(i.estimate, fmt)} [{format(i.low, fmt)}, {format(i.high, fmt)}] " \
                                 f"({i.blocks} sessions)"


def render_paper(s: PaperSummary, cfg: BacktestConfig) -> str:
    bar = 1.0 / s.alpha
    out = ["# Paper betting report", "",
           f"Params {s.params_version or 'all versions'} · {s.frozen} frozen predictions in {s.sessions} sessions · "
           f"{s.settled} settled, {s.pending} pending · {s.corrections} corrected result(s).",
           f"Record: hash chain **{'INTACT' if s.chain.intact else 'BROKEN'}**, {s.chain.rows} rows"
           + (f", head #{s.chain.head_pos} `{s.chain.head_hash}`" if s.chain.head_hash else "") + ".",
           "", "## Gate B (forward test)", "", "| Criterion | Status | Detail |", "|---|---|---|"]
    out += [f"| {c.name} | **{c.status}** | {c.detail} |" for c in s.gate]

    out += ["", "## Forecasts (all priced, settled predictions)", "",
            "| Market | n | Mean P | Actual | Brier | Log loss |", "|---|---|---|---|---|---|"]
    for market, (sc, _) in s.forecasts.items():
        out.append(f"| {market} | " + ("0 | — | — | — | — |" if sc is None else
                                       f"{sc.n} | {sc.mean_p:.1%} | {sc.actual:.1%} | {sc.brier:.4f} | "
                                       f"{sc.log_loss:.4f} |"))
    buckets = s.forecasts["UNDER05"][1]
    if buckets:
        c = cfg.calibration
        out += ["", "Calibration, Under 0.5 (judged when n >= " + str(c.min_bucket_matches) + "):", "",
                "| P bucket | n | Mean P | Actual | Gap (pts) |", "|---|---|---|---|---|"]
        out += [f"| {b.lo_pts:g}-{b.hi_pts:g} % | {b.n} | {b.mean_p:.1%} | {b.actual:.1%} | {b.gap_pts:+.1f} |"
                for b in buckets]
    if s.paired is not None:
        out += ["", f"Paired Brier, final price minus competition-only, {s.paired.n} fixtures: "
                    f"{_ci(s.paired.brier, '+.4f')} (negative = BETTORY better)."]

    b = s.bets
    out += ["", "## Paper bets (sealed before kick-off)", "",
            f"{b.bets} settled ({b.wins} won), {b.voids} void, {b.pending} pending · staked {b.staked:g} · "
            f"profit {b.profit:+.2f} · ROI {_pct(b.roi, '+.1%')} · interval {_ci(b.roi_interval, '+.1%')}",
            f"Hit rate {_pct(b.hit_rate)} vs break-even {_pct(b.break_even)} at mean odds "
            f"{'—' if b.mean_odds is None else f'{b.mean_odds:.2f}'}.",
            f"Closing line value: {_pct(b.clv_mean, '+.1%')} on average over {b.clv_n} bets with a closing line; "
            f"{_pct(b.clv_beat)} beat the fair closing price."]

    out += ["", "## Sequential evidence (valid at every look)", "",
            "| Test | Observations | E now | E peak | Bar 1/alpha | Reached |", "|---|---|---|---|---|---|",
            f"| Profit: bets at their sealed odds (H0: no edge) | {s.e_profit.n} | {s.e_profit.value:.3g} | "
            f"{s.e_profit.peak:.3g} | {bar:g} | {'yes' if s.e_profit.reached(s.alpha) else 'no'} |",
            f"| Forecast: BETTORY vs de-vigged market, Under 0.5 (H0: the market is right) | {s.e_forecast.n} | "
            f"{s.e_forecast.value:.3g} | {s.e_forecast.peak:.3g} | {bar:g} | "
            f"{'yes' if s.e_forecast.reached(s.alpha) else 'no'} |",
            "", f"Mean gap BETTORY minus market P(Under 0.5): "
                f"{'—' if s.market_gap_pts is None else f'{s.market_gap_pts:+.1f} pts'}.",
            "", "An e-value is the wealth of a bet against H0 that started at 1: under H0 it reaches 1/alpha at most "
                "alpha of the time, however often this report is read. Falling towards 0 is evidence for H0."]
    return "\n".join(out) + "\n"


def paper_json(s: PaperSummary) -> dict[str, Any]:
    def iv(i: Interval | None) -> dict[str, Any] | None:
        return None if i is None else {"estimate": i.estimate, "low": i.low, "high": i.high, "sessions": i.blocks}

    return {
        "params_version": s.params_version, "frozen": s.frozen, "sessions": s.sessions, "settled": s.settled,
        "pending": s.pending, "corrections": s.corrections,
        "chain": {"intact": s.chain.intact, "rows": s.chain.rows, "head_pos": s.chain.head_pos,
                  "head_hash": s.chain.head_hash},
        "gate": [{"name": c.name, "status": c.status, "detail": c.detail} for c in s.gate],
        "forecasts": {m: None if sc is None else {"n": sc.n, "mean_p": sc.mean_p, "actual": sc.actual,
                                                  "brier": sc.brier, "log_loss": sc.log_loss}
                      for m, (sc, _) in s.forecasts.items()},
        "paired_brier": iv(s.paired.brier) if s.paired else None,
        "bets": {"settled": s.bets.bets, "wins": s.bets.wins, "voids": s.bets.voids, "pending": s.bets.pending,
                 "staked": s.bets.staked, "profit": s.bets.profit, "roi": s.bets.roi,
                 "roi_interval": iv(s.bets.roi_interval), "break_even": s.bets.break_even,
                 "clv_n": s.bets.clv_n, "clv_mean": s.bets.clv_mean, "clv_beat": s.bets.clv_beat},
        "e_profit": {"n": s.e_profit.n, "value": s.e_profit.value, "peak": s.e_profit.peak},
        "e_forecast": {"n": s.e_forecast.n, "value": s.e_forecast.value, "peak": s.e_forecast.peak},
        "market_gap_pts": s.market_gap_pts, "alpha": s.alpha,
    }
