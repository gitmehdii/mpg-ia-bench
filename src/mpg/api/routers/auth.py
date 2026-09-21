"""Registration and login."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from mpg.api.deps import SessionDep, UserDep
from mpg.api.schemas import LoginIn, RegisterIn, TokenOut, UserOut
from mpg.api.security import create_token, hash_password, verify_password
from mpg.db.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, session: SessionDep) -> TokenOut:
    existing = session.execute(select(User).where(User.email == body.email)).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "this email is already registered")
    user = User(
        email=body.email,
        display_name=body.display_name,
        password_hash=hash_password(body.password),
    )
    session.add(user)
    session.flush()
    return TokenOut(access_token=create_token(user.id))


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, session: SessionDep) -> TokenOut:
    user = session.execute(select(User).where(User.email == body.email)).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    return TokenOut(access_token=create_token(user.id))


@router.get("/me", response_model=UserOut)
def me(user: UserDep) -> UserOut:
    return UserOut(id=user.id, email=user.email, display_name=user.display_name)
