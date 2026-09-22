"""The /ui screens: the whole journey without curl, smoke tests, and opacity.

Acceptance criterion 1 is the long test here: create an account, create a league, join
it from a second account, run the mercato, post a lineup, resolve the game week and
read the report -- every step through a form, never through the JSON API.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fixtures.league import seed_pool
from mpg.api.app import app
from mpg.db.base import Base
from mpg.db.models import (
    League,
    LeagueMatch,
    Match,
    Participant,
    Performance,
    Player,
    Roster,
)
from mpg.db.session import get_db
from mpg.engine.lines import Line, line_of

GAME_WEEK = 1


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", future=True, connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()

    def override():
        yield session

    app.dependency_overrides[get_db] = override
    yield session
    app.dependency_overrides.clear()
    session.close()


def client() -> TestClient:
    """A browser: its own cookie jar, following redirects like a browser does."""
    return TestClient(app, follow_redirects=True)


def sign_up(browser: TestClient, label: str) -> None:
    response = browser.post(
        "/ui/register",
        data={
            "email": f"{label.lower()}@example.com",
            "display_name": label,
            "password": "password123",
        },
    )
    assert response.status_code == 200, response.text
    assert "Mes ligues" in response.text


# ------------------------------------------------------- criterion 1: whole journey


def test_a_whole_season_step_can_be_played_without_curl(db):
    seed_pool(db)

    # --- an account, and a league
    alice = client()
    sign_up(alice, "Alice")
    created = alice.post(
        "/ui/leagues",
        data={"name": "Ligue des tests", "team_name": "Les Parigots", "size": "2",
              "return_legs": "1"},
    )
    assert created.status_code == 200, created.text
    league = db.execute(select(League)).scalars().one()
    code = league.code
    assert "Ligue des tests" in created.text

    # --- a second account joins with the code
    bob = client()
    sign_up(bob, "Bob")
    joined = bob.post(
        "/ui/leagues/join", data={"code": code, "team_name": "Les Provinciaux"}
    )
    assert joined.status_code == 200, joined.text
    assert "Ligue des tests" in joined.text

    # --- the admin opens the mercato
    opened = alice.post(f"/ui/leagues/{league.id}/mercato/open")
    assert opened.status_code == 200
    assert "Mercato" in opened.text

    # --- each of them bids on a free player, from the pool screen
    pool = alice.get(f"/ui/leagues/{league.id}/mercato?position=A")
    assert pool.status_code == 200
    bid = alice.post(
        f"/ui/leagues/{league.id}/mercato/bids",
        data={"player_id": "pool_40_01", "amount": "3"},
    )
    assert bid.status_code == 200
    assert "pool_40_01" in bid.text, "my own bid must show on my own screen"

    bob.post(
        f"/ui/leagues/{league.id}/mercato/bids",
        data={"player_id": "pool_40_02", "amount": "3"},
    )

    # --- both validate, which resolves the round at once
    alice.post(f"/ui/leagues/{league.id}/mercato/validate")
    bob.post(f"/ui/leagues/{league.id}/mercato/validate")
    assert db.execute(select(Roster)).scalars().all(), "the round must have awarded"

    # --- both close their mercato, which triggers the end-of-mercato draft
    alice.post(f"/ui/leagues/{league.id}/mercato/close")
    bob.post(f"/ui/leagues/{league.id}/mercato/close")
    db.expire_all()
    participants = db.execute(
        select(Participant).where(Participant.league_id == league.id).order_by(Participant.id)
    ).scalars().all()
    for participant in participants:
        squad = db.execute(
            select(Roster).where(Roster.participant_id == participant.id)
        ).scalars().all()
        assert len(squad) == 18, "every squad must be able to field a lineup"

    _give_everyone_a_rating(db, league)

    # --- a lineup, posted from the form
    for browser, participant in zip((alice, bob), participants, strict=True):
        payload = _lineup_form(db, participant)
        response = browser.post(
            f"/ui/leagues/{league.id}/team/lineups/{GAME_WEEK}", data=payload
        )
        assert response.status_code == 200, response.text
        assert "Composition enregistrée" in response.text, response.text[:800]

    # --- a bonus, also from the form
    captain = _lineup_form(db, participants[0])["starter_5"]
    bonus = alice.post(
        f"/ui/leagues/{league.id}/team/bonuses/{GAME_WEEK}",
        data={"bonus_type": "captain", "target_player_id": captain},
    )
    assert "Bonus posé" in bonus.text

    # --- the admin resolves the game week
    resolved = alice.post(
        f"/ui/leagues/{league.id}/resolve", data={"game_week": str(GAME_WEEK)}
    )
    assert resolved.status_code == 200
    fixture = db.execute(
        select(LeagueMatch).where(LeagueMatch.game_week_number == GAME_WEEK)
    ).scalars().first()
    assert fixture.resolved_at is not None

    # --- and the report reads
    report = alice.get(f"/ui/leagues/{league.id}/matches/{fixture.id}")
    assert report.status_code == 200
    assert "Journée" in report.text
    assert "Onze final" in report.text
    assert "Moyenne" in report.text or "moyenne" in report.text


def _give_everyone_a_rating(session, league: League) -> None:
    """Every rostered player played, so the report has something to explain."""
    session.add(
        Match(
            id="m1", championship_id=1, season=2026, game_week_number=GAME_WEEK,
            date=datetime(2026, 9, 20, tzinfo=UTC), status=1,
        )
    )
    session.flush()
    rostered = session.execute(
        select(Roster).join(Participant, Participant.id == Roster.participant_id)
        .where(Participant.league_id == league.id)
    ).scalars().all()
    for index, row in enumerate(rostered):
        session.add(
            Performance(
                player_id=row.player_id, match_id="m1", game_week_number=GAME_WEEK,
                season=2026, rating=5.0 + (index % 7) * 0.5,
                goals_scored=1 if index % 11 == 0 else 0,
            )
        )
    session.flush()


def _lineup_form(session, participant: Participant) -> dict[str, str]:
    """A legal 4-4-2 out of the squad, shaped the way the form posts it."""
    players = session.execute(
        select(Player).join(Roster, Roster.player_id == Player.id)
        .where(Roster.participant_id == participant.id)
    ).scalars().all()
    by_line: dict[Line, list[str]] = {}
    for player in players:
        by_line.setdefault(line_of(player.ultra_position), []).append(player.id)

    starters = [
        by_line[Line.G][0], *by_line[Line.D][:4], *by_line[Line.M][:4], *by_line[Line.A][:2]
    ]
    bench = [
        by_line[Line.G][1], *by_line[Line.D][4:6], *by_line[Line.M][4:6], *by_line[Line.A][2:4]
    ]
    payload = {"formation": "4-4-2"}
    payload |= {f"starter_{i}": pid for i, pid in enumerate(starters)}
    payload |= {f"bench_{i}": pid for i, pid in enumerate(bench)}
    return payload


# ------------------------------------------------------------------- smoke tests


@pytest.fixture
def running_league(db):
    """A two-team league past its mercato, with one game week resolved."""
    seed_pool(db)
    alice, bob = client(), client()
    sign_up(alice, "Alice")
    sign_up(bob, "Bob")
    alice.post(
        "/ui/leagues",
        data={"name": "Ligue", "team_name": "A", "size": "2", "return_legs": "1"},
    )
    league = db.execute(select(League)).scalars().one()
    bob.post("/ui/leagues/join", data={"code": league.code, "team_name": "B"})
    alice.post(f"/ui/leagues/{league.id}/mercato/open")
    alice.post(f"/ui/leagues/{league.id}/mercato/close")
    bob.post(f"/ui/leagues/{league.id}/mercato/close")
    db.expire_all()
    _give_everyone_a_rating(db, league)

    participants = db.execute(
        select(Participant).where(Participant.league_id == league.id).order_by(Participant.id)
    ).scalars().all()
    for browser, participant in zip((alice, bob), participants, strict=True):
        browser.post(
            f"/ui/leagues/{league.id}/team/lineups/{GAME_WEEK}",
            data=_lineup_form(db, participant),
        )
    alice.post(f"/ui/leagues/{league.id}/resolve", data={"game_week": str(GAME_WEEK)})
    fixture = db.execute(
        select(LeagueMatch).where(LeagueMatch.game_week_number == GAME_WEEK)
    ).scalars().first()
    return db, league, alice, bob, fixture


def test_every_ui_screen_answers_200_for_a_member(running_league):
    db, league, alice, _bob, fixture = running_league
    for url in (
        "/ui/",
        "/ui/login",
        f"/ui/leagues/{league.id}",
        f"/ui/leagues/{league.id}/mercato",
        f"/ui/leagues/{league.id}/team",
        f"/ui/leagues/{league.id}/team?game_week=1",
        f"/ui/leagues/{league.id}/matches/{fixture.id}",
    ):
        response = alice.get(url)
        assert response.status_code == 200, f"{url} answered {response.status_code}"
        assert response.headers["content-type"].startswith("text/html")


def test_a_non_member_is_refused_on_every_screen(running_league):
    db, league, _alice, _bob, fixture = running_league
    stranger = client()
    sign_up(stranger, "Zoe")

    for url in (
        f"/ui/leagues/{league.id}",
        f"/ui/leagues/{league.id}/mercato",
        f"/ui/leagues/{league.id}/team",
        f"/ui/leagues/{league.id}/matches/{fixture.id}",
    ):
        response = stranger.get(url)
        assert response.status_code == 403, f"{url} answered {response.status_code}"


def test_a_signed_out_visitor_is_sent_to_the_login_page(running_league):
    db, league, _alice, _bob, _fixture = running_league
    anonymous = TestClient(app, follow_redirects=False)

    response = anonymous.get(f"/ui/leagues/{league.id}")
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/login"


def test_logging_out_clears_the_session(running_league):
    db, league, alice, _bob, _fixture = running_league
    alice.get("/ui/logout")
    alice.follow_redirects = False
    assert alice.get("/ui/").status_code == 303


def test_a_wrong_password_is_refused_without_leaking_which_half_was_wrong(db):
    browser = client()
    sign_up(browser, "Alice")
    browser.get("/ui/logout")
    response = browser.post(
        "/ui/login", data={"email": "alice@example.com", "password": "wrong-password"}
    )
    assert response.status_code == 200
    assert "incorrect" in response.text
    assert "Mes ligues" not in response.text


# ---------------------------------------------------------- criterion 2: the report


def test_the_report_explains_each_mpg_goal_duel_by_duel(running_league):
    db, league, alice, _bob, fixture = running_league
    page = alice.get(f"/ui/leagues/{league.id}/matches/{fixture.id}").text

    result = fixture.report
    scorers = [
        slot for side in ("home", "away")
        for slot in result[side]["final_xi"] if slot.get("mpg_goal")
    ]
    if not scorers:
        pytest.skip("this fixture produced no MPG goal to explain")

    assert "duel par duel" in page
    assert "BUT MPG" in page
    # The wording of a crossing, with its numbers, is on the page.
    assert re.search(r"vs\s+(attaque|milieu|défense) adverse", page)
    assert "passe" in page


def test_the_report_refuses_a_fixture_that_is_not_resolved(running_league):
    db, league, alice, _bob, _fixture = running_league
    pending = db.execute(
        select(LeagueMatch).where(LeagueMatch.resolved_at.is_(None))
    ).scalars().first()
    if pending is None:
        pytest.skip("every fixture is resolved")
    assert alice.get(f"/ui/leagues/{league.id}/matches/{pending.id}").status_code == 409


def test_the_dashboard_splits_real_and_virtual_goals(running_league):
    db, league, alice, _bob, _fixture = running_league
    page = alice.get(f"/ui/leagues/{league.id}").text
    assert "Classement" in page
    assert "réels" in page and "MPG" in page
    assert "Départages dans l’ordre" in page


# ------------------------------------------------- criterion 3: opacity on the screens


def _pool_row(page: str, player_id: str) -> str | None:
    """The free-pool table row for one player."""
    match = re.search(rf"<tr>(?:(?!</tr>).)*{re.escape(player_id)}(?:(?!</tr>).)*</tr>",
                      page, re.S)
    return match.group(0) if match else None


def _row_shape(row: str) -> list[str]:
    """A row with its player-specific values stripped out, leaving only its structure."""
    without_ids = re.sub(r"pool_\d+_\d+", "ID", row)
    without_numbers = re.sub(r"\d+", "N", without_ids)
    return re.findall(r"<[^>]+>|[A-Za-zÀ-ÿ]+", without_numbers)


def test_the_mercato_screen_never_shows_a_rival_bid(db):
    seed_pool(db)
    alice, bob = client(), client()
    sign_up(alice, "Alice")
    sign_up(bob, "Bob")
    alice.post(
        "/ui/leagues",
        data={"name": "Ligue", "team_name": "A", "size": "2", "return_legs": "1"},
    )
    league = db.execute(select(League)).scalars().one()
    bob.post("/ui/leagues/join", data={"code": league.code, "team_name": "B"})
    alice.post(f"/ui/leagues/{league.id}/mercato/open")

    # Bob bids an amount that could not appear by accident.
    bob.post(
        f"/ui/leagues/{league.id}/mercato/bids",
        data={"player_id": "pool_40_30", "amount": "271"},
    )

    page = alice.get(f"/ui/leagues/{league.id}/mercato").text
    assert not re.search(r"(?<!\d)271(?!\d)", page), "Bob's bid amount is on Alice's screen"

    # The row for the player Bob bid on must be byte-for-byte the shape of any other
    # free player's row: no marker, no counter, nothing that says he is wanted.
    contested = _pool_row(page, "pool_40_30")
    untouched = _pool_row(page, "pool_40_29")
    assert contested and untouched
    assert _row_shape(contested) == _row_shape(untouched)

    # Bob does see his own.
    assert re.search(r"(?<!\d)271(?!\d)", bob.get(f"/ui/leagues/{league.id}/mercato").text)


def test_the_mercato_screen_never_shows_a_rival_budget(db):
    seed_pool(db)
    alice, bob = client(), client()
    sign_up(alice, "Alice")
    sign_up(bob, "Bob")
    alice.post(
        "/ui/leagues",
        data={"name": "Ligue", "team_name": "A", "size": "2", "return_legs": "1"},
    )
    league = db.execute(select(League)).scalars().one()
    bob.post("/ui/leagues/join", data={"code": league.code, "team_name": "B"})
    alice.post(f"/ui/leagues/{league.id}/mercato/open")
    bob.post(
        f"/ui/leagues/{league.id}/mercato/bids",
        data={"player_id": "pool_40_01", "amount": "137"},
    )
    bob.post(f"/ui/leagues/{league.id}/mercato/validate")

    page = alice.get(f"/ui/leagues/{league.id}/mercato").text
    assert not re.search(r"(?<!\d)137(?!\d)", page)
    # Nor whether Bob has validated, which would say how far along he is.
    assert page.count("tour validé") == 0


def test_a_rival_lineup_is_not_readable(running_league):
    """The team screen is scoped to the caller: there is no route to another squad."""
    db, league, alice, bob, _fixture = running_league
    alice_page = alice.get(f"/ui/leagues/{league.id}/team").text
    bob_page = bob.get(f"/ui/leagues/{league.id}/team").text
    alice_squad = set(re.findall(r"pool_\d+_\d+", alice_page))
    bob_squad = set(re.findall(r"pool_\d+_\d+", bob_page))
    assert alice_squad and bob_squad
    assert not (alice_squad & bob_squad), "the two screens must not share any player"
