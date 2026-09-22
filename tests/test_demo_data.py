"""The demo seeder, so `mpg demo` cannot quietly stop producing a readable league."""

from __future__ import annotations

from sqlalchemy import select

from fixtures.league import make_session
from mpg.db.models import LeagueMatch, Participant, Roster
from mpg.demo_data import seed


def test_the_demo_seeds_two_resolved_leagues_and_one_open_mercato():
    session = make_session()
    try:
        info = seed(session)

        scores = {row["name"]: row["score"] for row in info["leagues"]}
        # The blow-out and the tight match, both on the real game week 5 figures.
        assert scores["Démo — écart maximal"] == (11, 0)
        assert scores["Démo — match serré"] == (5, 4)
        # And a third league still bidding, so the mercato screens have something to do.
        assert scores["Démo — mercato ouvert"] is None

        for row in info["leagues"]:
            if row["fixture_id"] is None:
                continue
            fixture = session.get(LeagueMatch, row["fixture_id"])
            assert fixture.report is not None
            for side in ("home", "away"):
                assert len(fixture.report[side]["final_xi"]) == 11
    finally:
        session.close()


def test_the_open_league_can_actually_be_bid_in():
    """The third league is not decorative: a bid must go through on it."""
    from mpg.db.models import League, LeagueStatus
    from mpg.mercato.service import current_round, place_bid

    session = make_session()
    try:
        seed(session)
        league = session.execute(
            select(League).where(League.code == "DEMO03")
        ).scalar_one()
        assert league.status is LeagueStatus.MERCATO

        round_ = current_round(session, league.id)
        assert round_ is not None
        assert round_.number == 1
        assert round_.resolved_at is None

        participants = session.execute(
            select(Participant).where(Participant.league_id == league.id)
        ).scalars().all()
        assert len(participants) == 2
        # Nobody owns anyone yet, so the whole pool is biddable.
        assert not session.execute(
            select(Roster).where(Roster.participant_id.in_([p.id for p in participants]))
        ).scalars().all()

        bid = place_bid(session, participants[0], round_, "Tolisso", 42)
        assert bid.amount == 42
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
        by_name = {row["name"]: row for row in info["leagues"]}
        fixture = session.get(LeagueMatch, by_name["Démo — écart maximal"]["fixture_id"])
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
        # Three leagues of two, and no duplicates from the second run.
        assert len(session.execute(select(Participant)).scalars().all()) == 6
        squads = [
            len(session.execute(
                select(Roster).where(Roster.participant_id == participant.id)
            ).scalars().all())
            for participant in session.execute(select(Participant)).scalars()
        ]
        # Four squads of 18 for the two resolved leagues, two empty ones still bidding.
        assert sorted(squads) == [0, 0, 18, 18, 18, 18]
    finally:
        session.close()
