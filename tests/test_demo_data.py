"""The demo seeder, so `mpg demo` cannot quietly stop producing a readable league."""

from __future__ import annotations

from sqlalchemy import select

from fixtures.league import make_session
from mpg.db.models import LeagueMatch, Participant, Roster
from mpg.demo_data import seed


def test_the_demo_seeds_two_resolved_leagues():
    session = make_session()
    try:
        info = seed(session)

        scores = {row["name"]: row["score"] for row in info["leagues"]}
        # The blow-out and the tight match, both on the real game week 5 figures.
        assert scores["Démo — écart maximal"] == (11, 0)
        assert scores["Démo — match serré"] == (5, 4)

        for row in info["leagues"]:
            fixture = session.get(LeagueMatch, row["fixture_id"])
            assert fixture.report is not None
            for side in ("home", "away"):
                assert len(fixture.report[side]["final_xi"]) == 11
    finally:
        session.close()


def test_the_demo_report_explains_goals_and_failures():
    session = make_session()
    try:
        info = seed(session)
        by_name = {row["name"]: row for row in info["leagues"]}

        blowout = session.get(LeagueMatch, by_name["Démo — écart maximal"]["fixture_id"])
        scorers = [s for s in blowout.report["home"]["final_xi"] if s["mpg_goal"]]
        assert scorers, "the blow-out must show successful gauntlets"
        assert all(s["duels"] for s in scorers)

        tight = session.get(LeagueMatch, by_name["Démo — match serré"]["fixture_id"])
        ran_and_failed = [
            s for side in ("home", "away") for s in tight.report[side]["final_xi"]
            if s["duels"] and not s["mpg_goal"]
        ]
        assert ran_and_failed, "the tight match must show runs that failed"
    finally:
        session.close()


def test_the_demo_shows_a_tactical_and_a_mandatory_substitution():
    session = make_session()
    try:
        info = seed(session)
        fixture = session.get(LeagueMatch, info["leagues"][0]["fixture_id"])
        kinds = {
            slot["replacement_kind"]
            for slot in fixture.report["home"]["final_xi"]
            if slot["replacement_kind"]
        }
        assert kinds == {"tactical", "mandatory"}
    finally:
        session.close()


def test_seeding_twice_is_idempotent():
    session = make_session()
    try:
        first = seed(session)
        second = seed(session)
        assert [row["league_id"] for row in first["leagues"]] == [
            row["league_id"] for row in second["leagues"]
        ]
        assert len(session.execute(select(Participant)).scalars().all()) == 4
        # Squads are not duplicated by a second run.
        for participant in session.execute(select(Participant)).scalars():
            squad = session.execute(
                select(Roster).where(Roster.participant_id == participant.id)
            ).scalars().all()
            assert len(squad) == 18
    finally:
        session.close()
