"""A whole league, end to end, on the real Ligue 1 data of game week 5 2026-27.

Ingestion -> league -> mercato -> squads -> lineups -> game week resolution ->
standings, with no network access: the API payloads are committed fixtures, trimmed
to the fields the pipeline actually reads.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from fixtures.league import make_session
from mpg.db.models import (
    Championship,
    LeagueMatch,
    Participant,
    Performance,
    Player,
    PlayerQuotation,
    Roster,
    User,
)
from mpg.engine.lines import Line, line_of
from mpg.ingest.client import MpgClient
from mpg.ingest.jobs import ingest_clubs, ingest_pool
from mpg.mercato.rules import quota_satisfied
from mpg.mercato.service import close_mercato, open_mercato, squad_positions
from mpg.services.leagues import create_league, generate_fixtures, join_league
from mpg.services.lineups import pose_bonus, save_lineup
from mpg.services.matchday import resolve_game_week, resolve_league_match
from mpg.services.standings import as_table, standings

DATA = Path(__file__).parent / "fixtures" / "data"
GAME_WEEK = 5


class OfflineClient(MpgClient):
    """The real payloads, served from disk."""

    def __init__(self) -> None:
        pass

    def clubs(self) -> dict:
        return json.loads((DATA / "clubs_l1.json").read_text())

    def players_pool(self, championship_id: int, season: int | None = None) -> dict:
        return json.loads((DATA / "pool_l1_2026.json").read_text())


@pytest.fixture
def ingested():
    session = make_session()
    session.add(Championship(id=1, code="fr_l1", name="Ligue 1", current_season=2026))
    session.flush()
    client = OfflineClient()
    ingest_clubs(session, client)
    report = ingest_pool(session, client, 1)
    session.flush()
    yield session, report
    session.close()


def test_ingestion_loads_the_pool_with_its_ratings(ingested):
    session, report = ingested

    assert report.players == 496
    assert report.quotations == 496            # one opening quotation per player
    assert report.performances > 300

    # Quotations are integers from 1 to 40 (spec 1.4).
    quotations = list(session.execute(select(Player.quotation)).scalars())
    assert all(1 <= q <= 40 for q in quotations if q is not None)

    # Ratings are multiples of 0.5 between 1 and 10.
    ratings = [
        r for r in session.execute(select(Performance.rating)).scalars() if r is not None
    ]
    assert ratings
    assert all(1.0 <= r <= 10.0 and (r * 2) % 1 == 0 for r in ratings)

    # Every ultraPosition maps onto a line (spec 1.5).
    for ultra in session.execute(select(Player.ultra_position).distinct()).scalars():
        assert line_of(ultra) in (Line.G, Line.D, Line.M, Line.A)


def test_ingestion_historises_quotations_and_never_overwrites(ingested):
    """Spec 4.5: the mercato and the market need the series, so a new quotation is
    appended and the previous one is left alone."""
    session, _report = ingested
    player = session.execute(
        select(Player).where(Player.last_name == "Marquinhos")
    ).scalars().first()
    original = player.quotation

    class Bumped(OfflineClient):
        """The same payload, with this one player's quotation moved."""

        def players_pool(self, championship_id: int, season: int | None = None) -> dict:
            payload = super().players_pool(championship_id, season)
            for raw in payload["poolPlayers"]:
                if raw["id"] == player.id:
                    raw["quotation"] = original + 3
            return payload

    report = ingest_pool(session, Bumped(), 1)

    rows = session.execute(
        select(PlayerQuotation).where(PlayerQuotation.player_id == player.id)
        .order_by(PlayerQuotation.observed_at)
    ).scalars().all()
    assert [row.quotation for row in rows] == [original, original + 3]
    assert player.quotation == original + 3
    # Only the player who moved gets a new row.
    assert report.quotations == 1

    # Re-ingesting the same value appends nothing.
    ingest_pool(session, Bumped(), 1)
    again = session.execute(
        select(func.count(PlayerQuotation.id)).where(PlayerQuotation.player_id == player.id)
    ).scalar_one()
    assert again == 2


def test_a_revised_rating_is_upserted_not_duplicated(ingested):
    """Spec 1.6: upsert on (player_id, match_id), because a rating can change."""
    session, _report = ingested
    performance = session.execute(
        select(Performance).where(Performance.rating.is_not(None))
    ).scalars().first()
    key = (performance.player_id, performance.match_id)
    original = performance.rating
    performance.rating = 1.0
    session.flush()

    report = ingest_pool(session, OfflineClient(), 1)

    assert report.revised_performances >= 1
    rows = session.execute(
        select(Performance).where(
            Performance.player_id == key[0], Performance.match_id == key[1]
        )
    ).scalars().all()
    assert len(rows) == 1, "the rating is upserted, never duplicated"
    assert rows[0].rating == original
    assert rows[0].revised_at is not None


@pytest.fixture
def running_league(ingested):
    """A league of 4 that has been through a full mercato and can field lineups."""
    session, _report = ingested

    users = []
    for label in "ABCD":
        user = User(
            email=f"{label.lower()}@example.com", display_name=label, password_hash="x"
        )
        session.add(user)
        users.append(user)
    session.flush()

    league = create_league(
        session,
        name="E2E",
        championship_id=1,
        size=4,
        creator=users[0],
        team_name="Team A",
        first_game_week=GAME_WEEK,
    )
    for user in users[1:]:
        join_league(session, league, user, f"Team {user.display_name}")
    session.flush()

    open_mercato(session, league)
    # Nobody bids: the end-of-mercato draft has to produce four playable squads.
    close_mercato(session, league)
    generate_fixtures(session, league)
    session.flush()
    return session, league


