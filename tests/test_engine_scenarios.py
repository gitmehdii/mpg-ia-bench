"""The five acceptance scenarios of spec 5, on game week 5 of Ligue 1 2026-27.

These are tests, not scripts: each one asserts the expected scoreline and the
intermediate facts the spec calls out as the things not to get wrong.
"""

from __future__ import annotations

import pytest
from fixtures.j5_ligue1 import naufrages, parigots, provinciaux

from mpg.engine import Bonuses, MatchContext, TacticalSub, resolve_match


def rating(result, name):
    return result.rating_of(name)


def names(xi):
    return [slot.name for slot in xi]


# ------------------------------------------------------- scenario 1: crossed bonuses

def test_scenario_1_balanced_match_with_crossed_bonuses():
    """Parigots 5 - 4 Provinciaux, with Défense/Capitaine/Suarez against
    Défense/Capitaine/Cheat Code."""
    home = parigots(Bonuses(defense=True, captain_target="Tolisso", suarez=True))
    away = provinciaux(Bonuses(defense=True, captain_target="Sbaï", cheat_code=True))

    result = resolve_match(home, away)

    assert result.scoreline == (5, 4)

    # Ferran Torres did not play: Openda comes on and ends at 5.0, that is 5.5 less the
    # 0.5 of the opposing Cheat Code.
    openda = next(s for s in result.home.final_xi if s.name == "Openda")
    assert openda.replacement_kind == "mandatory"
    assert openda.replaced == "Ferran Torres"
    assert openda.rating == 5.0
    assert "Ferran Torres" not in names(result.home.final_xi)

    # Suarez takes the opposing goalkeeper from 7.5 down to 6.5.
    assert rating(result.away, "Diouf") == 6.5

    # The four Provinciaux defenders go from 6.0 to 6.5 on the Défense bonus.
    for defender in ("O. Camara", "Arcus", "Sané", "Lefort"):
        assert rating(result.away, defender) == 6.5

    # The lines are too dense on both sides: no virtual goal anywhere.
    assert result.home.mpg_goals == 0
    assert result.away.mpg_goals == 0
    assert result.home.real_goals == 5
    assert result.away.real_goals == 4


def test_scenario_1_captain_and_cheat_code_reach_the_right_players():
    home = parigots(Bonuses(defense=True, captain_target="Tolisso", suarez=True))
    away = provinciaux(Bonuses(defense=True, captain_target="Sbaï", cheat_code=True))
    result = resolve_match(home, away)

    # Tolisso: 8.5 + 0.5 captain - 0.5 Cheat Code = 8.5
    assert rating(result.home, "Tolisso") == 8.5
    # Sbaï: 8.0 + 0.5 captain, and the Parigots posed no Cheat Code
    assert rating(result.away, "Sbaï") == 8.5
    # The Cheat Code never touches a goalkeeper (invariant 4.1.4)
    assert rating(result.home, "Greif") == 7.0


# --------------------------------------------------- scenario 2: maximal mismatch

def test_scenario_2_maximal_gap_at_home():
    """Parigots 10 - 0 Naufragés: 5 real goals and 5 virtual ones."""
    result = resolve_match(parigots(), naufrages())

    assert result.scoreline == (10, 0)
    assert result.home.real_goals == 5
    assert result.home.mpg_goals == 5
    assert [s.name for s in result.home.final_xi if s.mpg_goal] == [
        "Nuno Mendes",
        "Vitinha",
        "Morton",
        "Doué",
        "Openda",
    ]


def test_scenario_2_line_averages_match_the_spec():
    from mpg.engine.lines import Line

    result = resolve_match(parigots(), naufrages())
    averages = result.away.line_averages
    assert averages[Line.A] == 3.25
    assert averages[Line.M] == 3.5
    assert averages[Line.D] == 3.625
    assert averages[Line.G] == 4.0


def test_scenario_2_nuno_mendes_wins_the_tie_at_home():
    """The twelfth-man regression test: Nuno Mendes arrives at exactly 4.00 in front of
    a goalkeeper at exactly 4.00, and scores only because the Parigots are at home."""
    result = resolve_match(parigots(), naufrages())
    scorer = next(s for s in result.home.final_xi if s.name == "Nuno Mendes")
    assert scorer.mpg_goal is True

    # 6.0 -> beats the attack (3.25) -> 5.0 -> the midfield (3.5) -> 4.5
    #     -> the defence (3.625) -> 4.00, tied with the goalkeeper at 4.00.
    assert scorer.rating == 6.0


def test_scenario_2_same_duel_is_lost_away():
    """Away from home the tied duel is lost and the score drops to 9 - 0."""
    result = resolve_match(naufrages(), parigots())

    assert result.scoreline == (0, 9)
    scorer = next(s for s in result.away.final_xi if s.name == "Nuno Mendes")
    assert scorer.mpg_goal is False


