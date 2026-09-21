"""Squad, lineups and bonuses -- the manager's side of things (spec 3.3, 3.5)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from mpg.api.deps import LeagueDep, ParticipantDep, SessionDep
from mpg.api.schemas import BonusIn, LineupIn, PlayerOut
from mpg.db.models import Lineup, Player, Roster
from mpg.services.lineups import (
    BonusError,
    LineupError,
    pose_bonus,
    record_live_sub,
    save_lineup,
)

router = APIRouter(prefix="/leagues/{league_id}/team", tags=["team"])


@router.get("/squad", response_model=list[PlayerOut])
def squad(league: LeagueDep, participant: ParticipantDep, session: SessionDep) -> list[PlayerOut]:
    rows = session.execute(
        select(Player, Roster)
        .join(Roster, Roster.player_id == Player.id)
        .where(Roster.participant_id == participant.id, Roster.sold_at.is_(None))
    ).all()
    return [
        PlayerOut(
            id=player.id,
            name=player.display_name,
            ultra_position=player.ultra_position,
            quotation=player.quotation,
            club_id=player.club_id,
            owned=True,
        )
        for player, _roster in rows
    ]


@router.put("/lineups/{game_week}", status_code=status.HTTP_204_NO_CONTENT)
def put_lineup(
    league: LeagueDep,
    participant: ParticipantDep,
    game_week: int,
    body: LineupIn,
    session: SessionDep,
) -> None:
    try:
        save_lineup(
            session,
            participant,
            game_week,
            formation=body.formation,
            starters=body.starters,
            bench=body.bench,
            tactical_subs=[(s.starter_id, s.sub_id, s.threshold) for s in body.tactical_subs],
            live_mode=body.live_mode or league.live_mode,
        )
    except LineupError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error


@router.post("/bonuses/{game_week}", status_code=status.HTTP_201_CREATED)
def add_bonus(
    league: LeagueDep,
    participant: ParticipantDep,
    game_week: int,
    body: BonusIn,
    session: SessionDep,
) -> dict:
    try:
        usage = pose_bonus(session, participant, game_week, body.bonus_type, body.target_player_id)
    except BonusError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    return {"bonus_type": usage.bonus_type, "target_player_id": usage.target_player_id}


@router.post("/lineups/{game_week}/live-subs", status_code=status.HTTP_201_CREATED)
def live_sub(
    league: LeagueDep,
    participant: ParticipantDep,
    game_week: int,
    starter_id: str,
    sub_id: str,
    session: SessionDep,
) -> dict:
    if not league.live_mode:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "this league is not in live mode")
    lineup = session.execute(
        select(Lineup).where(
            Lineup.participant_id == participant.id, Lineup.game_week_number == game_week
        )
    ).scalar_one_or_none()
    if lineup is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no lineup for this game week")
    try:
        record = record_live_sub(session, lineup, starter_id, sub_id)
    except LineupError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    return {"starter_id": record.starter_id, "sub_id": record.sub_id, "order": record.order}
