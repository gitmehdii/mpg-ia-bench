"""Leagues: creation, joining, fixture list, standings."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from mpg.api.deps import AdminDep, LeagueDep, ParticipantDep, SessionDep, UserDep
from mpg.api.schemas import (
    FixtureOut,
    LeagueCreateIn,
    LeagueJoinIn,
    LeagueOut,
    ParticipantOut,
    StandingOut,
)
from mpg.db.models import League, LeagueMatch, LeagueStatus, Participant, Roster
from mpg.services.leagues import LeagueError, create_league, generate_fixtures, join_league
from mpg.services.standings import as_table, standings

router = APIRouter(prefix="/leagues", tags=["leagues"])


def _serialise(session, league: League, caller: Participant | None) -> LeagueOut:
    participants = session.execute(
        select(Participant).where(Participant.league_id == league.id).order_by(Participant.id)
    ).scalars().all()

    rows = []
    for participant in participants:
        squad_size = session.execute(
            select(func.count(Roster.id)).where(
                Roster.participant_id == participant.id, Roster.sold_at.is_(None)
            )
        ).scalar_one()
        rows.append(
            ParticipantOut(
                id=participant.id,
                team_name=participant.team_name,
                is_admin=participant.is_admin,
                # A rival's remaining budget is a direct read on what they can still
                # bid, so it stays hidden until the mercato is over (spec 4.4.2).
                budget=participant.budget
                if (caller and caller.id == participant.id)
                or league.status not in (LeagueStatus.MERCATO,)
                else None,
                squad_size=squad_size,
            )
        )
    return LeagueOut(
        id=league.id,
        name=league.name,
        code=league.code,
        championship_id=league.championship_id,
        size=league.size,
        return_legs=league.return_legs,
        playoffs=league.playoffs,
        status=str(league.status),
        live_mode=league.live_mode,
        participants=rows,
    )


@router.post("", response_model=LeagueOut, status_code=status.HTTP_201_CREATED)
def create(body: LeagueCreateIn, user: UserDep, session: SessionDep) -> LeagueOut:
    try:
        league = create_league(
            session,
            name=body.name,
            championship_id=body.championship_id,
            size=body.size,
            creator=user,
            team_name=body.team_name,
            return_legs=body.return_legs,
            playoffs=body.playoffs,
            first_game_week=body.first_game_week,
        )
    except LeagueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    caller = session.execute(
        select(Participant).where(
            Participant.league_id == league.id, Participant.user_id == user.id
        )
    ).scalar_one()
    return _serialise(session, league, caller)


@router.post("/{league_id}/join", response_model=LeagueOut)
def join(league: LeagueDep, body: LeagueJoinIn, user: UserDep, session: SessionDep) -> LeagueOut:
    try:
        participant = join_league(session, league, user, body.team_name)
    except LeagueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    return _serialise(session, league, participant)


@router.get("/{league_id}", response_model=LeagueOut)
def read(league: LeagueDep, participant: ParticipantDep, session: SessionDep) -> LeagueOut:
    return _serialise(session, league, participant)


@router.post("/{league_id}/fixtures", response_model=list[FixtureOut])
def build_fixtures(league: LeagueDep, admin: AdminDep, session: SessionDep) -> list[FixtureOut]:
    try:
        fixtures = generate_fixtures(session, league)
    except LeagueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    return [FixtureOut.model_validate(f, from_attributes=True) for f in fixtures]


@router.get("/{league_id}/fixtures", response_model=list[FixtureOut])
def list_fixtures(
    league: LeagueDep, participant: ParticipantDep, session: SessionDep,
    game_week: int | None = None,
) -> list[FixtureOut]:
    query = select(LeagueMatch).where(LeagueMatch.league_id == league.id)
    if game_week is not None:
        query = query.where(LeagueMatch.game_week_number == game_week)
    fixtures = session.execute(query.order_by(LeagueMatch.game_week_number)).scalars().all()
    return [FixtureOut.model_validate(f, from_attributes=True) for f in fixtures]


@router.get("/{league_id}/standings", response_model=list[StandingOut])
def table(league: LeagueDep, participant: ParticipantDep, session: SessionDep) -> list[StandingOut]:
    return [StandingOut(**row) for row in as_table(standings(session, league))]
