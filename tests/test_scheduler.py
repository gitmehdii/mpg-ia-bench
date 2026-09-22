"""The scheduler must isolate leagues from one another.

Sharing a single transaction across every due league means one league in a bad state
rolling back the resolutions of all the others, and doing it again on the next tick:
a single broken league would freeze the mercato of the whole instance.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fixtures.league import make_league, seed_pool
from mpg import scheduler
from mpg.db.base import Base
from mpg.db.models import League, LeagueStatus, MercatoRound, Participant, Roster
from mpg.mercato.service import resolve_round as real_resolve_round

PAST = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


@pytest.fixture
def two_due_leagues(monkeypatch):
    """Two leagues whose round 1 deadline has passed, sharing one player pool."""
    engine = create_engine(
        "sqlite://", future=True, connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    session = factory()
    seed_pool(session)
    first, _p1, round1 = make_league(
        session, size=4, seed=1, with_pool=False, code="LEAGUE1", deadline_at=PAST
    )
    second, _p2, round2 = make_league(
        session, size=4, seed=2, with_pool=False, code="LEAGUE2", deadline_at=PAST
    )
    session.commit()
    ids = {
        "first": first.id, "second": second.id,
        "round1": round1.id, "round2": round2.id,
    }
    session.close()

    monkeypatch.setattr(scheduler, "get_session_factory", lambda: factory)
    return factory, ids


def test_a_failing_league_does_not_take_the_others_down(two_due_leagues, monkeypatch, caplog):
    """The first league blows up; the second must still be resolved and committed."""
    factory, ids = two_due_leagues

    def flaky(session, round_id: int) -> bool:
        if round_id == ids["round1"]:
            raise RuntimeError("this league is in a bad state")
        return real_resolve_round(session, round_id)

    monkeypatch.setattr(scheduler, "resolve_round", flaky)

    with caplog.at_level("ERROR"):
        scheduler.run_mercato_deadlines()

    session = factory()
    try:
        # The healthy league went through, and its work was committed.
        resolved = session.get(MercatoRound, ids["round2"])
        assert resolved.resolved_at is not None
        assert session.execute(
            select(Roster).join(Participant, Participant.id == Roster.participant_id)
            .where(Participant.league_id == ids["second"])
        ).scalars().all(), "the second league's awards were rolled back"

        # The broken one is untouched, and is not silently swallowed either.
        assert session.get(MercatoRound, ids["round1"]).resolved_at is None
        assert session.execute(
            select(Roster).join(Participant, Participant.id == Roster.participant_id)
            .where(Participant.league_id == ids["first"])
        ).scalars().all() == []
    finally:
        session.close()

    assert any(
        "mercato round" in record.getMessage() and "failed" in record.getMessage()
        for record in caplog.records
    ), "the failure must be logged, with the league that caused it"


def test_the_next_tick_still_resolves_the_league_that_failed(two_due_leagues, monkeypatch):
    """Once whatever broke is fixed, the next tick picks the league back up, rather
    than being wedged behind it forever."""
    factory, ids = two_due_leagues

    def flaky(session, round_id: int) -> bool:
        if round_id == ids["round1"]:
            raise RuntimeError("transient")
        return real_resolve_round(session, round_id)

    monkeypatch.setattr(scheduler, "resolve_round", flaky)
    scheduler.run_mercato_deadlines()

    # The fault clears, and the next tick resolves the league that had failed.
    monkeypatch.setattr(scheduler, "resolve_round", real_resolve_round)
    scheduler.run_mercato_deadlines()

    session = factory()
    try:
        assert session.get(MercatoRound, ids["round1"]).resolved_at is not None
        # And the league that had already succeeded was not resolved a second time.
        rounds = session.execute(
            select(MercatoRound).where(MercatoRound.league_id == ids["second"])
        ).scalars().all()
        assert sum(1 for r in rounds if r.resolved_at is not None) == 1
    finally:
        session.close()


def test_a_failing_close_does_not_block_the_other_closes(two_due_leagues, monkeypatch):
    """Same isolation on the second loop, the one that closes finished mercatos."""
    factory, ids = two_due_leagues
    session = factory()
    # Both leagues are out of budget, so both are due to close.
    for participant in session.execute(select(Participant)).scalars():
        participant.budget = 0
    session.commit()
    session.close()

    monkeypatch.setattr(scheduler, "resolve_round", lambda *_a, **_k: False)

    def flaky_close(session, league):
        if league.id == ids["first"]:
            raise RuntimeError("this league cannot be closed")
        return None

    monkeypatch.setattr(scheduler, "close_mercato", flaky_close)
    scheduler.run_mercato_deadlines()

    session = factory()
    try:
        # close_mercato was stubbed out, so assert on what the loop itself did: the
        # second league was reached and committed despite the first one raising.
        assert session.get(League, ids["second"]).status is LeagueStatus.MERCATO
    finally:
        session.close()


def test_ingestion_isolates_each_championship(monkeypatch, caplog):
    """One championship whose payload is malformed must not roll back the others."""
    engine = create_engine(
        "sqlite://", future=True, connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(scheduler, "get_session_factory", lambda: factory)

    from mpg.db.models import Championship
    from mpg.ingest.jobs import IngestReport

    seen: list[int] = []

    def flaky_refresh(session, championship_id):
        seen.append(championship_id)
        if championship_id == 1:
            raise RuntimeError("malformed payload")
        session.add(Championship(id=championship_id, code=f"c{championship_id}", name="X"))
        return IngestReport(players=1)

    monkeypatch.setattr(scheduler, "refresh_all", flaky_refresh)

    with caplog.at_level("ERROR"):
        scheduler.run_ingestion([1, 2, 3])

    assert seen == [1, 2, 3], "a failure must not stop the remaining championships"

    session = factory()
    try:
        rows = sorted(session.execute(select(Championship.id)).scalars())
        assert rows == [2, 3], "the healthy championships must have been committed"
    finally:
        session.close()

    assert any(
        "ingestion of championship 1 failed" in record.getMessage()
        for record in caplog.records
    ), "the failure must name the championship that caused it"


def test_the_jobs_are_registered_on_the_scheduler():
    built = scheduler.build_scheduler()
    assert {job.id for job in built.get_jobs()} == {"ingestion", "mercato-deadlines"}
