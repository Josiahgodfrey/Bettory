"""Build a FixtureAnalysis from the database as known at `as_of`, and verify it.

    AnalysisContext(conn, params, registry, as_of)      one per analysis moment (session run, backtest kick-off)
      .baseline(season)    pooled competition baseline (<= competition_baselines.max_pooled_seasons seasons),
                           shrunk toward the global shares of every complete league season before as_of
      .analyse(match_id)   windows, Model B v2 history with per-match competition baselines (data doc 16),
                           Models A / L / B v2 / C, price, H2H, favourite odds -> FixtureAnalysis
      .verdict(match_id)   analyse + verify; any failure -> ERROR verdict (fail closed)

Every read goes through PointInTimeReader. A model that cannot be built is recorded with its reason in
FixtureAnalysis.unavailable (the decision layer then returns INSUFFICIENT_DATA with that reason).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection

from app.calculation.baselines import (
    BaselineMatch,
    CompetitionBaseline,
    GlobalShares,
    Tally,
    competition_baseline,
    global_shares,
    pool_seasons,
    tally,
)
from app.calculation.ev import Bet
from app.calculation.invariants import InvariantError
from app.calculation.models import ModelUnavailableError, TeamWindows
from app.calculation.models.model_a import ModelAResult, model_a
from app.calculation.models.model_b2 import (
    IndexMatch,
    ModelB2Result,
    TeamIndex,
    expected_for_history_match,
    fixture_baseline,
    model_b2,
    team_index,
)
from app.calculation.models.model_c import MarketQuote, ModelCResult, model_c
from app.calculation.pricing import Price, price
from app.calculation.team_stats import H2HScope, H2HStats, TeamMatch, View, WindowStats, h2h_stats, window_stats
from app.decision.layers import FixtureAnalysis, Verdict, verify
from app.domain.enums import Decision, Grade, MarketCode
from app.domain.models import Score
from app.domain.params import Params
from app.domain.registry import Registry, season_start_year
from app.storage.point_in_time import FixtureRow, MatchRow, OddsRow, PointInTimeReader

log = logging.getLogger(__name__)

MODEL_C_MARKETS = (MarketCode.FT_1X2, MarketCode.FT_TOTAL, MarketCode.HT_TOTAL, MarketCode.HT_ASIAN_TOTAL)
_VIEWS = (("home", View.GENERAL), ("home", View.HOME), ("away", View.GENERAL), ("away", View.AWAY))


def _score(pair: tuple[int, int] | None) -> Score | None:
    return Score(home=pair[0], away=pair[1]) if pair is not None else None


def baseline_match(m: MatchRow, season_label: str) -> BaselineMatch:
    ft = _score(m.ft)
    if ft is None:
        raise InvariantError(f"finished match {m.id} without a full-time score")
    return BaselineMatch(season_label, m.data_status, ft, _score(m.ht), _score(m.h15), m.goal_before_15)


def team_match(m: MatchRow, team_id: UUID) -> TeamMatch:
    ft = _score(m.ft)
    if ft is None:
        raise InvariantError(f"finished match {m.id} without a full-time score")
    if team_id not in (m.home_team_id, m.away_team_id):
        raise InvariantError(f"team {team_id} did not play match {m.id}")
    return TeamMatch(m.kickoff_utc, m.home_team_id == team_id, m.data_status, ft, _score(m.ht), _score(m.h15),
                     m.goal_before_15)


def market_quotes(odds: Sequence[OddsRow]) -> dict[str, list[MarketQuote]]:
    """Complete markets per bookmaker (incomplete ones are not quotes: one side cannot reveal the margin)."""
    grouped: dict[tuple[str, MarketCode, float | None], dict[str, float]] = defaultdict(dict)
    for o in odds:
        grouped[(o.bookmaker, o.market, o.line)][o.selection] = o.odds
    out: dict[str, list[MarketQuote]] = defaultdict(list)
    for (bookmaker, market, line), prices in grouped.items():
        complete = ({"HOME", "DRAW", "AWAY"} if market is MarketCode.FT_1X2 else {"OVER", "UNDER"}) == set(prices)
        if complete:
            out[bookmaker].append(MarketQuote(market, prices, None if market is MarketCode.FT_1X2 else line, bookmaker))
    return dict(out)


class AnalysisContext:
    def __init__(self, conn: Connection, params: Params, registry: Registry, as_of: datetime) -> None:
        self.reader = PointInTimeReader(conn, as_of)
        self.params, self.registry, self.as_of = params, registry, as_of
        self._tallies: dict[UUID, Tally] = {}
        self._baselines: dict[UUID, CompetitionBaseline] = {}
        self._shares: GlobalShares | None = None
        self._domestic: dict[tuple[UUID, str], UUID | None] = {}

    # --- competition baselines ------------------------------------------------------------------------------

    def tally(self, season_id: UUID) -> Tally:
        if season_id not in self._tallies:
            label = self.reader.season(season_id).label
            rows = self.reader.competition_matches(season_id)
            self._tallies[season_id] = tally(baseline_match(m, label) for m in rows)
        return self._tallies[season_id]

    def shares(self) -> GlobalShares:
        if self._shares is None:
            self._shares = global_shares([self.tally(s.id) for s in self.reader.all_league_seasons()])
        return self._shares

    def baseline(self, season_id: UUID) -> CompetitionBaseline:
        """Pooled baseline of a competition-season.

        The season itself is always included (if it lacks goal minutes the baseline is INSUFFICIENT_DATA, by
        the selection-bias rule). Older seasons are pooled only when complete: leaving out a WHOLE season that
        a source does not cover is unrelated to match outcomes, unlike dropping individual INCOMPLETE matches
        (which are exactly the ones with early goals, DATA-MODEL 7.1)."""
        if season_id not in self._baselines:
            season = self.reader.season(season_id)
            start = season_start_year(season.label)
            older = sorted((s for s in self.reader.competition_seasons(season.competition_id)
                            if season_start_year(s.label) < start), key=lambda s: season_start_year(s.label),
                           reverse=True)
            pool = {season.label: self.tally(season_id)}
            for s in older:
                t = self.tally(s.id)
                if t.matches and t.exact_matches == t.matches == t.ht_matches:
                    pool[s.label] = t
            pooled, labels = pool_seasons(pool, self.params.competition_baselines.max_pooled_seasons)
            self._baselines[season_id] = competition_baseline(pooled, self.shares(), self.params, labels)
        return self._baselines[season_id]

    def _domestic_season(self, team_id: UUID, label: str) -> UUID | None:
        key = (team_id, label)
        if key not in self._domestic:
            self._domestic[key] = self.reader.domestic_league_season(team_id, label)
        return self._domestic[key]

    # --- one fixture ----------------------------------------------------------------------------------------

    def _index_history(self, rows: Sequence[MatchRow], team_id: UUID) -> list[IndexMatch]:
        out = []
        for m in rows:
            season = self.reader.season(m.competition_season_id)
            domestic_id = self._domestic_season(team_id, season.label)
            domestic = self.baseline(domestic_id) if domestic_id is not None else None
            own = self.baseline(m.competition_season_id)
            lam, method = expected_for_history_match(own, domestic, self.params)
            out.append(IndexMatch(team_match(m, team_id), lam, method))
        return out

    def _model_c(self, fixture_id: UUID, base: CompetitionBaseline | None,
                 notes: list[str]) -> tuple[ModelCResult | None, float | None]:
        """(Model C of the chosen bookmaker, that bookmaker's shortest FT win price)."""
        quotes = market_quotes(self.reader.odds(fixture_id, MODEL_C_MARKETS))
        if not quotes:
            return None, None
        r, s = (base.r, base.s) if base is not None else (None, None)
        results = {bm: model_c(qs, r, s, self.params) for bm, qs in quotes.items()}
        usable = [bm for bm, res in results.items() if res.available]
        priority = [bm for bm in self.params.model_c.bookmaker_priority if bm in usable]
        chosen = priority[0] if priority else max(usable, key=lambda bm: (len(results[bm].lines), bm), default=None)
        if chosen is None:
            first = sorted(results)[0]
            notes.append(f"Model C unusable for every bookmaker ({first}: {results[first].status})")
            return results[first], None
        favourite = None
        for q in quotes[chosen]:
            if q.market is MarketCode.FT_1X2:
                favourite = min(q.prices["HOME"], q.prices["AWAY"])
        return results[chosen], favourite

    def analyse(self, match_id: UUID, quotes: Mapping[Bet, Mapping[str, float]] | None = None,
                context_flags: tuple[str, ...] = (), bookmaker: str = "") -> FixtureAnalysis:
        """`quotes`: bookmaker 15' markets you entered (quote mode); without them the verdict gives minimum odds."""
        f: FixtureRow = self.reader.fixture(match_id)
        if self.as_of > f.kickoff_utc:
            raise InvariantError(f"as_of {self.as_of} is after kick-off {f.kickoff_utc}: not a pre-match analysis")
        p, unavailable = self.params, []
        comp = self.registry.get(f.competition_code) if f.competition_code else None
        teams = {"home": f.home_team_id, "away": f.away_team_id}

        base: CompetitionBaseline | None = None
        try:
            base = self.baseline(f.competition_season_id)
        except (ModelUnavailableError, InvariantError) as exc:
            unavailable.append(f"baseline: {exc}")

        windows: TeamWindows | None = None
        stats: dict[tuple[str, View], WindowStats] = {}
        indexes: dict[tuple[str, View], TeamIndex] = {}
        for side, view in _VIEWS:
            team = teams[side]
            rows = self.reader.team_history(team, view.value, p.data_gate.history_matches)
            stats[(side, view)] = window_stats([team_match(m, team) for m in rows], view, self.as_of, p)
            try:
                indexes[(side, view)] = team_index(self._index_history(rows, team), view, self.as_of, p)
            except (ModelUnavailableError, InvariantError) as exc:
                unavailable.append(f"{side} {view} index: {exc}")
        windows = TeamWindows(stats[("home", View.GENERAL)], stats[("home", View.HOME)],
                              stats[("away", View.GENERAL)], stats[("away", View.AWAY)])

        a: ModelAResult | None = None
        b2: ModelB2Result | None = None
        if base is not None:
            try:
                a = model_a(windows, base, p)
            except (ModelUnavailableError, InvariantError) as exc:
                unavailable.append(f"Model A: {exc}")
            if len(indexes) == 4:
                try:
                    home_dom, away_dom = (self._domestic_season(t, f.season_label) for t in teams.values())
                    lam, method = fixture_baseline(
                        f.competition_type, base, self.baseline(home_dom) if home_dom else None,
                        self.baseline(away_dom) if away_dom else None, p)
                    b2 = model_b2(indexes[("home", View.GENERAL)], indexes[("home", View.HOME)],
                                  indexes[("away", View.GENERAL)], indexes[("away", View.AWAY)], lam, p, method)
                except (ModelUnavailableError, InvariantError) as exc:
                    unavailable.append(f"Model B v2: {exc}")

        notes: list[str] = []
        c, favourite = self._model_c(match_id, base, notes)
        unavailable.extend(notes)
        pr: Price | None = None
        if a is not None and base is not None and base.s is not None:
            pr = price(a, base.s, p, b2, c)

        meetings = self.reader.h2h(f.home_team_id, f.away_team_id, p.h2h.max_meetings, p.h2h.max_years)
        h2h: H2HStats = h2h_stats([team_match(m, f.home_team_id) for m in meetings], H2HScope.ALL_VENUES,
                                  self.as_of, p)
        return FixtureAnalysis(
            fixture_id=str(match_id), kickoff_utc=f.kickoff_utc, baseline=base,
            fifteen_minute_market=bool(comp and comp.fifteen_minute_market), windows=windows, model_a=a,
            model_b2=b2, model_c=c, price=pr, unavailable=tuple(unavailable), h2h=h2h, favourite_odds=favourite,
            context_flags=context_flags, quotes=dict(quotes or {}), bookmaker=bookmaker,
        )

    def verdict(self, match_id: UUID, quotes: Mapping[Bet, Mapping[str, float]] | None = None,
                context_flags: tuple[str, ...] = (), bookmaker: str = "") -> Verdict:
        """Analyse and verify. Fail closed: any failure while building the analysis -> ERROR verdict."""
        try:
            analysis = self.analyse(match_id, quotes, context_flags, bookmaker)
        except Exception as exc:  # deliberately broad: a fixture that cannot be analysed is never a bet
            log.exception("analysis failed for fixture %s", match_id)
            return Verdict(fixture_id=str(match_id), kickoff_utc=self.as_of, decision=Decision.ERROR,
                           grade=Grade.SKIP, checks=(), params_version=self.params.params_version,
                           error=f"{type(exc).__name__}: {exc}")
        return verify(analysis, self.params)