def test_scenario_2_attacker_wins_ties_without_return_legs():
    """Golden rule 1's exception: with no return legs the attacker takes the tie, so
    Nuno Mendes scores away too."""
    result = resolve_match(
        naufrages(), parigots(), MatchContext(return_legs=False)
    )
    assert result.scoreline == (0, 10)
    assert next(s for s in result.away.final_xi if s.name == "Nuno Mendes").mpg_goal is True


def test_scenario_2_pacho_does_not_score():
    """Pacho arrives at 3.5 in front of a goalkeeper at 4.0."""
    result = resolve_match(parigots(), naufrages())
    assert next(s for s in result.home.final_xi if s.name == "Pacho").mpg_goal is False


def test_scenario_2_naufrages_are_all_below_the_floor():
    result = resolve_match(parigots(), naufrages())
    assert result.away.mpg_goals == 0
    assert result.away.real_goals == 0


# ------------------------------- scenario 3: Rotaldos and goalkeeper watertightness

def _naufrages_decimated():
    """Three starters did not play -- a centre back, a defensive midfielder and a
    forward -- and the bench is down to Samba, a goalkeeper."""
    sheet = naufrages()
    for name in ("Touba", "Thomasson", "Dembélé"):
        player = next(p for p in sheet.starters if p.name == name)
        player.played = False
        player.rating = None
    sheet.bench = [p for p in sheet.bench if p.name == "Samba"]
    return sheet


def test_scenario_3_rotaldos_and_goalkeeper_watertightness():
    """Parigots 11 - 0 Naufragés: 10 goals plus the own goal three phantoms concede."""
    home = parigots(Bonuses(mcdo_target="Greif"))
    result = resolve_match(home, _naufrages_decimated())

    assert result.scoreline == (11, 0)

    # Samba must not go up into defence (invariant 4.1.1).
    assert "Samba" not in names(result.away.final_xi)

    # The three holes are filled by phantom players, which concede one own goal.
    assert result.away.rotaldo_count == 3
    assert result.home.rotaldo_own_goals_for == 1
    assert result.home.real_goals + result.home.mpg_goals == 10

    # McDo+ arms the save at 8.0, but the Naufragés scored no real goal, so it cancels
    # nothing: the counter must read 0, not 1.
    assert rating(result.home, "Greif") == 8.0
    assert result.home.saved == 0


def test_scenario_3_phantom_ratings_and_slots():
    from mpg.engine.lines import Line

    result = resolve_match(parigots(Bonuses(mcdo_target="Greif")), _naufrages_decimated())
    phantoms = [s for s in result.away.final_xi if s.is_rotaldo]
    assert len(phantoms) == 3
    assert {s.rating for s in phantoms} == {2.5}
    assert sorted(s.slot_line for s in phantoms) == sorted([Line.D, Line.M, Line.A])


@pytest.mark.parametrize(
    "missing",
    [("Touba", "Thomasson", "Dembélé"), ("Seko", "Rongier", "Maupay")],
)
def test_scenario_3_holds_whichever_players_are_missing(missing):
    sheet = naufrages()
    for name in missing:
        player = next(p for p in sheet.starters if p.name == name)
        player.played = False
        player.rating = None
    sheet.bench = [p for p in sheet.bench if p.name == "Samba"]

    result = resolve_match(parigots(Bonuses(mcdo_target="Greif")), sheet)
    assert result.scoreline == (11, 0)


def test_six_phantoms_concede_two_own_goals():
    sheet = naufrages()
    for name in ("Touba", "Seko", "Thomasson", "Rongier", "Dembélé", "Maupay"):
        player = next(p for p in sheet.starters if p.name == name)
        player.played = False
        player.rating = None
    sheet.bench = []

    result = resolve_match(parigots(), sheet)
    assert result.away.rotaldo_count == 6
    assert result.home.rotaldo_own_goals_for == 2


# ------------------------------------- scenario 4: tactical substitution, Tonton Pat'

def test_scenario_4_tactical_substitution_happens():
    """Pacho at 5.5 is below 6.0, so Niakhaté comes on at 6.0."""
    home = parigots()
    home.tactical_subs = [TacticalSub("Pacho", "Niakhaté", 6.0)]

    result = resolve_match(home, provinciaux())

    assert "Niakhaté" in names(result.home.final_xi)
    assert "Pacho" not in names(result.home.final_xi)
    assert rating(result.home, "Niakhaté") == 6.0
    niakhate = next(s for s in result.home.final_xi if s.name == "Niakhaté")
    assert niakhate.replacement_kind == "tactical"
    assert niakhate.replaced == "Pacho"


def test_scenario_4_tonton_pat_cancels_the_tactical_but_not_the_mandatory():
    home = parigots()
    home.tactical_subs = [TacticalSub("Pacho", "Niakhaté", 6.0)]
    away = provinciaux(Bonuses(tonton_pat=True))

    result = resolve_match(home, away)

    # The tactical substitution is cancelled.
    assert "Pacho" in names(result.home.final_xi)
    assert "Niakhaté" not in names(result.home.final_xi)
    # The mandatory one still works.
    assert "Openda" in names(result.home.final_xi)
    assert "Ferran Torres" not in names(result.home.final_xi)


