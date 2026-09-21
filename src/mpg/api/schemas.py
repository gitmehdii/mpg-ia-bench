"""Request and response bodies.

Note what is *not* here: no schema exposes another participant's bids, nor any
aggregate over them (a count, a maximum, a "player under pressure" flag). A closed
bid stays closed until the round is resolved (spec 4.4.2, acceptance test M8).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class RegisterIn(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str


class LeagueCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    championship_id: int = 1
    size: int = 4
    team_name: str = Field(min_length=1, max_length=64)
    return_legs: bool = True
    playoffs: bool = False
    first_game_week: int = 1


class LeagueJoinIn(BaseModel):
    team_name: str = Field(min_length=1, max_length=64)


class ParticipantOut(BaseModel):
    id: int
    team_name: str
    is_admin: bool
    #: Only ever the caller's own remaining budget; other participants read as None.
    budget: int | None = None
    squad_size: int | None = None


class LeagueOut(BaseModel):
    id: int
    name: str
    code: str
    championship_id: int
    size: int
    return_legs: bool
    playoffs: bool
    status: str
    live_mode: bool
    participants: list[ParticipantOut]


class RoundOut(BaseModel):
    id: int
    number: int
    opens_at: datetime
    deadline_at: datetime
    resolved_at: datetime | None
    #: Whether the caller has validated. Never whether anyone else has, because the
    #: pattern of validations leaks how far along the others are.
    validated: bool


class BidIn(BaseModel):
    player_id: str
    amount: int = Field(ge=1)


class BidOut(BaseModel):
    """One of the caller's own bids. There is no endpoint that returns anyone else's."""

    player_id: str
    amount: int
    is_random: bool
    won: bool | None = None


class PlayerOut(BaseModel):
    id: str
    name: str
    ultra_position: int
    quotation: int | None
    club_id: str | None
    owned: bool = False


class LineupIn(BaseModel):
    formation: str
    starters: list[str] = Field(min_length=11, max_length=11)
    bench: list[str] = Field(min_length=7, max_length=7)
    tactical_subs: list[TacticalSubIn] = Field(default_factory=list, max_length=5)
    live_mode: bool = False


class TacticalSubIn(BaseModel):
    starter_id: str
    sub_id: str
    threshold: float = Field(ge=1, le=10)


class BonusIn(BaseModel):
    bonus_type: str
    target_player_id: str | None = None


class FixtureOut(BaseModel):
    id: int
    game_week_number: int
    home_participant_id: int
    away_participant_id: int
    home_score: int | None
    away_score: int | None
    resolved_at: datetime | None
    needs_recompute: bool


class StandingOut(BaseModel):
    rank: int
    participant_id: int
    team_name: str
    played: int
    points: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    goal_difference: int
    away_goals: int


LineupIn.model_rebuild()
