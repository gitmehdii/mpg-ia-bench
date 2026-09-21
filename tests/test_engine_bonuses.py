"""The bonuses the five acceptance scenarios do not exercise (spec 3.5), plus the
invariants of spec 4.1 that have no scenario of their own."""

from __future__ import annotations

import pytest
from fixtures.j5_ligue1 import naufrages, parigots, provinciaux

from mpg.engine import Bonuses, LiveSub, MatchContext, TacticalSub, resolve_match
from mpg.engine.lines import Line


def rating(result, name):
    return result.rating_of(name)


def names(xi):
    return [slot.name for slot in xi]


# ------------------------------------------------------------------------- Zahia

def test_zahia_lifts_every_starter_goalkeeper_included():
    result = resolve_match(parigots(Bonuses(zahia=True)), provinciaux())

    assert rating(result.home, "Greif") == 7.5
    assert rating(result.home, "Marquinhos") == 7.5
    assert rating(result.home, "Nuamah") == 8.5
    # The substitute who came on is a starter by the time the sheet is final.
    assert rating(result.home, "Openda") == 6.0
    # The opponent is untouched.
    assert rating(result.away, "Diouf") == 7.5


# ----------------------------------------------------------------------- Défense

def test_defense_bonus_scales_with_the_number_of_defenders():
    four = resolve_match(parigots(Bonuses(defense=True)), provinciaux())
    assert rating(four.home, "Marquinhos") == 7.5          # 4 defenders: +0.5

    five = parigots(Bonuses(defense=True))
    five.formation = "5-3-2"
    # Swap a midfielder out for a fifth defender.
    five.starters = [p for p in five.starters if p.name != "Vitinha"]
    five.starters.append(next(p for p in five.bench if p.name == "Niakhaté"))
    five.bench = [p for p in five.bench if p.name != "Niakhaté"]

    result = resolve_match(five, provinciaux())
    assert rating(result.home, "Marquinhos") == 8.0        # 5 defenders: +1


def test_three_defenders_get_no_defense_bonus():
    sheet = parigots(Bonuses(defense=True))
    sheet.formation = "3-4-3"
    sheet.starters = [p for p in sheet.starters if p.name != "Pacho"]
    sheet.starters.append(next(p for p in sheet.bench if p.name == "Openda"))
    sheet.bench = [p for p in sheet.bench if p.name != "Openda"]

    result = resolve_match(sheet, provinciaux())
    assert rating(result.home, "Marquinhos") == 7.0


# ---------------------------------------------------------------------- Capitaine

def test_captain_is_lost_when_it_is_also_the_mcdo_target():
    """Invariant 4.1.6: the two are not cumulable."""
    both = resolve_match(
        parigots(Bonuses(mcdo_target="Tolisso", captain_target="Tolisso")), provinciaux()
    )
    assert rating(both.home, "Tolisso") == 9.5             # 8.5 + 1, not + 1.5

    apart = resolve_match(
        parigots(Bonuses(mcdo_target="Doué", captain_target="Tolisso")), provinciaux()
    )
    assert rating(apart.home, "Tolisso") == 9.0
    assert rating(apart.home, "Doué") == 8.0


def test_captain_never_applies_to_the_goalkeeper():
    result = resolve_match(parigots(Bonuses(captain_target="Greif")), provinciaux())
    assert rating(result.home, "Greif") == 7.0


def test_captain_is_lost_when_the_player_does_not_play():
    result = resolve_match(parigots(Bonuses(captain_target="Ferran Torres")), provinciaux())
    assert "Ferran Torres" not in names(result.home.final_xi)
    assert rating(result.home, "Openda") == 5.5            # the substitute gets nothing


# ------------------------------------------------------------------------- Suarez

def test_suarez_also_hits_a_substitute_goalkeeper():
    """Spec 3.5: the malus follows the goalkeeper slot, replacement included."""
    away = provinciaux()
    diouf = next(p for p in away.starters if p.name == "Diouf")
    diouf.played, diouf.rating = False, None

    result = resolve_match(parigots(Bonuses(suarez=True)), away)

    assert rating(result.away, "Restes") == 5.0            # 6.0 less the Suarez
    assert "Diouf" not in names(result.away.final_xi)


