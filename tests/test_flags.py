"""Every ambiguous rule of spec 6 sits behind a flag, and the flag really switches it.

A flag nobody exercises is a comment. These tests run the same fixture both ways and
assert the outcome differs, so the alternative reading stays a live option if a real
case ever shows MPG does it the other way.
"""

from __future__ import annotations

from fixtures.j5_ligue1 import naufrages, parigots, provinciaux
from mpg.engine import Bonuses, EngineFlags, resolve_match
from mpg.engine.flags import DEFAULT_FLAGS
from mpg.engine.lines import Line


def names(xi):
    return [slot.name for slot in xi]


def test_defaults_are_the_ones_the_spec_prescribes():
    assert DEFAULT_FLAGS.mandatory_sub_direction == "nearest"
    assert DEFAULT_FLAGS.mpg_save_can_cancel_own_goal is True
    assert DEFAULT_FLAGS.save_before_valise is True
    assert DEFAULT_FLAGS.goals_cancelled_per_valise == 1
    assert DEFAULT_FLAGS.out_of_position_line == "slot"
    assert DEFAULT_FLAGS.mirror_steals_limited_bonus is True


# ------------------------------------------- 6.1 direction of the mandatory substitution

def _midfield_hole_with_a_defender_and_a_forward():
    """A midfield hole, with a defender and a forward left on the bench, the defender
    first. Both are one line away, so the two readings of "lower position" diverge here:
    the directional one refuses to pull a defender forward."""
    sheet = parigots()
    vitinha = next(p for p in sheet.starters if p.name == "Vitinha")
    vitinha.played, vitinha.rating = False, None
    bench = {p.name: p for p in sheet.bench}
    sheet.bench = [bench["de Lange"], bench["Niakhaté"], bench["Openda"]]
    return sheet


def _who_replaced(result, starter: str) -> str:
    slot = next(s for s in result.home.final_xi if s.replaced == starter)
    return slot.name


def test_nearest_line_can_pull_a_defender_forward():
    """Default: the closest line in either direction, ties broken by bench order, so
    the defender listed first drops into midfield."""
    result = resolve_match(_midfield_hole_with_a_defender_and_a_forward(), provinciaux())
    assert _who_replaced(result, "Vitinha") == "Niakhaté"
    assert result.home.rating_of("Niakhaté") == 5.0        # 6.0 - 1 for one line


def test_lower_only_walks_away_from_the_goalkeeper():
    """The directional reading: only players further forward drop back, so the forward
    fills the midfield hole and the defender stays on the bench."""
    flags = EngineFlags(mandatory_sub_direction="lower")
    result = resolve_match(
        _midfield_hole_with_a_defender_and_a_forward(), provinciaux(), flags=flags
    )
    assert _who_replaced(result, "Vitinha") == "Openda"
    assert result.home.rating_of("Openda") == 4.5          # 5.5 - 1 for one line
    # Niakhaté is not pulled forward into midfield; he is only left to fill the
    # forward hole, two lines from home, once Openda has gone.
    assert _who_replaced(result, "Ferran Torres") == "Niakhaté"
    assert result.home.rating_of("Niakhaté") == 4.0        # 6.0 - 2 for two lines


def _defender_hole_with_only_a_midfielder_and_a_forward():
    """A defence hole with a midfielder and a forward on the bench. Both readings agree
    here -- every candidate is already further forward than the hole -- so this one is
    about the nearest-line rule itself, not about the flag."""
    sheet = parigots()
    pacho = next(p for p in sheet.starters if p.name == "Pacho")
    pacho.played, pacho.rating = False, None
    sheet.bench = [p for p in sheet.bench if p.name in ("de Lange", "Bidstrup", "Openda")]
    return sheet


def test_the_nearest_line_wins_over_the_further_one():
    """A midfielder is one line from defence, a forward two: the midfielder comes on."""
    result = resolve_match(_defender_hole_with_only_a_midfielder_and_a_forward(), provinciaux())
    assert "Bidstrup" in names(result.home.final_xi)
    assert result.home.rating_of("Bidstrup") == 6.0        # 7.0 - 1 for one line
    assert "Openda" in names(result.home.final_xi)         # he still replaces Ferran Torres


# ------------------------------------------------ 6.3 may the MPG save cancel an own goal

def _own_goal_fixture():
    """The Provinciaux put through their own net; the Parigots have nothing else."""
    home = parigots()
    for player in home.all_players():
        player.real_goals = 0
        if player.rating is not None:
            player.rating = 1.0                            # nobody reaches the 5.0 floor
    away = provinciaux()
    for player in away.starters:
        player.real_goals = 0
    next(p for p in away.starters if p.name == "Sané").own_goals = 1
    # The Parigots' own keeper is talked up so the save is armed against the Provinciaux
    # too; what matters here is the keeper facing the own goal.
    next(p for p in away.starters if p.name == "Diouf").rating = 8.0
    return home, away


def test_the_save_cancels_an_own_goal_by_default():
    home, away = _own_goal_fixture()
    result = resolve_match(home, away)
    assert result.home.saved == 1
    assert result.home.score == 0


