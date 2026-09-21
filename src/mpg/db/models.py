"""Database model (spec 2, 4.4.1, 4.5).

Reference entities are fed by the MPG public API; game entities belong to this build.
"""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mpg.db.base import Base, JSONType, TimestampMixin

# ------------------------------------------------------------------ reference entities


class Championship(Base):
    __tablename__ = "championship"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    current_season: Mapped[int | None] = mapped_column(Integer)
    first_game_week: Mapped[int | None] = mapped_column(Integer)
    last_game_week: Mapped[int | None] = mapped_column(Integer)


class Club(Base):
    __tablename__ = "club"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    short_name: Mapped[str | None] = mapped_column(String(32))
    championship_id: Mapped[int | None] = mapped_column(ForeignKey("championship.id"))
    primary_color: Mapped[str | None] = mapped_column(String(16))


class Player(Base):
    __tablename__ = "player"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str] = mapped_column(String(128), nullable=False)
    #: Fine-grained position (10/20/21/30/31/40); drives every rule (spec 1.5).
    ultra_position: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int | None] = mapped_column(Integer)
    club_id: Mapped[str | None] = mapped_column(ForeignKey("club.id"), index=True)
    championship_id: Mapped[int | None] = mapped_column(ForeignKey("championship.id"), index=True)
    country_code: Mapped[str | None] = mapped_column(String(8))
    birth_date: Mapped[date | None] = mapped_column(Date)
    #: Latest known quotation, denormalised for listings; history lives in PlayerQuotation.
    quotation: Mapped[int | None] = mapped_column(Integer)
    #: True once the player has left the championship (spec 3.2: refunded at cost).
    left_championship: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    stats: Mapped[dict | None] = mapped_column(JSONType)

    @property
    def display_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip() if self.first_name else self.last_name


