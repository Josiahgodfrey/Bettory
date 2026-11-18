"""Paper betting step 3: the paper bet sealed at the freeze by the live quote check, and the append-only settlement
(pending until the 15' result is known, WIN / LOSE with profit, outcomes without a bet, a corrected result)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
import pytest
from sqlalchemy import Engine, text

from app.backtest.config import load_backtest_config
from app.backtest.paper_report import summarise_paper
from app.domain.enums import SOURCE_IDS, SourceCode
from app.domain.sessions import SessionCalendar
from app.paper.freeze import freeze_session, replay_session
from app.paper.settle import settle_due
from app.storage.repositories import paper as paper_repo
from tests.integration.conftest import ADMIN_URL
from tests.integration.test_backtest_runner import REGISTRY, league  # noqa: F401 - the synthetic league fixture
from tests.integration.test_paper_freeze import schedule
from tests.support import PARAMS

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")
CAL = SessionCalendar(PARAMS)
# P(Under 0.5) of the synthetic fixtures is about 0.78 (minimum odds about 1.38, EV >= 8 % without a market check)
BOOKS = {"Good": {"NO": 1.40, "YES": 3.30},       # >= minimum odds, de-vigged P about 0.71: within 10 pts -> bet
         "Better": {"NO": 1.50, "YES": 2.80},     # a higher price, but the bookmaker's P is > 10 pts away -> no
         "Short": {"NO": 1.30, "YES": 3.40}}      # below the minimum odds -> no


def add_odds(url: str, match_id: UUID, captured: datetime) -> None:
    with psycopg.connect(url, autocommit=True) as c:
        for book, prices in BOOKS.items():
            row = c.execute("insert into core.bookmakers (name) values (%s) returning id", (book,)).fetchone()
            assert row is not None
            for selection, odds in prices.items():
                c.execute("insert into core.odds_snapshots (match_id, bookmaker_id, source_id, market, "
                          "provider_market_name, selection, odds, snapshot_type, captured_at) values "
                          "(%s, %s, %s, 'FIRST15_GOAL', 'Goal in 1-15 minutes', %s, %s, 'ANALYSIS', %s)",
                          (match_id, row[0], SOURCE_IDS[SourceCode.API_FOOTBALL], selection, odds, captured))


def set_result(engine: Engine, match_id: UUID, ft: tuple[int, int], ht: tuple[int, int],
               h15: tuple[int, int]) -> None:
    with engine.begin() as c:
        c.execute(text("update core.matches set status = 'FINISHED', data_status = 'COMPLETE', ft_home = :fh, "
                       "ft_away = :fa, ht_home = :hh, ht_away = :ha, h15_home = :h, h15_away = :a, "
                       "goal_before_15 = :g where id = :m"),
                  {"fh": ft[0], "fa": ft[1], "hh": ht[0], "ha": ht[1], "h": h15[0], "a": h15[1],
                   "g": sum(h15) > 0, "m": match_id})


def latest(engine: Engine, match_id: UUID) -> Any:
    with engine.connect() as c:
        return c.execute(text("select s.* from paper.settlements s join paper.predictions p on p.id = s.prediction_id "
                              "where p.match_id = :m order by s.settled_at desc limit 1"),
                         {"m": match_id}).mappings().one()


def test_paper_bet_is_sealed_and_settled(league: Any) -> None:  # noqa: F811
    engine, url, _ = league
    ko = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=8)
    bet_match, other = schedule(url, [ko, ko + timedelta(seconds=1)])
    sid = CAL.session_of(ko)
    assert CAL.session_of(ko + timedelta(seconds=1)) == sid
    as_of = CAL.run_start(sid, ko)
    add_odds(url, bet_match, as_of - timedelta(hours=1))

    r = freeze_session(engine, PARAMS, REGISTRY, sid, now=as_of, stake=1.0)
    assert r.frozen == 2 and r.bets == 1
    with engine.connect() as c:
        rows = {x.match_id: x for x in paper_repo.frozen(c, str(sid))}
    b = rows[bet_match]
    assert (b.decision, b.bet_market, b.bet_odds, b.bet_book, b.bet_stake) == (
        "QUALIFIED", "UNDER05", 1.40, "Good", 1.0)
    assert b.bet_ev is not None and b.p_under05 is not None and abs(b.bet_ev - (1.40 * b.p_under05 - 1)) < 1e-9
    assert b.best_under05_odds == 1.50 and b.best_under05_book == "Better"      # the best price is not the bet
    assert rows[other].bet_market is None
    assert replay_session(engine, PARAMS, REGISTRY, str(sid)).identical

    time.sleep(max(0.0, (ko + timedelta(seconds=2) - datetime.now(UTC)).total_seconds()))   # both kicked off
    later = datetime.now(UTC) + timedelta(hours=3)
    first = settle_due(engine, later)
    assert (first.settled, first.pending) == (0, 2)                             # 15' results not known yet

    set_result(engine, bet_match, (0, 0), (0, 0), (0, 0))
    set_result(engine, other, (2, 1), (1, 0), (1, 0))
    done = settle_due(engine, later)
    assert (done.settled, done.pending, done.corrected) == (2, 0, 0)
    won = latest(engine, bet_match)
    assert (won["bet_outcome"], won["under05"], won["draw"]) == ("WIN", True, True) and abs(won["profit"] - 0.40) < 1e-9
    no_bet = latest(engine, other)
    assert (no_bet["bet_outcome"], no_bet["profit"], no_bet["under05"], no_bet["draw"]) == (None, None, False, False)
    assert settle_due(engine, later).unchanged == 2

    set_result(engine, bet_match, (1, 0), (1, 0), (1, 0))                     # the score is corrected later
    fix = settle_due(engine, later)
    assert (fix.corrected, fix.unchanged) == (1, 1)
    lost = latest(engine, bet_match)
    assert (lost["bet_outcome"], lost["profit"], lost["supersedes"]) == ("LOSE", -1.0, won["id"])

    with pytest.raises(Exception, match="append-only"), engine.begin() as c:
        c.execute(text("update paper.settlements set profit = 5"))
    with pytest.raises(Exception, match="prediction hash"), engine.begin() as c:
        c.execute(text("insert into paper.settlements (prediction_id, prediction_hash, match_status, data_status) "
                       "values (:p, :h, 'FINISHED', 'COMPLETE')"), {"p": lost["prediction_id"],
                                                                     "h": "0" * 64})

    with engine.connect() as c:                                               # the report reads the same tables
        rep = summarise_paper(paper_repo.report_rows(c), paper_repo.verify_chain(c), load_backtest_config(), PARAMS)
    assert (rep.frozen, rep.settled, rep.pending, rep.corrections) == (2, 2, 0, 1)
    assert (rep.bets.bets, rep.bets.wins, rep.bets.profit, rep.e_profit.n) == (1, 0, -1.0, 1)
