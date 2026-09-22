"""The duel-by-duel trace, and the French rendering built on it.

The trace is a record of decisions the engine already made: adding it must not move a
single score, which the rest of the suite guards, and the values it records must be the
ones the comparisons actually used.
"""

from __future__ import annotations

import pytest

from fixtures.j5_ligue1 import naufrages, parigots, provinciaux
from mpg import presentation as fr
from mpg.engine import Bonuses, MatchContext, resolve_match
from mpg.engine.lines import Line
from mpg.report_text import render_match_report


def slot_of(team, name):
    return next(s for s in team.final_xi if s.name == name)


# ------------------------------------------------------------------------- the trace

def test_the_trace_reproduces_the_worked_example_of_the_spec():
    """Nuno Mendes: 6.0, then 5.0, 4.5, 4.0, tied with a goalkeeper at 4.00."""
    result = resolve_match(parigots(), naufrages())
    scorer = slot_of(result.home, "Nuno Mendes")

    assert [(d.line, d.opponent, d.rating_before, d.outcome, d.cost, d.rating_after)
            for d in scorer.duels] == [
        (Line.A, 3.25, 6.0, "won", 1.0, 5.0),
        (Line.M, 3.5, 5.0, "won", 0.5, 4.5),
        (Line.D, 3.625, 4.5, "won", 0.5, 4.0),
        (Line.G, 4.0, 4.0, "won_on_tie", 0.5, 3.5),
    ]
    assert scorer.mpg_goal is True
    assert scorer.mpg_skip_reason is None


def test_the_trace_shows_the_tie_lost_away():
    result = resolve_match(naufrages(), parigots())
    scorer = slot_of(result.away, "Nuno Mendes")

    assert scorer.duels[-1].outcome == "lost_on_tie"
    assert scorer.duels[-1].rating_before == 4.0
    assert scorer.duels[-1].opponent == 4.0
    assert scorer.duels[-1].cost == 0.0
    assert scorer.mpg_goal is False


def test_the_trace_stops_where_the_run_stops():
    """Pacho is beaten by the goalkeeper, so there is no duel after it."""
    result = resolve_match(parigots(), naufrages())
    pacho = slot_of(result.home, "Pacho")

    assert [d.outcome for d in pacho.duels] == ["won", "won", "won", "lost"]
    assert pacho.duels[-1].line is Line.G
    assert pacho.duels[-1].rating_before == 3.5


@pytest.mark.parametrize(
    ("name", "reason"),
    [("Greif", "goalkeeper"), ("Tolisso", "already_scored")],
)
def test_players_who_never_run_the_gauntlet_say_why(name, reason):
    result = resolve_match(parigots(), naufrages())
    slot = slot_of(result.home, name)
    assert slot.duels == []
    assert slot.mpg_skip_reason == reason


def test_a_player_below_the_floor_says_so():
    result = resolve_match(naufrages(), parigots())
    assert slot_of(result.home, "Maupay").mpg_skip_reason == "below_floor"


def test_an_empty_line_is_recorded_as_a_free_crossing():
    away = naufrages()
    away.formation = None
    away.starters = [p for p in away.starters if p.line is not Line.M]
    away.starters.extend(p for p in away.bench if p.name in ("Nordin", "I. Baldé"))
    away.bench = []

    result = resolve_match(parigots(), away)
    scorer = next(s for s in result.home.final_xi if s.duels)
    free = [d for d in scorer.duels if d.outcome == "free"]

    assert free, "the empty line must appear in the trace"
    assert free[0].opponent is None
    assert free[0].cost == 0.0
    assert free[0].rating_before == free[0].rating_after


def test_the_trace_is_rebuilt_on_every_resolution():
    """A sheet resolved twice must not accumulate duels."""
    home = parigots()
    first = resolve_match(home, naufrages())
    count = len(slot_of(first.home, "Nuno Mendes").duels)

    second = resolve_match(parigots(), naufrages())
    assert len(slot_of(second.home, "Nuno Mendes").duels) == count


def test_the_trace_does_not_change_any_score():
    """The guard that matters: the acceptance scorelines are untouched."""
    assert resolve_match(parigots(), naufrages()).scoreline == (10, 0)
    assert resolve_match(naufrages(), parigots()).scoreline == (0, 9)
    assert resolve_match(
        parigots(Bonuses(defense=True, captain_target="Tolisso", suarez=True)),
        provinciaux(Bonuses(defense=True, captain_target="Sbaï", cheat_code=True)),
    ).scoreline == (5, 4)


def test_the_number_of_goals_matches_the_number_of_traces_that_passed():
    result = resolve_match(parigots(), naufrages())
    passed = [s for s in result.home.final_xi if s.duels and all(d.passed for d in s.duels)]
    assert len(passed) == result.home.mpg_goals


# ------------------------------------------------------------------- French rendering

@pytest.mark.parametrize(
    ("value", "expected"),
    [(3.625, "3,63"), (7.125, "7,13"), (6.625, "6,63"), (3.25, "3,25"), (4.0, "4,00")],
)
def test_line_averages_round_half_up_the_way_a_reader_expects(value, expected):
    """3.625 reads as 3,63. Python's own formatting rounds half to even and shows 3,62."""
    assert fr.average(value) == expected