# ------------------------------------------------------------------- Cheat Code

def test_cheat_code_lands_after_the_substitutions_and_spares_the_goalkeeper():
    """Invariant 4.1.4."""
    home = parigots()
    home.tactical_subs = [TacticalSub("Pacho", "Niakhaté", 6.0)]
    result = resolve_match(home, provinciaux(Bonuses(cheat_code=True)))

    # Niakhaté came on at 6.0 and then took the Cheat Code: he is on the final sheet.
    assert rating(result.home, "Niakhaté") == 5.5
    assert rating(result.home, "Greif") == 7.0             # goalkeeper spared


def test_cheat_code_does_not_change_whether_a_tactical_substitution_fires():
    """Invariant 4.1.5: the threshold reads the rating before the Cheat Code."""
    home = parigots()
    home.tactical_subs = [TacticalSub("Vitinha", "Bidstrup", 6.0)]

    result = resolve_match(home, provinciaux(Bonuses(cheat_code=True)))

    # Vitinha sits at 6.0 before the Cheat Code, so the rule does not fire, even
    # though the Cheat Code would have taken him to 5.5.
    assert "Vitinha" in names(result.home.final_xi)
    assert rating(result.home, "Vitinha") == 5.5


# -------------------------------------------------------------------------- Miroir

def test_mirror_turns_the_opposing_bonus_around():
    """The Provinciaux pose a Cheat Code, the Parigots mirror it: the Provinciaux take
    the malus instead."""
    result = resolve_match(
        parigots(Bonuses(mirror=True)), provinciaux(Bonuses(cheat_code=True))
    )

    assert rating(result.home, "Marquinhos") == 7.0        # untouched
    assert rating(result.away, "O. Camara") == 5.5         # 6.0 less the mirrored 0.5
    assert rating(result.away, "Diouf") == 7.5             # goalkeepers are spared


def test_two_mirrors_cancel_each_other():
    result = resolve_match(parigots(Bonuses(mirror=True)), provinciaux(Bonuses(mirror=True)))
    assert rating(result.home, "Marquinhos") == 7.0
    assert rating(result.away, "O. Camara") == 6.0
    assert any("cancel out" in line for line in result.home.log)


def test_mirroring_a_424_takes_its_defensive_boost():
    """Spec 3.5, 424 entry: the mirroring side pockets the +0.5 of the defence."""
    away = provinciaux(Bonuses(formation_424=True))
    away.formation = "4-2-4"
    away.starters = [p for p in away.starters if p.name not in ("Cásseres", "Bretelle")]
    away.starters.extend(p for p in away.bench if p.name in ("Tengstedt", "Sinayoko"))
    away.bench = [p for p in away.bench if p.name not in ("Tengstedt", "Sinayoko")]

    result = resolve_match(parigots(Bonuses(mirror=True)), away)

    assert rating(result.home, "Marquinhos") == 7.5        # the Parigots defence gains
    assert rating(result.home, "Vitinha") == 6.0           # midfielders gain nothing


# ---------------------------------------------------------------------- Tonton Pat'

def test_tonton_pat_caps_live_changes_at_two():
    home = parigots()
    home.live_mode = True
    home.live_subs = [
        LiveSub("Pacho", "Niakhaté", 0),
        LiveSub("Vitinha", "Bidstrup", 1),
        LiveSub("Doué", "João Neves", 2),
    ]
    result = resolve_match(home, provinciaux(Bonuses(tonton_pat=True)))

    final = names(result.home.final_xi)
    assert "Niakhaté" in final and "Bidstrup" in final
    assert "João Neves" not in final and "Doué" in final


# ------------------------------------------------------------------------ live mode

def test_live_mode_disables_tactical_and_mandatory_substitutions():
    """Spec 3.4: a starter who did not play simply leaves a hole, filled by a phantom."""
    home = parigots()
    home.live_mode = True
    home.tactical_subs = [TacticalSub("Pacho", "Niakhaté", 6.0)]

    result = resolve_match(home, provinciaux())

    assert "Niakhaté" not in names(result.home.final_xi)   # no tactical substitution
    assert "Openda" not in names(result.home.final_xi)     # no mandatory one either
    assert result.home.rotaldo_count == 1                  # Ferran Torres leaves a hole


