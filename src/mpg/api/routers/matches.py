"""Resolving game weeks and reading match reports (spec 3.6, 4.5)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from mpg.api.deps import AdminDep, LeagueDep, ParticipantDep, SessionDep
from mpg.api.schemas import FixtureOut
from mpg.db.models import LeagueMatch
from mpg.services.matchday import resolve_game_week, resolve_league_match

router = APIRouter(prefix="/leagues/{league_id}/matches", tags=["matches"])


@router.post("/game-weeks/{game_week}/resolve", response_model=list[FixtureOut])
def resolve(
    league: LeagueDep, admin: AdminDep, game_week: int, session: SessionDep
) -> list[FixtureOut]:
    resolve_game_week(session, league, game_week)
    fixtures = session.execute(
        select(LeagueMatch).where(
            LeagueMatch.league_id == league.id, LeagueMatch.game_week_number == game_week
        )
    ).scalars().all()
    return [FixtureOut.model_validate(f, from_attributes=True) for f in fixtures]


@router.get("/{fixture_id}/report")
def report(
    league: LeagueDep, participant: ParticipantDep, fixture_id: int, session: SessionDep
) -> dict:
    """The full match report: final XI, ratings, bonuses, goals and how they were counted."""
    fixture = session.get(LeagueMatch, fixture_id)
    if fixture is None or fixture.league_id != league.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such fixture")
    if fixture.report is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "this fixture is not resolved yet")
    return fixture.report


@router.post("/{fixture_id}/replay", response_model=FixtureOut)
def replay(
    league: LeagueDep, admin: AdminDep, fixture_id: int, session: SessionDep
) -> FixtureOut:
    """Recompute a fixture flagged by the ingestion after a retroactive rating change."""
    fixture = session.get(LeagueMatch, fixture_id)
    if fixture is None or fixture.league_id != league.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such fixture")
    resolve_league_match(session, fixture, force=True)
    return FixtureOut.model_validate(fixture, from_attributes=True)
