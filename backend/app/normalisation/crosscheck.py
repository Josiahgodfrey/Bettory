"""Cross-source agreement check (DEVELOPMENT-PLAN.md 3.6; exit criterion: >= 98% of sampled 15' results agree). Pure.

For every match with records from two or more sources (each source's latest record, as rebuilt by
repositories.matches.latest_records):
- FT  : all sources with a full-time score show the same score
- HT  : all sources with a half-time score show the same score
- 15' : all sources with a consistent goal list derive the same 15' score (needs >= 2 goal-minute sources)
- implied 15': a source's 15' score from goals must agree with every other source's HT score
  (HT 0-0 anywhere -> the 15' score must be 0-0; 15' goals can never exceed another source's HT goals)
Disagreements are listed with the values of each source, so they can be explained.
`evaluate` judges a report against config/data_quality.yaml (crosscheck.*): PASS / FAIL / NOT_MEASURABLE.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from app.calculation.recovery import derive_fifteen
from app.domain.enums import FixtureStatus, SourceCode
from app.domain.models import Score, SourceMatch
from app.domain.quality_config import CrossCheckRules


@dataclass(frozen=True)
class Agreement:
    compared: int = 0  # matches where >= 2 sources had the value
    agreed: int = 0

    @property
    def rate(self) -> float | None:
        return self.agreed / self.compared if self.compared else None

    def __add__(self, other: Agreement) -> Agreement:
        return Agreement(self.compared + other.compared, self.agreed + other.agreed)


@dataclass(frozen=True)
class Disagreement:
    match_key: str
    kind: str  # FT / HT / H15 / H15_VS_HT
    values: tuple[tuple[SourceCode, str], ...]


@dataclass(frozen=True)
class MatchComparison:
    match_key: str
    sources: tuple[SourceCode, ...]
    ft: Agreement
    ht: Agreement
    h15: Agreement
    h15_vs_ht: Agreement
    disagreements: tuple[Disagreement, ...]


def _agree(name: str, key: str, values: list[tuple[SourceCode, Score]]) -> tuple[Agreement, list[Disagreement]]:
    if len(values) < 2:
        return Agreement(), []
    same = len({v for _, v in values}) == 1
    return Agreement(1, int(same)), [] if same else [Disagreement(key, name, tuple((s, str(v)) for s, v in values))]


def compare_match(key: str, records: Sequence[SourceMatch], cutoff_minute: int) -> MatchComparison:
    finished = [r for r in records if r.status is FixtureStatus.FINISHED]
    ft, d_ft = _agree("FT", key, [(r.source, r.ft) for r in finished if r.ft is not None])
    ht, d_ht = _agree("HT", key, [(r.source, r.ht) for r in finished if r.ht is not None])
    h15_values: list[tuple[SourceCode, Score]] = []
    for r in finished:
        if r.has_goal_minutes and r.goals_consistent():
            f = derive_fifteen(r.status, r.ft, r.ht, r.goals, cutoff_minute)
            if f.h15 is not None:
                h15_values.append((r.source, f.h15))
    h15, d_h15 = _agree("H15", key, h15_values)

    implied = Agreement()
    d_imp: list[Disagreement] = []
    for src, s15 in h15_values:
        for other in finished:
            if other.source is src or other.ht is None:
                continue
            ok = s15.home <= other.ht.home and s15.away <= other.ht.away   # HT 0-0 forces 15' 0-0
            implied = implied + Agreement(1, int(ok))
            if not ok:
                d_imp.append(Disagreement(key, "H15_VS_HT", ((src, f"15' {s15}"), (other.source, f"HT {other.ht}"))))
    return MatchComparison(key, tuple(sorted({r.source for r in records})), ft, ht, h15, implied,
                           tuple(d_ft + d_ht + d_h15 + d_imp))


@dataclass(frozen=True)
class CrossCheckReport:
    matches: int
    multi_source: int  # matches with records from >= 2 sources
    ft: Agreement
    ht: Agreement
    h15: Agreement
    h15_vs_ht: Agreement
    disagreements: tuple[Disagreement, ...] = field(default=())

    def agreements(self) -> dict[str, Agreement]:
        return {"FT": self.ft, "HT": self.ht, "H15": self.h15, "H15_VS_HT": self.h15_vs_ht}


class CheckStatus(StrEnum):
    PASS = "PASS"  # every judged value agrees often enough
    FAIL = "FAIL"  # at least one judged value below min_agreement: the season's data is not trusted
    NOT_MEASURABLE = "NOT_MEASURABLE"  # nothing compared on enough matches (e.g. a single source)


@dataclass(frozen=True)
class CrossCheckVerdict:
    status: CheckStatus
    fifteen_verified: bool  # 15' results judged between >= 2 goal-minute sources and passed (Phase 3 exit)
    failed: tuple[str, ...]  # FT / HT / H15 / H15_VS_HT below min_agreement
    judged: tuple[str, ...]  # values compared on >= min_compared matches


def evaluate(report: CrossCheckReport, rules: CrossCheckRules) -> CrossCheckVerdict:
    """A value is judged when compared on >= rules.min_compared matches; judged values must agree at
    >= rules.min_agreement. Fewer comparisons are reported, never judged."""
    judged = tuple(k for k, a in report.agreements().items() if a.compared >= rules.min_compared)
    failed = tuple(k for k in judged if (report.agreements()[k].rate or 0.0) < rules.min_agreement)
    status = CheckStatus.FAIL if failed else (CheckStatus.PASS if judged else CheckStatus.NOT_MEASURABLE)
    return CrossCheckVerdict(status, "H15" in judged and "H15" not in failed, failed, judged)


def cross_check(matches: Iterable[tuple[str, Sequence[SourceMatch]]], cutoff_minute: int) -> CrossCheckReport:
    total = multi = 0
    ft = ht = h15 = imp = Agreement()
    dis: list[Disagreement] = []
    for key, records in matches:
        total += 1
        if len({r.source for r in records}) < 2:
            continue
        multi += 1
        c = compare_match(key, records, cutoff_minute)
        ft, ht, h15, imp = ft + c.ft, ht + c.ht, h15 + c.h15, imp + c.h15_vs_ht
        dis.extend(c.disagreements)
    return CrossCheckReport(total, multi, ft, ht, h15, imp, tuple(dis))
