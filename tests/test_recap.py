"""The side-by-side recap of a fixture."""

from __future__ import annotations

from fixtures.j5_ligue1 import naufrages, parigots, provinciaux
from mpg.engine import Bonuses, MatchContext, resolve_match
from mpg.match_recap import render_recap, team_average


def test_the_recap_shows_both_teams_and_the_score():
    result = resolve_match(
        parigots(Bonuses(defense=True, captain_target="Tolisso", suarez=True)),
        provinciaux(Bonuses(defense=True, captain_target="Sbaï", cheat_code=True)),
        MatchContext(game_week=5),
    )
    text = render_recap(
        result, home_name="Les Parigots", away_name="Les Provinciaux",
        home_captain="Tolisso", away_captain="Sbaï",
        home_bonuses=["Défense"], away_bonuses=["Cheat Code 18-26"],
    )

    assert "JOURNÉE 5" in text
    assert "5 - 4" in text
    assert "Les Parigots" in text and "Les Provinciaux" in text
    for label in ("GARDIEN", "DÉFENSE", "MILIEU", "ATTAQUE"):
        assert label in text
    # Line averages sit between the two sides, since they decide the virtual goals.
    assert "6,63" in text            # the Parigots defence
    assert "7,75" in text            # the Provinciaux attack
    # Markers.
    assert "[BC]" in text            # Tolisso: a real goal and the captaincy
    assert "[o]" in text             # Openda came on as a mandatory substitute
    assert "buteurs :" in text
    assert "bonus :" in text


def test_the_recap_reports_a_team_average():
    result = resolve_match(parigots(), naufrages(), MatchContext(game_week=5))

    home = team_average(result.home)
    away = team_average(result.away)
    assert home is not None and away is not None
    assert home > away, "the stronger side must average higher"
    # The average covers the whole XI, goalkeeper included.
    assert home == sum(s.rating for s in result.home.final_xi) / 11

    text = render_recap(result, home_name="A", away_name="B")
    assert "moyenne" in text


def test_the_recap_marks_virtual_goals_and_phantoms():
    sheet = naufrages()
    for name in ("Touba", "Thomasson", "Dembélé"):
        player = next(p for p in sheet.starters if p.name == name)
        player.played, player.rating = False, None
    sheet.bench = [p for p in sheet.bench if p.name == "Samba"]

    result = resolve_match(parigots(), sheet, MatchContext(game_week=5))
    text = render_recap(result, home_name="A", away_name="B")

    assert "[V]" in text, "a virtual goal must be marked"
    assert "Rotaldo" in text
    assert "[R]" in text
    assert "(MPG)" in text


def test_the_recap_never_runs_past_its_width():
    result = resolve_match(
        parigots(Bonuses(defense=True, captain_target="Tolisso")),
        provinciaux(Bonuses(cheat_code=True)),
        MatchContext(game_week=5),
    )
    text = render_recap(
        result, home_name="Un nom d'équipe particulièrement long", away_name="B",
    )
    for line in text.splitlines():
        assert len(line) <= 90, f"line too wide: {line!r}"
        assert line == line.rstrip(), "trailing whitespace"


def test_the_recap_reads_a_stored_fixture(tmp_path):
    """It goes through the stored report, so reading a result never recomputes it."""
    import json

    from mpg.report_view import rehydrate
    from mpg.services.matchday import _serialise

    live = resolve_match(parigots(), provinciaux(), MatchContext(game_week=5))
    stored = json.loads(json.dumps(_serialise(live)))

    assert render_recap(rehydrate(stored), home_name="A", away_name="B") == render_recap(
        live, home_name="A", away_name="B"
    )
