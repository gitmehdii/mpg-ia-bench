"""An in-memory league of 4, with a Ligue 1 player pool, for the mercato scenarios."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from mpg.config import MERCATO_BUDGET
from mpg.db.models import (
    Base,
    Championship,
    Club,
    League,
    LeagueStatus,
    MercatoRound,
    Participant,
    Player,
    User,
)

GK, CB, FB, DM, AM, FW = 10, 20, 21, 30, 31, 40

#: The players the acceptance scenarios name, with their game-week-5 quotations.
NAMED_PLAYERS: list[tuple[str, int, int]] = [
    ("Tolisso", AM, 30),
    ("Doué", AM, 19),
    ("Dembélé", FW, 34),
    ("Openda", FW, 26),
    ("Marquinhos", CB, 11),
    ("Greif", GK, 13),
    ("Nuamah", FW, 22),
    ("Vitinha", DM, 28),
    ("Nuno Mendes", FB, 21),
    ("Pacho", CB, 14),
]


def make_session() -> Session:
    """A fresh in-memory database. The schema is dialect-portable on purpose."""
    engine = create_engine("sqlite://", future=True)

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()


def seed_pool(session: Session, championship_id: int = 1) -> None:
    """The named players plus a deep synthetic pool, so the quota is always reachable."""
    session.add(
        Championship(id=championship_id, code="fr_l1", name="Ligue 1", current_season=2026)
    )
    session.add(Club(id="club_1", name="Test FC", championship_id=championship_id))

    for name, ultra, quotation in NAMED_PLAYERS:
        session.add(
            Player(
                id=name,
                last_name=name,
                ultra_position=ultra,
                club_id="club_1",
                championship_id=championship_id,
                quotation=quotation,
            )
        )

    # 30 players per fine position: 4 participants need 2/6/6/4 each, and the
    # end-of-mercato draft has to find cheap ones left over.
    for ultra in (GK, CB, FB, DM, AM, FW):
        for index in range(1, 31):
            pid = f"pool_{ultra}_{index:02d}"
            session.add(
                Player(
                    id=pid,
                    last_name=pid,
                    ultra_position=ultra,
                    club_id="club_1",
                    championship_id=championship_id,
                    quotation=index,          # quotations 1..30, so the cheapest is 1
                )
            )
    session.flush()


def make_league(
    session: Session,
    *,
    size: int = 4,
    seed: int = 42,
    return_legs: bool = True,
    with_pool: bool = True,
    code: str | None = None,
    deadline_at: datetime | None = None,
) -> tuple[League, list[Participant], MercatoRound]:
    """A league in mercato, one round open, with a fixed seed for reproducibility.

    `with_pool=False` skips seeding the player pool, for when several leagues share one.
    """
    if with_pool:
        seed_pool(session)
    league = League(
        name="Test League",
        code=code or f"TEST{seed}",
        championship_id=1,
        size=size,
        return_legs=return_legs,
        status=LeagueStatus.MERCATO,
        mercato_opens_at=datetime(2026, 9, 21, 12, 0, tzinfo=UTC),
    )
    session.add(league)
    session.flush()

    participants: list[Participant] = []
    for index, label in enumerate("ABCDEFGHIJ"[:size]):
        user = User(
            email=f"{label.lower()}-{code or seed}@example.com",
            display_name=label,
            password_hash="x",
        )
        session.add(user)
        session.flush()
        participant = Participant(
            league_id=league.id,
            user_id=user.id,
            team_name=f"Team {label}",
            budget=MERCATO_BUDGET,
            is_admin=index == 0,
        )
        session.add(participant)
        participants.append(participant)
    session.flush()

    round_ = MercatoRound(
        league_id=league.id,
        number=1,
        opens_at=league.mercato_opens_at,
        deadline_at=deadline_at or datetime(2026, 9, 23, 12, 0, tzinfo=UTC),
        seed=seed,
    )
    session.add(round_)
    session.flush()
    session.refresh(league)
    return league, participants, round_