class PlayerQuotation(Base):
    """Quotation history. Never overwritten: the mercato and the market need it (spec 4.5)."""

    __tablename__ = "player_quotation"
    __table_args__ = (
        UniqueConstraint("player_id", "observed_at", name="uq_quotation_player_time"),
        Index("ix_quotation_player_time", "player_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("player.id"), nullable=False)
    championship_id: Mapped[int | None] = mapped_column(ForeignKey("championship.id"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quotation: Mapped[int] = mapped_column(Integer, nullable=False)


class Match(Base):
    __tablename__ = "match"
    __table_args__ = (
        Index("ix_match_championship_week", "championship_id", "season", "game_week_number"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    championship_id: Mapped[int] = mapped_column(ForeignKey("championship.id"), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    game_week_number: Mapped[int] = mapped_column(Integer, nullable=False)
    date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    home_club_id: Mapped[str | None] = mapped_column(ForeignKey("club.id"))
    away_club_id: Mapped[str | None] = mapped_column(ForeignKey("club.id"))
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    #: 1 means finished, as served by the API (spec 1.4).
    status: Mapped[int | None] = mapped_column(Integer)
    #: Spec 3.8: brought forward or postponed with respect to its game week.
    is_advanced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_postponed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Performance(Base):
    """A player's rating for one match. Upserted on (player_id, match_id) (spec 1.6)."""

    __tablename__ = "performance"
    __table_args__ = (
        UniqueConstraint("player_id", "match_id", name="uq_performance_player_match"),
        Index("ix_performance_week", "game_week_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("player.id"), nullable=False)
    match_id: Mapped[str] = mapped_column(ForeignKey("match.id"), nullable=False)
    game_week_number: Mapped[int] = mapped_column(Integer, nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Official MPG rating, 1 to 10 by steps of 0.5. None means the player did not play.
    rating: Mapped[float | None] = mapped_column(Float)
    goals_scored: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    own_goals: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Set when a later ingestion changed an already-used rating (spec 4.5).
    revised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ----------------------------------------------------------------------- game entities


class LeagueStatus(enum.StrEnum):
    CREATED = "created"
    MERCATO = "mercato"
    RUNNING = "running"
    FINISHED = "finished"


class User(Base, TimestampMixin):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)


class League(Base, TimestampMixin):
    __tablename__ = "league"
    __table_args__ = (
        CheckConstraint("size IN (2,4,6,8,10)", name="ck_league_size"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(12), nullable=False, unique=True, index=True)
    championship_id: Mapped[int] = mapped_column(ForeignKey("championship.id"), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    return_legs: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    playoffs: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[LeagueStatus] = mapped_column(
        Enum(LeagueStatus, name="league_status"), default=LeagueStatus.CREATED, nullable=False
    )
    created_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"))
    mercato_opens_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mercato_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Spec 3.4: live weekend mode, switched on by the admin and never switched off.
    live_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: Spec 3.8: recompute a game week retroactively once a postponed match is
    #: played.
    postponed_recompute: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    first_game_week: Mapped[int | None] = mapped_column(Integer)

    participants: Mapped[list[Participant]] = relationship(
        back_populates="league", cascade="all, delete-orphan"
    )


class Participant(Base, TimestampMixin):
    __tablename__ = "participant"
    __table_args__ = (UniqueConstraint("league_id", "user_id", name="uq_participant_league_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("league.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), nullable=False)
    team_name: Mapped[str] = mapped_column(String(64), nullable=False)
    jersey_id: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    #: Remaining budget, in millions of euros (spec 3.2).
    budget: Mapped[int] = mapped_column(Integer, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: Spec 3.2: the participant clicked "close my mercato".
    mercato_closed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: Spec 3.3: keep the bench from one game week to the next.
    keep_bench: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    league: Mapped[League] = relationship(back_populates="participants")
    roster: Mapped[list[Roster]] = relationship(
        back_populates="participant", cascade="all, delete-orphan"
    )


class Roster(Base):
    """A player owned by a participant. One owner per player per league (spec 3.2)."""

    __tablename__ = "roster"
    __table_args__ = (
        UniqueConstraint("participant_id", "player_id", name="uq_roster_participant_player"),
        Index("ix_roster_player", "player_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    participant_id: Mapped[int] = mapped_column(
        ForeignKey("participant.id"), nullable=False, index=True
    )
    player_id: Mapped[str] = mapped_column(ForeignKey("player.id"), nullable=False)
    bought_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Price actually paid, in millions; also the resale value (spec 1.4).
    bought_price: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Set when the player was handed over by the end-of-mercato draft (spec 4.4.5).
    from_draft: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    participant: Mapped[Participant] = relationship(back_populates="roster")


class MercatoRound(Base):
    """One closed-bid round (spec 4.4.1)."""

    __tablename__ = "mercato_round"
    __table_args__ = (UniqueConstraint("league_id", "number", name="uq_round_league_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("league.id"), nullable=False, index=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    opens_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Makes every draw of the round reproducible, so a resolution can be replayed.
    seed: Mapped[int] = mapped_column(Integer, nullable=False)


class Bid(Base):
    """A closed bid. Invisible to everyone but its author until resolution (spec 4.4.2)."""

    __tablename__ = "bid"
    __table_args__ = (
        UniqueConstraint(
            "round_id", "participant_id", "player_id", name="uq_bid_round_part_player"
        ),
        CheckConstraint("amount >= 1", name="ck_bid_amount_positive"),
        Index("ix_bid_round_participant", "round_id", "participant_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("mercato_round.id"), nullable=False)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participant.id"), nullable=False)
    player_id: Mapped[str] = mapped_column(ForeignKey("player.id"), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    is_random: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    won: Mapped[bool | None] = mapped_column(Boolean)


class RoundValidation(Base):
    __tablename__ = "round_validation"
    __table_args__ = (
        UniqueConstraint("round_id", "participant_id", name="uq_validation_round_participant"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("mercato_round.id"), nullable=False)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participant.id"), nullable=False)
    validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MercatoLog(Base):
    """Audit trail of a resolution: draws, budget refusals, drafted players (spec 4.4.3)."""

    __tablename__ = "mercato_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("league.id"), nullable=False, index=True)
    round_id: Mapped[int | None] = mapped_column(ForeignKey("mercato_round.id"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    player_id: Mapped[str | None] = mapped_column(ForeignKey("player.id"))
    participant_id: Mapped[int | None] = mapped_column(ForeignKey("participant.id"))
    detail: Mapped[dict | None] = mapped_column(JSONType)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Lineup(Base, TimestampMixin):
    __tablename__ = "lineup"
    __table_args__ = (
        UniqueConstraint("participant_id", "game_week_number", name="uq_lineup_participant_week"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    participant_id: Mapped[int] = mapped_column(
        ForeignKey("participant.id"), nullable=False, index=True
    )
    game_week_number: Mapped[int] = mapped_column(Integer, nullable=False)
    formation: Mapped[str] = mapped_column(String(8), nullable=False)
    live_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Spec 3.3: carried over from the previous game week because none was submitted.
    inherited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    slots: Mapped[list[LineupSlot]] = relationship(
        back_populates="lineup", cascade="all, delete-orphan"
    )
    tactical_subs: Mapped[list[TacticalSubRule]] = relationship(
        back_populates="lineup", cascade="all, delete-orphan"
    )


class LineupSlot(Base):
    __tablename__ = "lineup_slot"
    __table_args__ = (
        UniqueConstraint("lineup_id", "player_id", name="uq_slot_lineup_player"),
        CheckConstraint("role IN ('starter','bench')", name="ck_slot_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lineup_id: Mapped[int] = mapped_column(ForeignKey("lineup.id"), nullable=False, index=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("player.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(8), nullable=False)
    #: Bench order, which is the priority order for mandatory substitutions (spec 3.4).
    order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    lineup: Mapped[Lineup] = relationship(back_populates="slots")


class TacticalSubRule(Base):
    __tablename__ = "tactical_sub"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lineup_id: Mapped[int] = mapped_column(ForeignKey("lineup.id"), nullable=False, index=True)
    starter_id: Mapped[str] = mapped_column(ForeignKey("player.id"), nullable=False)
    sub_id: Mapped[str] = mapped_column(ForeignKey("player.id"), nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)

    lineup: Mapped[Lineup] = relationship(back_populates="tactical_subs")


class LiveSubRecord(Base):
    """A change made during the weekend in live mode (spec 3.4)."""

    __tablename__ = "live_sub"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lineup_id: Mapped[int] = mapped_column(ForeignKey("lineup.id"), nullable=False, index=True)
    starter_id: Mapped[str] = mapped_column(ForeignKey("player.id"), nullable=False)
    sub_id: Mapped[str] = mapped_column(ForeignKey("player.id"), nullable=False)
    order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    made_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BonusUsage(Base):
    __tablename__ = "bonus_usage"
    __table_args__ = (
        Index("ix_bonus_participant_week", "participant_id", "game_week_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participant.id"), nullable=False)
    game_week_number: Mapped[int] = mapped_column(Integer, nullable=False)
    bonus_type: Mapped[str] = mapped_column(String(24), nullable=False)
    target_player_id: Mapped[str | None] = mapped_column(ForeignKey("player.id"))


class LeagueMatch(Base):
    __tablename__ = "league_match"
    __table_args__ = (
        UniqueConstraint(
            "league_id", "game_week_number", "home_participant_id",
            name="uq_league_match_week_home",
        ),
        Index("ix_league_match_week", "league_id", "game_week_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("league.id"), nullable=False)
    game_week_number: Mapped[int] = mapped_column(Integer, nullable=False)
    home_participant_id: Mapped[int] = mapped_column(ForeignKey("participant.id"), nullable=False)
    away_participant_id: Mapped[int] = mapped_column(ForeignKey("participant.id"), nullable=False)
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Spec 4.5: a rating changed after this fixture was resolved. Never recompute
    #: silently; flag it and let the replay command do the work.
    needs_recompute: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: Full match report, so the result can be explained without recomputing it.
    report: Mapped[dict | None] = mapped_column(JSONType)
    is_playoff: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
