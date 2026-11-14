"""Model A (FT strength + Model L) and Model L match level."""

from dataclasses import replace

import pytest

from app.calculation.baselines import BaselineStatus, CompetitionBaseline
from app.calculation.invariants import InvariantError
from app.calculation.models import (
    Expectancy,
    ModelUnavailableError,
    Period,
    Strength,
    TeamWindows,
    expectancy,
    strength,
)
from app.calculation.models.model_a import combine, model_a
from app.calculation.models.model_l import model_l, model_l_from_strength
from app.calculation.team_stats import StatsStatus, View, WindowStats


def ws(view: View, scored: float, conceded: float, ht_scored: float | None = None,
       ht_conceded: float | None = None, n: int = 10) -> WindowStats:
    """A window with per-match averages (totals = average x n)."""
    return WindowStats(view=view, status=StatsStatus.OK, reasons=(), sample_size=n, exact_sample_size=n,
                       first_kickoff=None, last_kickoff=None, wins=0, draws=0, losses=0,
                       goals_scored=round(scored * n), goals_conceded=round(conceded * n),
                       ht_goals_scored=None if ht_scored is None else round(ht_scored * n),
                       ht_goals_conceded=None if ht_conceded is None else round(ht_conceded * n))


def baseline(**kw: object) -> CompetitionBaseline:
    values: dict[str, object] = {
        "status": BaselineStatus.OK, "reasons": (), "matches": 380, "seasons": ("2025-26",),
        "avg_home_goals": 1.45, "avg_away_goals": 1.15, "avg_ht_home_goals": 0.64, "avg_ht_away_goals": 0.51,
        "ht_share": 0.44, "s": 0.12, "r": 0.28,
    }
    values.update(kw)
    return CompetitionBaseline(**values)  # type: ignore[arg-type]


# SYSTEM-DOCUMENTATION.md 8 team data (per match); HT values for Model L
WINDOWS = TeamWindows(
    home_general=ws(View.GENERAL, 1.1, 1.0, 0.5, 0.4),
    home_home=ws(View.HOME, 1.2, 0.9, 0.6, 0.4),
    away_general=ws(View.GENERAL, 1.0, 1.1, 0.4, 0.5),
    away_away=ws(View.AWAY, 0.8, 1.3, 0.3, 0.6),
)


def test_system_doc_section_8_model_a_ft() -> None:
    st = strength(WINDOWS, Period.FT, 1.45, 1.15)
    assert (round(st.attack_home, 3), round(st.defence_away, 3)) == (0.835, 0.876)    # doc
    assert (round(st.attack_away, 3), round(st.defence_home, 3)) == (0.725, 0.777)    # doc: 0.778 (rounded inputs)
    mu = expectancy(st, 1.45, 1.15)
    assert (round(mu.home, 2), round(mu.away, 2)) == (1.06, 0.65)                     # doc
    a = model_a(WINDOWS, baseline())
    assert round(a.lambda15_ft.total, 3) == 0.205 and round(a.p00_ft, 3) == 0.815    # doc: 81.5%


def test_data_doc_14_4_model_l_match_level() -> None:
    st = Strength(attack_home=0.90, defence_home=0.85, attack_away=0.80, defence_away=0.95)
    res = model_l_from_strength(st, 0.64, 0.51, 0.28)
    assert (round(res.lambda_ht.home, 3), round(res.lambda_ht.away, 3)) == (0.547, 0.347)    # doc
    assert round(res.lambda15.total, 3) == 0.250                                             # doc
    assert round(res.p00, 3) == 0.779 and round(1 / res.p00, 2) == 1.28                      # doc: 77.9%, 1.28
    assert round(1.36 * res.p00 - 1, 3) == 0.059                                             # doc: EV +5.9%


def test_model_a_is_half_ft_half_ht() -> None:
    a = model_a(WINDOWS, baseline())
    assert a.lambda15.home == pytest.approx(0.5 * a.lambda15_ft.home + 0.5 * a.model_l.lambda15.home)
    assert a.mu.home == pytest.approx(a.lambda15.home / 0.12)                # FT-equivalent for the mu blend
    only_ft = combine(a.mu_ft, 0.12, a.model_l, ft_weight=1.0)
    only_ht = combine(a.mu_ft, 0.12, a.model_l, ft_weight=0.0)
    assert only_ft.p00 == pytest.approx(a.p00_ft) and only_ht.p00 == pytest.approx(a.model_l.p00)
    assert min(only_ft.p00, only_ht.p00) <= a.p00 <= max(only_ft.p00, only_ht.p00)


def test_l_uses_ht_ratings() -> None:
    res = model_l(WINDOWS, baseline())
    assert res.strength.attack_home == pytest.approx(0.6 * 0.6 / 0.64 + 0.4 * 0.5 / 0.575)
    assert res.lambda15.total == pytest.approx(0.28 * res.lambda_ht.total)


def test_unavailable_inputs_raise_with_reason() -> None:
    with pytest.raises(ModelUnavailableError, match="no early share"):
        model_a(WINDOWS, baseline(s=None, status=BaselineStatus.INSUFFICIENT_DATA))
    with pytest.raises(ModelUnavailableError, match="no r"):
        model_l(WINDOWS, baseline(r=None))
    no_ht = replace(WINDOWS, away_away=ws(View.AWAY, 0.8, 1.3))
    with pytest.raises(ModelUnavailableError, match="HT score"):
        model_l(no_ht, baseline())
    model_a_ft_only = strength(no_ht, Period.FT, 1.45, 1.15)                # FT ratings do not need HT
    assert model_a_ft_only.attack_away > 0
    short = replace(WINDOWS, home_home=ws(View.HOME, 1.2, 0.9, 0.6, 0.4, n=6))
    with pytest.raises(ModelUnavailableError, match="6 matches"):
        model_a(short, baseline())
    with pytest.raises(ModelUnavailableError, match="average goals is 0"):
        strength(WINDOWS, Period.FT, 0.0, 1.15)


def test_invalid_configuration_rejected() -> None:
    with pytest.raises(InvariantError):
        TeamWindows(WINDOWS.home_home, WINDOWS.home_general, WINDOWS.away_general, WINDOWS.away_away)
    with pytest.raises(InvariantError):
        strength(WINDOWS, Period.FT, 1.45, 1.15, venue_weight=0.7, general_weight=0.4)
    with pytest.raises(InvariantError):
        combine(Expectancy(home=1.0, away=1.0), 0.12, model_l(WINDOWS, baseline()), ft_weight=1.5)


def test_stronger_attack_lowers_p00() -> None:
    better = replace(WINDOWS, home_home=ws(View.HOME, 2.0, 0.9, 1.0, 0.4))
    assert model_a(better, baseline()).p00 < model_a(WINDOWS, baseline()).p00


def test_team_that_never_scores_gives_zero_lambda() -> None:
    """Documents a known weakness (no shrinkage of ratings in the doc formula): see report."""
    blank = replace(WINDOWS, away_away=ws(View.AWAY, 0.0, 1.3, 0.0, 0.6),
                    away_general=ws(View.GENERAL, 0.0, 1.1, 0.0, 0.5))
    a = model_a(blank, baseline())
    assert a.lambda15.away == 0.0
