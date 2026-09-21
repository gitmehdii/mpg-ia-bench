"""The mercato over HTTP.

Every route here is scoped to the caller's own participant. There is deliberately no
route that returns another participant's bids, and none that returns an aggregate over
them either -- a count of bids on a player, a maximum, a "contested" flag would all let
a rival reconstruct what the others are doing (spec 4.4.2, acceptance test M8).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from mpg.api.deps import AdminDep, LeagueDep, ParticipantDep, SessionDep
from mpg.api.schemas import BidIn, BidOut, PlayerOut, RoundOut
from mpg.db.models import Bid, LeagueStatus, MercatoRound, Player, RoundValidation
from mpg.mercato.rules import BidRejected
from mpg.mercato.service import (
    cancel_bid,
    current_round,
    everyone_validated,
    free_players,
    open_mercato,
    owned_player_ids,
    place_bid,
    resolve_round,
    validate_round,
)

router = APIRouter(prefix="/leagues/{league_id}/mercato", tags=["mercato"])


def _round_or_404(session, league_id: int, round_id: int | None) -> MercatoRound:
    if round_id is None:
        round_ = current_round(session, league_id)
    else:
        round_ = session.get(MercatoRound, round_id)
    if round_ is None or round_.league_id != league_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such round")
    return round_


@router.post("/open", response_model=RoundOut, status_code=status.HTTP_201_CREATED)
def open_(league: LeagueDep, admin: AdminDep, session: SessionDep) -> RoundOut:
    if league.status is not LeagueStatus.CREATED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "the mercato is already open or done")
    round_ = open_mercato(session, league)
    return RoundOut(
        id=round_.id, number=round_.number, opens_at=round_.opens_at,
        deadline_at=round_.deadline_at, resolved_at=None, validated=False,
    )


@router.get("/round", response_model=RoundOut)
def read_round(league: LeagueDep, participant: ParticipantDep, session: SessionDep) -> RoundOut:
    round_ = _round_or_404(session, league.id, None)
    validated = session.execute(
        select(RoundValidation).where(
            RoundValidation.round_id == round_.id,
            RoundValidation.participant_id == participant.id,
        )
    ).first() is not None
    return RoundOut(
        id=round_.id, number=round_.number, opens_at=round_.opens_at,
        deadline_at=round_.deadline_at, resolved_at=round_.resolved_at, validated=validated,
    )


@router.get("/players", response_model=list[PlayerOut])
def available_players(
    league: LeagueDep, participant: ParticipantDep, session: SessionDep, limit: int = 200
) -> list[PlayerOut]:
    """Players still free. Which players are owned is public; who bids on them is not."""
    owned = owned_player_ids(session, league.id)
    pool = free_players(session, league, owned)[:limit]
    ids = [p.player_id for p in pool]
    rows = {
        row.id: row
        for row in session.execute(select(Player).where(Player.id.in_(ids))).scalars()
    }
    return [
        PlayerOut(
            id=p.player_id,
            name=rows[p.player_id].display_name if p.player_id in rows else p.name,
            ultra_position=rows[p.player_id].ultra_position if p.player_id in rows else 0,
            quotation=p.quotation,
            club_id=rows[p.player_id].club_id if p.player_id in rows else None,
            owned=False,
        )
        for p in pool
    ]


@router.get("/bids", response_model=list[BidOut])
def my_bids(league: LeagueDep, participant: ParticipantDep, session: SessionDep) -> list[BidOut]:
    """The caller's own bids, and only those. There is no route for anyone else's."""
    round_ = _round_or_404(session, league.id, None)
    bids = session.execute(
        select(Bid).where(Bid.round_id == round_.id, Bid.participant_id == participant.id)
    ).scalars().all()
    return [
        BidOut(player_id=b.player_id, amount=b.amount, is_random=b.is_random, won=b.won)
        for b in bids
    ]


@router.post("/bids", response_model=BidOut, status_code=status.HTTP_201_CREATED)
def submit_bid(
    league: LeagueDep, participant: ParticipantDep, body: BidIn, session: SessionDep
) -> BidOut:
    round_ = _round_or_404(session, league.id, None)
    try:
        bid = place_bid(session, participant, round_, body.player_id, body.amount)
    except BidRejected as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    return BidOut(player_id=bid.player_id, amount=bid.amount, is_random=False, won=None)


@router.delete("/bids/{player_id}", status_code=status.HTTP_204_NO_CONTENT)
def withdraw_bid(
    league: LeagueDep, participant: ParticipantDep, player_id: str, session: SessionDep
) -> None:
    round_ = _round_or_404(session, league.id, None)
    try:
        cancel_bid(session, participant, round_, player_id)
    except BidRejected as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error


@router.post("/validate", status_code=status.HTTP_204_NO_CONTENT)
def validate(league: LeagueDep, participant: ParticipantDep, session: SessionDep) -> None:
    """Validate the round. If everyone still open has validated, it resolves at once."""
    round_ = _round_or_404(session, league.id, None)
    try:
        validate_round(session, participant, round_)
    except BidRejected as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    if everyone_validated(session, round_, league.id):
        resolve_round(session, round_.id)


@router.post("/close", status_code=status.HTTP_204_NO_CONTENT)
def close_my_mercato(league: LeagueDep, participant: ParticipantDep, session: SessionDep) -> None:
    participant.mercato_closed = True
    session.flush()
    round_ = current_round(session, league.id)
    if round_ is not None and everyone_validated(session, round_, league.id):
        resolve_round(session, round_.id)


@router.post("/rounds/{round_id}/resolve", status_code=status.HTTP_204_NO_CONTENT)
def force_resolve(
    league: LeagueDep, admin: AdminDep, round_id: int, session: SessionDep
) -> None:
    """Resolve a round by hand. Idempotent: replaying it changes nothing."""
    round_ = _round_or_404(session, league.id, round_id)
    resolve_round(session, round_.id)
