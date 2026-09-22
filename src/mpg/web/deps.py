"""Web-specific plumbing.

Authorisation is *not* reimplemented here: the cookie carries the same token as the
API, `current_user` reads either, and every screen goes through the existing
`current_participant` and `require_admin`. This module only adds the redirect
behaviour a browser needs, and the login page's cookie handling.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select

from mpg.api.deps import SESSION_COOKIE, SessionDep
from mpg.api.security import decode_token
from mpg.db.models import League, Participant, User

#: A week, matching the token's own lifetime.
COOKIE_MAX_AGE = 7 * 24 * 3600


def set_session_cookie(response: Response, token: str, *, secure: bool = False) -> None:
    """`SameSite=Lax` keeps another site from driving a state-changing request with
    this cookie, while ordinary navigation still carries it."""
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


class LoginRequired(Exception):
    """Raised instead of a 401 so a browser is sent to the login page."""

    def __init__(self, next_url: str = "/ui/") -> None:
        self.next_url = next_url


def ui_user(request: Request, session: SessionDep) -> User:
    """The signed-in user, or a redirect to the login page."""
    token = request.cookies.get(SESSION_COOKIE)
    user = None
    if token:
        user_id = decode_token(token)
        if user_id is not None:
            user = session.get(User, user_id)
    if user is None:
        raise LoginRequired(str(request.url.path))
    return user


UiUser = Annotated[User, Depends(ui_user)]


def ui_league(league_id: int, session: SessionDep, user: UiUser) -> League:
    league = session.get(League, league_id)
    if league is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cette ligue n’existe pas.")
    return league


UiLeague = Annotated[League, Depends(ui_league)]


def ui_participant(league: UiLeague, user: UiUser, session: SessionDep) -> Participant:
    """The caller's own participant.

    Same rule as the API: a non-member gets a 403, so no screen can be reached by
    someone outside the league.
    """
    participant = session.execute(
        select(Participant).where(
            Participant.league_id == league.id, Participant.user_id == user.id
        )
    ).scalar_one_or_none()
    if participant is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Vous ne participez pas à cette ligue."
        )
    return participant


UiParticipant = Annotated[Participant, Depends(ui_participant)]


def ui_admin(participant: UiParticipant) -> Participant:
    if not participant.is_admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Réservé à l’administrateur de la ligue."
        )
    return participant


UiAdmin = Annotated[Participant, Depends(ui_admin)]


def redirect(url: str, *, status_code: int = status.HTTP_303_SEE_OTHER) -> RedirectResponse:
    """A redirect after a form post, so a refresh does not repeat it."""
    return RedirectResponse(url, status_code=status_code)