def test_an_empty_line_average_reads_as_a_dash():
    assert fr.average(None) == "—"


@pytest.mark.parametrize(
    ("count", "expected"),
    [(0, "0 but réel"), (1, "1 but réel"), (2, "2 buts réels"), (5, "5 buts réels")],
)
def test_french_agreement_starts_at_two(count, expected):
    assert fr.plural(count, "but réel", "buts réels") == expected


def test_the_gauntlet_reads_as_the_spec_writes_it():
    result = resolve_match(parigots(), naufrages())
    lines = fr.gauntlet_lines(slot_of(result.home, "Nuno Mendes"))

    assert lines == [
        "vs attaque adverse 3,25 : passe, -1,0 → 5,0",
        "vs milieu adverse 3,50 : passe, -0,5 → 4,5",
        "vs défense adverse 3,63 : passe, -0,5 → 4,0",
        "vs gardien 4,00 : égalité, avantage au domicile",
    ]


def test_the_last_duel_of_a_goal_drops_its_decrement():
    """The engine does decrement after beating the goalkeeper, but nothing follows it,
    and showing "-0,5 → 3,5" under a goal just scored only confuses the reader."""
    result = resolve_match(parigots(), naufrages())
    scorer = slot_of(result.home, "Nuno Mendes")

    assert scorer.duels[-1].cost == 0.5          # the engine really did apply it
    assert "→" not in fr.gauntlet_lines(scorer)[-1]


def test_a_failed_run_explains_itself():
    result = resolve_match(parigots(), naufrages())
    assert fr.why_no_goal(slot_of(result.home, "Pacho")) == "arrêté par gardien (4,00) à 3,5"
    assert fr.why_no_goal(slot_of(result.home, "Greif")) == (
        "un gardien ne marque jamais de but MPG"
    )
    assert fr.why_no_goal(slot_of(result.home, "Tolisso")) == "a déjà marqué un but réel"


def test_a_substitution_is_worded_with_its_kind():
    result = resolve_match(parigots(), naufrages())
    assert fr.substitution_sentence(slot_of(result.home, "Openda")) == (
        "Openda entre pour Ferran Torres (obligatoire)"
    )


def test_an_out_of_position_substitution_states_its_penalty():
    home = parigots()
    pacho = next(p for p in home.starters if p.name == "Pacho")
    pacho.played, pacho.rating = False, None
    home.bench = [p for p in home.bench if p.name in ("de Lange", "Bidstrup")]

    result = resolve_match(home, provinciaux())
    sentence = fr.substitution_sentence(slot_of(result.home, "Bidstrup"))
    assert sentence == "Bidstrup entre pour Pacho (obligatoire), -1,0 pour 1 ligne sautée"


# ------------------------------------------------------------------------- the report

def test_the_report_explains_every_mpg_goal():
    report = render_match_report(resolve_match(parigots(), naufrages()))

    assert "Les Parigots  10 - 0  Les Naufragés" in report
    for scorer in ("Nuno Mendes", "Vitinha", "Morton", "Doué", "Openda"):
        assert scorer in report
    assert "vs attaque adverse 3,25 : passe, -1,0 → 5,0" in report
    assert report.count("BUT MPG") >= 5
    assert "Openda entre pour Ferran Torres (obligatoire)" in report


def test_the_report_names_the_goals_the_save_and_the_valise_took():
    result = resolve_match(
        parigots(), provinciaux(Bonuses(mcdo_target="Diouf", valise=True))
    )
    report = render_match_report(result)

    assert "Arrêt MPG du gardien adverse (8,5) : 1 but réel annulé" in report
    assert "annulé par l'arrêt MPG" in report
    assert "annulé par la Valise à Nanard" in report
    assert "Score : 3" in report


def test_the_report_shows_the_phantoms_and_their_own_goal():
    away = naufrages()
    for name in ("Touba", "Thomasson", "Dembélé"):
        player = next(p for p in away.starters if p.name == name)
        player.played, player.rating = False, None
    away.bench = [p for p in away.bench if p.name == "Samba"]

    report = render_match_report(resolve_match(parigots(Bonuses(mcdo_target="Greif")), away))

    assert "Rotaldo" in report
    assert "CSC de punition" in report
    assert "Les Parigots  11 - 0" in report


def test_the_report_shows_the_bonuses_that_touched_a_rating():
    result = resolve_match(
        parigots(Bonuses(defense=True, captain_target="Tolisso", suarez=True)),
        provinciaux(Bonuses(defense=True, captain_target="Sbaï", cheat_code=True)),
    )
    report = render_match_report(result)

    assert "Défense +0.5" in report
    assert "Cheat Code -0.5" in report
    assert "Suarez -1" in report


def test_the_report_carries_the_game_week():
    result = resolve_match(parigots(), naufrages(), MatchContext(game_week=5))
    assert "Journée 5" in render_match_report(result)