def test_the_mercato_always_produces_playable_squads(running_league):
    """Spec 4.4.5: a mercato must never leave a squad that cannot field a lineup."""
    session, league = running_league
    participants = session.execute(
        select(Participant).where(Participant.league_id == league.id)
    ).scalars().all()

    assert len(participants) == 4
    for participant in participants:
        positions = squad_positions(session, participant.id)
        assert quota_satisfied(positions), f"participant {participant.id} is short"

    # A player belongs to exactly one participant across the whole league (spec 3.2).
    owned = list(session.execute(select(Roster.player_id)).scalars())
    assert len(owned) == len(set(owned)) == 4 * 18


def _pick_lineup(session, participant) -> tuple[str, list[str], list[str]]:
    """A legal 4-4-2 out of whatever the draft handed over."""
    rows = session.execute(
        select(Player)
        .join(Roster, Roster.player_id == Player.id)
        .where(Roster.participant_id == participant.id)
    ).scalars().all()
    by_line: dict[Line, list[str]] = {}
    for player in rows:
        by_line.setdefault(line_of(player.ultra_position), []).append(player.id)

    starters = [
        by_line[Line.G][0],
        *by_line[Line.D][:4],
        *by_line[Line.M][:4],
        *by_line[Line.A][:2],
    ]
    bench = [
        by_line[Line.G][1],
        *by_line[Line.D][4:6],
        *by_line[Line.M][4:6],
        *by_line[Line.A][2:4],
    ]
    return "4-4-2", starters, bench


def test_a_full_game_week_resolves_and_feeds_the_standings(running_league):
    session, league = running_league
    participants = session.execute(
        select(Participant).where(Participant.league_id == league.id).order_by(Participant.id)
    ).scalars().all()

    for participant in participants:
        formation, starters, bench = _pick_lineup(session, participant)
        save_lineup(
            session, participant, GAME_WEEK,
            formation=formation, starters=starters, bench=bench,
        )
    # One side posts a captain, to prove bonuses ride through the whole pipeline.
    _formation, starters, _bench = _pick_lineup(session, participants[0])
    pose_bonus(session, participants[0], GAME_WEEK, "captain", target_player_id=starters[-1])
    session.flush()

    results = resolve_game_week(session, league, GAME_WEEK)

    assert len(results) == 2
    for result in results:
        assert result.home.score >= 0 and result.away.score >= 0
        assert len(result.home.final_xi) == 11
        assert len(result.away.final_xi) == 11
        # Every slot carries a rating inside the legal interval.
        for slot in [*result.home.final_xi, *result.away.final_xi]:
            assert 1.0 <= slot.rating <= 10.0

    fixtures = session.execute(
        select(LeagueMatch).where(
            LeagueMatch.league_id == league.id, LeagueMatch.game_week_number == GAME_WEEK
        )
    ).scalars().all()
    assert all(f.resolved_at is not None for f in fixtures)
    assert all(f.report is not None for f in fixtures)

    table = as_table(standings(session, league))
    assert len(table) == 4
    assert sum(row["played"] for row in table) == 4
    # Goals for and goals against balance out across the table.
    assert sum(row["goals_for"] for row in table) == sum(row["goals_against"] for row in table)
    assert sum(row["points"] for row in table) in range(4, 13)


def test_a_resolved_fixture_is_not_resolved_twice(running_league):
    session, league = running_league
    participants = session.execute(
        select(Participant).where(Participant.league_id == league.id).order_by(Participant.id)
    ).scalars().all()
    for participant in participants:
        formation, starters, bench = _pick_lineup(session, participant)
        save_lineup(
            session, participant, GAME_WEEK,
            formation=formation, starters=starters, bench=bench,
        )
    resolve_game_week(session, league, GAME_WEEK)

    fixture = session.execute(
        select(LeagueMatch).where(LeagueMatch.game_week_number == GAME_WEEK)
    ).scalars().first()
    with pytest.raises(ValueError, match="already resolved"):
        resolve_league_match(session, fixture)

    # A replay is explicit, and reproduces the same score.
    before = (fixture.home_score, fixture.away_score)
    resolve_league_match(session, fixture, force=True)
    assert (fixture.home_score, fixture.away_score) == before


def test_the_match_report_explains_the_score(running_league):
    session, league = running_league
    participants = session.execute(
        select(Participant).where(Participant.league_id == league.id).order_by(Participant.id)
    ).scalars().all()
    for participant in participants:
        formation, starters, bench = _pick_lineup(session, participant)
        save_lineup(
            session, participant, GAME_WEEK,
            formation=formation, starters=starters, bench=bench,
        )
    resolve_game_week(session, league, GAME_WEEK)

    fixture = session.execute(
        select(LeagueMatch).where(LeagueMatch.game_week_number == GAME_WEEK)
    ).scalars().first()
    report = fixture.report

    for side in ("home", "away"):
        assert len(report[side]["final_xi"]) == 11
        assert "line_averages" in report[side]
        assert "log" in report[side]
    # The stored score matches the one the report accounts for.
    home = report["home"]
    assert fixture.home_score == (
        home["real_goals"] + home["mpg_goals"] + home["rotaldo_own_goals_for"]
    )
