"""FastAPI dependencies: session, authenticated user, league membership."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from mpg.api.security import decode_token
from mpg.db.models import League, Participant, User
from mpg.db.session import get_db

bearer = HTTPBearer(auto_error=False)

SessionDep = Annotated[Session, Depends(get_db)]

#: The web screens keep the same token in a cookie, so they can reuse every
#: authorisation dependency below rather than growing a second set.
SESSION_COOKIE = "mpg_session"


def current_user(
    request: Request,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
) -> User:
    """The caller, from the bearer header or, failing that, the session cookie.

    The cookie is set `SameSite=Lax`, which keeps another site from driving a
    state-changing request with it while still surviving ordinary navigation.
    """
    token = credentials.credentials if credentials else request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
    user_id = decode_token(token)
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown user")
    return user


UserDep = Annotated[User, Depends(current_user)]


def get_league(league_id: int, session: SessionDep, user: UserDep) -> League:
    """Authentication is resolved first on purpose: without it an anonymous caller
    could probe which league ids exist by reading the 404s."""
    league = session.get(League, league_id)
    if league is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "league not found")
    return league


LeagueDep = Annotated[League, Depends(get_league)]


def current_participant(league: LeagueDep, user: UserDep, session: SessionDep) -> Participant:
    """The caller's own participant. Everything bid-related hangs off this, which is
    what keeps a participant from ever addressing someone else's bids (spec 4.4.2)."""
    participant = session.execute(
        select(Participant).where(
            Participant.league_id == league.id, Participant.user_id == user.id
        )
    ).scalar_one_or_none()
    if participant is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "you are not in this league")
    return participant


ParticipantDep = Annotated[Participant, Depends(current_participant)]


def require_admin(participant: ParticipantDep) -> Participant:
    if not participant.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "reserved for the league admin")
    return participant


AdminDep = Annotated[Participant, Depends(require_admin)]