def test_the_save_can_be_confined_to_ordinary_goals():
    home, away = _own_goal_fixture()
    flags = EngineFlags(mpg_save_can_cancel_own_goal=False)
    result = resolve_match(home, away, flags=flags)
    assert result.home.saved == 0
    assert result.home.score == 1


# ------------------------------------------------------------ 6.5 goals cancelled per Valise

def test_a_valise_cancels_one_goal_by_default_and_the_count_is_tunable():
    away = provinciaux(Bonuses(valise=True))
    one = resolve_match(parigots(), away)
    assert one.home.valise_cancelled == 1
    assert one.home.score == 4                             # 5 real goals, less one

    two = resolve_match(
        parigots(),
        provinciaux(Bonuses(valise=True)),
        flags=EngineFlags(goals_cancelled_per_valise=2),
    )
    assert two.home.valise_cancelled == 2
    assert two.home.score == 3


# ----------------------------------------------- additional call: own goal and the Valise

def _real_own_goal_only():
    home = parigots()
    for player in home.all_players():
        player.real_goals = 0
        if player.rating is not None:
            player.rating = 1.0
    away = provinciaux(Bonuses(valise=True))
    for player in away.starters:
        player.real_goals = 0
    next(p for p in away.starters if p.name == "Sané").own_goals = 1
    return home, away


def test_by_default_the_valise_follows_the_numeric_formula():
    """Spec 3.6 counts every real goal in the Valise's pool."""
    home, away = _real_own_goal_only()
    result = resolve_match(home, away)
    assert result.home.valise_cancelled == 1
    assert result.home.score == 0


def test_the_bonus_table_reading_spares_the_own_goal():
    """Spec 3.5 says a Valise never cancels an own goal; this flag follows the table."""
    home, away = _real_own_goal_only()
    result = resolve_match(home, away, flags=EngineFlags(valise_can_cancel_real_own_goal=False))
    assert result.home.valise_cancelled == 0
    assert result.home.score == 1


def test_the_rotaldo_own_goal_is_out_of_reach_under_either_reading():
    """Invariant 4.1.3 is not a flag: it holds whatever the others say."""
    away = naufrages(Bonuses(valise=True))
    for name in ("Touba", "Thomasson", "Dembélé"):
        player = next(p for p in away.starters if p.name == name)
        player.played, player.rating = False, None
    away.bench = [p for p in away.bench if p.name == "Samba"]

    home = parigots()
    for player in home.all_players():
        player.real_goals = 0
        if player.rating is not None:
            player.rating = 1.0

    for flags in (DEFAULT_FLAGS, EngineFlags(valise_can_cancel_real_own_goal=False)):
        result = resolve_match(home, away, flags=flags)
        assert result.home.rotaldo_own_goals_for == 1
        assert result.home.score == 1, "the phantom punishment must survive the Valise"


# ------------------------------------- additional call: which line an out-of-position sub plays

def test_the_substitute_plays_in_the_slot_by_default():
    result = resolve_match(_defender_hole_with_only_a_midfielder_and_a_forward(), provinciaux())
    slot = next(s for s in result.home.final_xi if s.name == "Bidstrup")
    assert slot.slot_line is Line.D
    # The defensive line still holds four, so the shape is preserved.
    assert sum(1 for s in result.home.final_xi if s.slot_line is Line.D) == 4


def test_the_substitute_can_keep_his_own_line_instead():
    flags = EngineFlags(out_of_position_line="player")
    result = resolve_match(
        _defender_hole_with_only_a_midfielder_and_a_forward(), provinciaux(), flags=flags
    )
    slot = next(s for s in result.home.final_xi if s.name == "Bidstrup")
    assert slot.slot_line is Line.M
    assert sum(1 for s in result.home.final_xi if s.slot_line is Line.D) == 3


# ------------------------------------------------------- additional call: Miroir semantics

def test_the_mirror_can_be_switched_off_entirely():
    live = resolve_match(parigots(Bonuses(mirror=True)), provinciaux(Bonuses(cheat_code=True)))
    assert live.away.rating_of("O. Camara") == 5.5

    inert = resolve_match(
        parigots(Bonuses(mirror=True)),
        provinciaux(Bonuses(cheat_code=True)),
        flags=EngineFlags(mirror_steals_limited_bonus=False),
    )
    # The Cheat Code stays with its poser and lands on the Parigots as usual.
    assert inert.away.rating_of("O. Camara") == 6.0
    assert inert.home.rating_of("Marquinhos") == 6.5


# --------------------------------------------------------- flags never touch the scenarios

def test_the_acceptance_scenarios_are_unaffected_by_the_default_flags():
    """Passing the defaults explicitly must give exactly the documented scorelines."""
    assert resolve_match(parigots(), naufrages(), flags=DEFAULT_FLAGS).scoreline == (10, 0)
    assert resolve_match(
        parigots(Bonuses(defense=True, captain_target="Tolisso", suarez=True)),
        provinciaux(Bonuses(defense=True, captain_target="Sbaï", cheat_code=True)),
        flags=DEFAULT_FLAGS,
    ).scoreline == (5, 4)
