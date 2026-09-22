"""Two resolutions of the same round, racing (spec 4.4.3).

This is the one requirement the rest of the suite cannot check. Everything else runs
on SQLite in memory, and SQLite drops `FOR UPDATE` silently:

    Postgres : SELECT ... FROM league WHERE league.id = %(id_1)s FOR UPDATE
    SQLite   : SELECT ... FROM league WHERE league.id = ?

So the lock the scheduler relies on is a no-op under the default suite. The
idempotence test covers sequential re-entry; only this one covers two transactions
arriving together, which is what the cron firing at the deadline and a last
participant validating in the same millisecond actually look like.

Needs a real PostgreSQL. Point `MPG_TEST_DATABASE_URL` at one and run `pytest -m db`.
"""

from __future__ import annotations

import os
import threading

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from fixtures.league import make_league
from mpg.db.base import Base
from mpg.db.models import Bid, MercatoRound, Participant, Roster
from mpg.mercato.service import place_bid, resolve_round, validate_round

DB_URL = os.environ.get("MPG_TEST_DATABASE_URL")


def _with_driver(url: str) -> str:
    """Accept a bare `postgresql://` URL and point it at the driver the project uses."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


@pytest.fixture
def pg_engine():
    if not DB_URL:
        pytest.skip("MPG_TEST_DATABASE_URL is not set; see the README for the db suite")
    engine = create_engine(_with_driver(DB_URL), future=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def seeded(pg_engine):
    """A league of 4 with bids on the table and everyone validated, committed."""
    factory = sessionmaker(bind=pg_engine, expire_on_commit=False, future=True)
    session = factory()
    league, participants, round_ = make_league(session, size=4)
    a, b, c, d = participants

    place_bid(session, a, round_, "Tolisso", 45)
    place_bid(session, b, round_, "Greif", 20)
    place_bid(session, c, round_, "Doué", 25)
    for participant in participants:
        validate_round(session, participant, round_)
    session.commit()

    ids = {
        "round": round_.id,
        "league": league.id,
        "participants": [p.id for p in participants],
    }
    session.close()
    return factory, ids


def _is_serialisation_failure(error: BaseException) -> bool:
    """A genuine concurrency-control refusal, as opposed to corrupted work.

    PostgreSQL raising `could not serialize access` or `deadlock detected` means the
    loser was stopped *before* doing damage, which is a perfectly good outcome. A
    unique-constraint violation is a different animal: it means both transactions got
    into the critical section and only the constraint kept the data straight. That is
    exactly what the lock exists to prevent, so it has to read as a failure here.
    """
    original = getattr(error, "orig", None)
    name = type(original).__name__ if original is not None else type(error).__name__
    return name in {"SerializationFailure", "DeadlockDetected", "LockNotAvailable"}


def _race(factory, round_id: int) -> dict[str, object]:
    """Fire two resolutions of the same round from two sessions, at once."""
    start = threading.Barrier(2)
    results: dict[str, object] = {}

    def worker(tag: str) -> None:
        session = factory()
        try:
            start.wait(timeout=10)
            results[tag] = resolve_round(session, round_id)
            session.commit()
        except Exception as exc:
            session.rollback()
            results[tag] = exc
        finally:
            session.close()

    threads = [threading.Thread(target=worker, args=(tag,)) for tag in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads), "a worker deadlocked"
    return results


@pytest.mark.db
def test_two_concurrent_resolutions_apply_only_one(seeded):
    factory, ids = seeded

    results = _race(factory, ids["round"])

    applied = [value for value in results.values() if value is True]
    declined = [value for value in results.values() if value is False]
    errors = [value for value in results.values() if isinstance(value, Exception)]

    # Exactly one resolution goes through.
    assert len(applied) == 1, results
    assert len(declined) + len(errors) == 1, results

    # The loser must have been turned away cleanly -- by reading `resolved_at` behind
    # the lock, or by a serialisation failure. Anything else means it ran the
    # resolution too and was only stopped by a constraint downstream.
    for error in errors:
        assert _is_serialisation_failure(error), (
            "the second resolution was not serialised, it ran and was caught by a "
            f"constraint instead: {type(error).__name__}: {error}"
        )


@pytest.mark.db
def test_the_race_leaves_exactly_one_set_of_awards(seeded):
    factory, ids = seeded
    _race(factory, ids["round"])

    session = factory()
    try:
        # No player is awarded twice, and no participant holds a duplicate.
        rosters = session.execute(select(Roster)).scalars().all()
        player_ids = [row.player_id for row in rosters]
        assert sorted(player_ids) == ["Doué", "Greif", "Tolisso"]
        assert len(player_ids) == len(set(player_ids))

        # Budgets are debited once, not twice.
        budgets = {
            row.id: row.budget
            for row in session.execute(
                select(Participant).where(Participant.league_id == ids["league"])
            ).scalars()
        }
        a, b, c, d = ids["participants"]
        assert budgets[a] == 455          # 500 - 45, debited a single time
        assert budgets[b] == 480          # 500 - 20
        assert budgets[c] == 475          # 500 - 25
        assert budgets[d] == 500          # never bid

        # The round is resolved once and exactly one successor was created.
        rounds = session.execute(
            select(MercatoRound).where(MercatoRound.league_id == ids["league"])
            .order_by(MercatoRound.number)
        ).scalars().all()
        assert len(rounds) == 2, [r.number for r in rounds]
        assert rounds[0].resolved_at is not None
        assert rounds[1].resolved_at is None

        # Every bid was marked exactly once.
        assert session.execute(
            select(func.count(Bid.id)).where(Bid.round_id == ids["round"], Bid.won.is_(None))
        ).scalar_one() == 0
    finally:
        session.close()


def test_the_lock_is_emitted_on_postgresql_and_dropped_on_sqlite():
    """Guards the premise of this whole file, and runs in the fast suite on purpose.

    If `FOR UPDATE` ever stopped being emitted, the racing tests above would still
    pass by luck rather than by locking -- and on SQLite nobody would ever notice,
    because SQLite accepts the query and silently drops the clause.
    """
    from sqlalchemy.dialects import postgresql, sqlite

    from mpg.db.models import League

    statement = select(League).where(League.id == 1).with_for_update()
    assert "FOR UPDATE" in str(statement.compile(dialect=postgresql.dialect()))
    # Documented, not aspirational: this is why the racing tests need a real server.
    assert "FOR UPDATE" not in str(statement.compile(dialect=sqlite.dialect()))
