"""Performance metrics: Brier score, log loss, calibration buckets, ROI. Pure.

FIFTEEN-MINUTE-ODDS-CALCULATION.md 15; DEVELOPMENT-PLAN.md Phase 4 (Gate B):
    Brier     mean (p - y)^2                       lower is better; compared with the competition-only baseline
    log loss  -mean(y ln p + (1 - y) ln(1 - p))
    buckets   predictions grouped by `width_pts` (70-72 %, 72-74 % ...): mean predicted vs actual rate,
              with the binomial standard error of the actual rate (how much of a gap is noise)
    ROI       profit / staked over settled bets; break-even rate = mean(1 / odds)
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.domain.enums import BetOutcome
from app.settlement.settle import Settlement

Pair = tuple[float, bool]  # (predicted probability, outcome happened)
_EPS = 1e-12


def _checked(pairs: Iterable[Pair]) -> list[Pair]:
    out = list(pairs)
    for p, _ in out:
        if not 0.0 <= p <= 1.0 or math.isnan(p):
            raise ValueError(f"probability out of range: {p}")
    return out


@dataclass(frozen=True)
class Score:
    n: int
    mean_p: float
    actual: float
    brier: float
    log_loss: float


def score(pairs: Iterable[Pair]) -> Score | None:
    ps = _checked(pairs)
    if not ps:
        return None
    n = len(ps)
    brier = sum((p - y) ** 2 for p, y in ps) / n
    ll = -sum(math.log(min(max(p if y else 1.0 - p, _EPS), 1.0)) for p, y in ps) / n
    return Score(n, sum(p for p, _ in ps) / n, sum(y for _, y in ps) / n, brier, ll)


@dataclass(frozen=True)
class Bucket:
    lo_pts: float
    hi_pts: float
    n: int
    mean_p: float
    actual: float

    @property
    def gap_pts(self) -> float:
        """actual - predicted, in percentage points (negative: the model is over-confident)."""
        return (self.actual - self.mean_p) * 100.0

    @property
    def se_pts(self) -> float:
        """Binomial standard error of the actual rate, in points (at the predicted rate)."""
        return math.sqrt(self.mean_p * (1.0 - self.mean_p) / self.n) * 100.0


def calibration(pairs: Iterable[Pair], width_pts: float) -> list[Bucket]:
    if width_pts <= 0:
        raise ValueError("bucket width must be > 0")
    groups: dict[int, list[Pair]] = {}
    for p, y in _checked(pairs):
        k = min(int(p * 100.0 // width_pts), math.ceil(100.0 / width_pts) - 1)
        groups.setdefault(k, []).append((p, y))
    out = []
    for k in sorted(groups):
        g = groups[k]
        out.append(Bucket(k * width_pts, (k + 1) * width_pts, len(g), sum(p for p, _ in g) / len(g),
                          sum(y for _, y in g) / len(g)))
    return out


@dataclass(frozen=True)
class Roi:
    bets: int  # settled (WIN / LOSE)
    wins: int
    voids: int
    pending: int
    staked: float
    profit: float
    break_even: float | None  # mean(1 / odds) of settled bets
    hit_rate: float | None

    @property
    def roi(self) -> float | None:
        return self.profit / self.staked if self.staked else None


def roi(settlements: Sequence[Settlement]) -> Roi:
    settled = [s for s in settlements if s.outcome in (BetOutcome.WIN, BetOutcome.LOSE)]
    wins = sum(s.outcome is BetOutcome.WIN for s in settled)
    n = len(settled)
    return Roi(
        bets=n, wins=wins, voids=sum(s.outcome is BetOutcome.VOID for s in settlements),
        pending=sum(s.outcome is BetOutcome.PENDING for s in settlements),
        staked=sum(s.stake for s in settled), profit=sum(s.profit for s in settled),
        break_even=sum(1.0 / s.odds for s in settled) / n if n else None, hit_rate=wins / n if n else None,
    )