def test_live_mode_still_replaces_a_goalkeeper_who_did_not_play():
    """Spec 3.4: the goalkeeper's mandatory replacement is chosen before kick-off."""
    home = parigots()
    home.live_mode = True
    greif = next(p for p in home.starters if p.name == "Greif")
    greif.played, greif.rating = False, None

    result = resolve_match(home, provinciaux())

    assert "de Lange" in names(result.home.final_xi)
    assert rating(result.home, "de Lange") == 7.0


def test_live_change_is_refused_once_the_kick_off_has_passed():
    home = parigots()
    home.live_mode = True
    niakhate = next(p for p in home.bench if p.name == "Niakhaté")
    niakhate.match_started = True
    home.live_subs = [LiveSub("Pacho", "Niakhaté", 0)]

    result = resolve_match(home, provinciaux())
    assert "Niakhaté" not in names(result.home.final_xi)
    assert "Pacho" in names(result.home.final_xi)


def test_live_change_can_draw_on_a_starter_who_has_not_kicked_off():
    """Spec 3.4: live changes pick from the whole squad, not only the bench."""
    home = parigots()
    home.live_mode = True
    home.live_subs = [LiveSub("Pacho", "Alexsandro", 0)]

    result = resolve_match(home, provinciaux())
    assert "Alexsandro" in names(result.home.final_xi)


# -------------------------------------------------------- out-of-position substitute

def test_out_of_position_substitute_carries_the_line_penalty():
    """Spec 3.4: -1 point per line skipped when no one of the right line is left."""
    home = parigots()
    pacho = next(p for p in home.starters if p.name == "Pacho")
    pacho.played, pacho.rating = False, None
    # Leave only midfielders and a goalkeeper on the bench.
    home.bench = [p for p in home.bench if p.name in ("de Lange", "Bidstrup")]

    result = resolve_match(home, provinciaux())

    # Bidstrup is a midfielder filling a defender slot: one line skipped, -1.
    assert rating(result.home, "Bidstrup") == 6.0          # 7.0 - 1
    slot = next(s for s in result.home.final_xi if s.name == "Bidstrup")
    assert slot.slot_line is Line.D
    assert slot.line_penalty == -1.0


def test_a_goalkeeper_is_never_pulled_out_of_position():
    """Invariant 4.1.1, the other direction: only the goalkeeper slot takes a keeper."""
    home = parigots()
    pacho = next(p for p in home.starters if p.name == "Pacho")
    pacho.played, pacho.rating = False, None
    home.bench = [p for p in home.bench if p.name == "de Lange"]

    result = resolve_match(home, provinciaux())

    assert "de Lange" not in names(result.home.final_xi)
    assert result.home.rotaldo_count == 2                  # Pacho and Ferran Torres


# --------------------------------------------------------------------- empty line

def test_an_empty_line_is_crossed_without_a_duel():
    """Invariant 4.1.8."""
    away = naufrages()
    # A 4-2-4 leaves a thin midfield; strip it entirely to make the line empty.
    away.formation = None
    away.starters = [p for p in away.starters if p.line is not Line.M]
    away.starters.extend(p for p in away.bench if p.name in ("Nordin", "I. Baldé"))
    away.bench = []

    result = resolve_match(parigots(), away)
    assert result.away.line_averages[Line.M] is None
    # The Parigots still score plenty; nothing crashed on the missing line.
    assert result.home.mpg_goals >= 4


# ------------------------------------------------------------------- rating clamp

def test_ratings_are_clamped_to_ten():
    """Invariant 4.1.9."""
    home = parigots(Bonuses(zahia=True, mcdo_target="Tolisso"))
    tolisso = next(p for p in home.starters if p.name == "Tolisso")
    tolisso.rating = 10.0

    result = resolve_match(home, provinciaux())
    assert rating(result.home, "Tolisso") == 10.0


@pytest.mark.parametrize("game_week", [1, 17, 34])
def test_context_is_carried_through(game_week):
    result = resolve_match(
        parigots(), provinciaux(), MatchContext(game_week=game_week, playoff=True)
    )
    assert result.context.game_week == game_week
    assert result.context.attacker_wins_ties is True
