"""League creation, joining, and fixture list persistence (spec 3.1)."""

from __future__ import annotations

import secrets
import string

from sqlalchemy import select
from sqlalchemy.orm import Session

from mpg.config import LEAGUE_SIZES, MERCATO_BUDGET
from mpg.db.models import League, LeagueMatch, LeagueStatus, Participant, User
from mpg.services.calendar import full_calendar

CODE_ALPHABET = string.ascii_uppercase + string.digits


class LeagueError(ValueError):
    pass


def generate_code(session: Session, length: int = 6) -> str:
    for _ in range(20):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))
        if session.execute(select(League).where(League.code == code)).first() is None:
            return code
    raise LeagueError("could not allocate a league code")


def create_league(
    session: Session,
    *,
    name: str,
    championship_id: int,
    size: int,
    creator: User,
    team_name: str,
    return_legs: bool = True,
    playoffs: bool = False,
    first_game_week: int = 1,
) -> League:
    """Create a league. Its settings are frozen once it is validated (spec 3.1)."""
    if size not in LEAGUE_SIZES:
        raise LeagueError(f"a league holds 2, 4, 6, 8 or 10 participants, not {size}")

    league = League(
        name=name,
        code=generate_code(session),
        championship_id=championship_id,
        size=size,
        return_legs=return_legs,
        playoffs=playoffs,
        status=LeagueStatus.CREATED,
        created_by=creator.id,
        first_game_week=first_game_week,
    )
    session.add(league)
    session.flush()
    join_league(session, league, creator, team_name, is_admin=True)
    return league


def join_league(
    session: Session, league: League, user: User, team_name: str, *, is_admin: bool = False
) -> Participant:
    if league.status is not LeagueStatus.CREATED:
        raise LeagueError("this league no longer accepts new participants")
    current = session.execute(
        select(Participant).where(Participant.league_id == league.id)
    ).scalars().all()
    if len(current) >= league.size:
        raise LeagueError("this league is full")
    if any(p.user_id == user.id for p in current):
        raise LeagueError("this user has already joined the league")

    participant = Participant(
        league_id=league.id,
        user_id=user.id,
        team_name=team_name,
        budget=MERCATO_BUDGET,
        is_admin=is_admin,
    )
    session.add(participant)
    session.flush()
    return participant


def generate_fixtures(session: Session, league: League) -> list[LeagueMatch]:
    """Lay the fixture list out over the championship's game weeks."""
    participants = list(
        session.execute(
            select(Participant).where(Participant.league_id == league.id).order_by(Participant.id)
        ).scalars()
    )
    if len(participants) < 2:
        raise LeagueError("a league needs at least two participants")

    existing = session.execute(
        select(LeagueMatch).where(LeagueMatch.league_id == league.id)
    ).scalars().all()
    if existing:
        return list(existing)

    weeks = full_calendar([p.id for p in participants], return_legs=league.return_legs)
    start = league.first_game_week or 1
    fixtures: list[LeagueMatch] = []
    for offset, week in enumerate(weeks):
        for home_id, away_id in week:
            fixture = LeagueMatch(
                league_id=league.id,
                game_week_number=start + offset,
                home_participant_id=home_id,
                away_participant_id=away_id,
            )
            session.add(fixture)
            fixtures.append(fixture)
    session.flush()
    return fixtures
