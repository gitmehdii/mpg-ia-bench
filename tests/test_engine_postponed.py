"""Matches brought forward and postponed (spec 3.8)."""

from __future__ import annotations

from fixtures.j5_ligue1 import naufrages, parigots, provinciaux
from mpg.engine import Bonuses, LiveSub, resolve_match


def rating(result, name):
    return result.rating_of(name)


def names(xi):
    return [slot.name for slot in xi]


def test_a_neutralised_player_is_rated_five_and_loses_his_goals():
    """Brought forward, or postponed with the option off: 5 for everyone concerned and
    their goals do not count."""
    home = parigots()
    for name in ("Tolisso", "Nuamah"):
        player = next(p for p in home.starters if p.name == name)
        player.neutralised = True

    result = resolve_match(home, provinciaux())

    assert rating(result.home, "Tolisso") == 5.0
    assert rating(result.home, "Nuamah") == 5.0
    # Tolisso's goal and Nuamah's two are dropped: 5 real goals become 2.
    assert result.home.real_goals == 2


def test_a_neutralised_player_can_still_start_and_is_not_replaced():
    """He is deemed present, so no mandatory substitution fires for him."""
    home = parigots()
    pacho = next(p for p in home.starters if p.name == "Pacho")
    pacho.played, pacho.rating, pacho.neutralised = False, None, True

    result = resolve_match(home, provinciaux())

    assert "Pacho" in names(result.home.final_xi)
    assert rating(result.home, "Pacho") == 5.0
    assert result.home.rotaldo_count == 0


def test_a_neutralised_player_cannot_come_on_during_the_weekend():
    """Spec 3.8: he may start, but he may not enter mid-weekend."""
    home = parigots()
    openda = next(p for p in home.bench if p.name == "Openda")
    openda.neutralised = True

    result = resolve_match(home, provinciaux())

    # Ferran Torres still has to be replaced, but Openda is not eligible any more, so
    # the next forward on the bench comes on instead.
    assert "Openda" not in names(result.home.final_xi)
    assert "Kvaratskhelia" in names(result.home.final_xi)


def test_a_neutralised_player_cannot_come_on_in_live_mode_either():
    home = parigots()
    home.live_mode = True
    niakhate = next(p for p in home.bench if p.name == "Niakhaté")
    niakhate.neutralised = True
    home.live_subs = [LiveSub("Pacho", "Niakhaté", 0)]

    result = resolve_match(home, provinciaux())
    assert "Niakhaté" not in names(result.home.final_xi)


def test_bonuses_posed_on_a_neutralised_player_still_count():
    """Spec 3.8: the rating is forced to 5, and the bonuses ride on top of it."""
    home = parigots(Bonuses(mcdo_target="Tolisso"))
    tolisso = next(p for p in home.starters if p.name == "Tolisso")
    tolisso.neutralised = True

    result = resolve_match(home, provinciaux())
    assert rating(result.home, "Tolisso") == 6.0           # 5.0 forced, + 1 McDo


def test_a_neutralised_line_still_averages_correctly():
    """The forced 5s feed the line averages like any other rating (invariant 4.1.7)."""
    from mpg.engine.lines import Line

    away = naufrages()
    for player in away.starters:
        if player.line is Line.D:
            player.neutralised = True

    result = resolve_match(parigots(), away)
    assert result.away.line_averages[Line.D] == 5.0