def test_tactical_threshold_reads_the_bonus_inclusive_rating():
    """Invariant 4.1.5: with the Défense bonus Pacho sits at 6.0 and stays on."""
    home = parigots(Bonuses(defense=True))
    home.tactical_subs = [TacticalSub("Pacho", "Niakhaté", 6.0)]

    result = resolve_match(home, provinciaux())

    assert "Pacho" in names(result.home.final_xi)
    assert "Niakhaté" not in names(result.home.final_xi)


def test_tactical_substitution_is_forbidden_on_the_goalkeeper():
    home = parigots()
    home.tactical_subs = [TacticalSub("Greif", "de Lange", 9.0)]

    result = resolve_match(home, provinciaux())

    assert "Greif" in names(result.home.final_xi)
    assert "de Lange" not in names(result.home.final_xi)


def test_one_substitute_cannot_cover_two_starters():
    home = parigots()
    home.tactical_subs = [
        TacticalSub("Pacho", "Niakhaté", 6.0),
        TacticalSub("Vitinha", "Niakhaté", 7.0),
    ]

    result = resolve_match(home, provinciaux())

    assert names(result.home.final_xi).count("Niakhaté") == 1
    assert "Vitinha" in names(result.home.final_xi)


# ----------------------------------------- scenario 5: MPG save and Valise both bite

def test_scenario_5_save_and_valise_both_bite():
    """Parigots 3 - 4 Provinciaux: 5 real goals, less one saved, less one valised."""
    away = provinciaux(Bonuses(mcdo_target="Diouf", valise=True))

    result = resolve_match(parigots(), away)

    assert result.scoreline == (3, 4)
    assert rating(result.away, "Diouf") == 8.5
    assert result.home.saved == 1
    assert result.home.valise_cancelled == 1
    assert result.home.mpg_goals == 0
    assert result.home.real_goals == 3


def test_scenario_5_valise_is_paid_for_even_when_it_bites_nothing():
    """The Valise is spent and paid whether or not a goal was there to cancel."""
    away = naufrages(Bonuses(valise=True))
    result = resolve_match(naufrages(), away)

    assert result.home.real_goals == 0
    assert result.home.valise_cancelled == 0
    assert result.home.valise_compensation == 5_000_000


def test_valise_cancels_a_virtual_goal_when_there_is_no_real_one():
    """With no real goal on the sheet the Valise takes an MPG goal instead."""
    home = parigots()
    for player in home.starters:
        player.real_goals = 0

    plain = resolve_match(home, naufrages())
    assert plain.home.mpg_goals > 0

    home = parigots()
    for player in home.starters:
        player.real_goals = 0
    valised = resolve_match(home, naufrages(Bonuses(valise=True)))

    assert valised.home.mpg_goals == plain.home.mpg_goals - 1
    assert valised.home.valise_cancelled == 1


def test_valise_never_cancels_the_rotaldo_own_goal():
    """Invariant 4.1.3: the phantom punishment sits outside the Valise's reach.

    The whole Parigots squad is talked down below the 5.0 floor and stripped of its
    real goals, so the only thing left on their sheet is the own goal the opposing
    phantoms concede -- which the Valise must not be able to touch.
    """
    sheet = _naufrages_decimated()
    sheet.bonuses = Bonuses(valise=True)
    home = parigots()
    for player in home.all_players():
        player.real_goals = 0
        if player.rating is not None:
            player.rating = 1.0                # nobody can reach the 5.0 floor

    result = resolve_match(home, sheet)

    assert result.home.real_goals == 0
    assert result.home.mpg_goals == 0
    assert result.home.rotaldo_own_goals_for == 1
    assert result.home.score == 1
    assert result.home.valise_cancelled == 0


def test_mpg_save_never_cancels_a_virtual_goal():
    """Invariant 4.1.2: the save is confined to real goals, so a side whose only goals
    are virtual keeps every one of them even against a goalkeeper at 8.0.

    Nuno Mendes is talked up to 10.0 so he can still run the gauntlet: 10.0 beats the
    attack and drops to 9.0, the midfield takes him to 8.5, the defence to 8.0, and he
    ties the goalkeeper at 8.0 -- a tie the home side wins.
    """
    home = parigots()
    for player in home.all_players():
        player.real_goals = 0
    next(p for p in home.starters if p.name == "Nuno Mendes").rating = 10.0

    away = naufrages()
    next(p for p in away.starters if p.name == "Mvogo").rating = 8.0

    result = resolve_match(home, away)

    assert result.home.real_goals == 0
    assert result.home.mpg_goals == 1
    assert next(s for s in result.home.final_xi if s.name == "Nuno Mendes").mpg_goal
    # The save is armed at 8.0 but has no real goal to take, and must not touch the
    # virtual one.
    assert result.home.saved == 0
    assert result.home.score == 1
