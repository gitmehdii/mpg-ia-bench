"""Scheduled work: ingestion, mercato deadlines, game week resolution (spec 1.6, 4.4.3).

Everything here only ever calls a service function. The scheduler decides *when*,
never *what*, which keeps the rules testable without a clock.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from mpg.config import get_settings
from mpg.db.models import League, LeagueStatus, MercatoRound
from mpg.db.session import get_session_factory
from mpg.ingest.jobs import refresh_all
from mpg.mercato.service import close_mercato, mercato_should_close, resolve_round

log = logging.getLogger(__name__)


def run_ingestion(championship_ids: list[int] | None = None) -> None:
    """Refresh each championship in its own transaction.

    One championship whose payload is malformed must not roll back the ingestion of
    the others, and must not keep rolling them back on every tick afterwards.
    """
    settings = get_settings()
    championship_ids = championship_ids or [settings.default_championship]
    session = get_session_factory()()
    try:
        for championship_id in championship_ids:
            try:
                report = refresh_all(session, championship_id)
                session.commit()
                log.info(
                    "ingestion of championship %s: %s players, %s new quotations, "
                    "%s ratings, %s revised, %s fixtures flagged",
                    championship_id, report.players, report.quotations,
                    report.performances, report.revised_performances,
                    report.flagged_fixtures,
                )
            except Exception:
                session.rollback()
                log.exception("ingestion of championship %s failed", championship_id)
    finally:
        session.close()


def run_mercato_deadlines() -> None:
    """Resolve every round whose deadline has passed, one league at a time.

    Each league gets its own try and its own commit. Sharing a single transaction
    would mean one league in a bad state rolling back every other league resolved in
    the same tick, and doing it again on the next tick, and the one after -- a single
    broken league would freeze the mercato of the whole instance indefinitely.

    Idempotent by `resolved_at`, so a double fire is harmless.
    """
    session = get_session_factory()()
    try:
        now = datetime.now(UTC)
        # Read the identifiers up front: the ORM objects would be expired by the
        # rollbacks between iterations.
        due = [
            (row.id, row.number, row.league_id)
            for row in session.execute(
                select(MercatoRound).where(
                    MercatoRound.resolved_at.is_(None), MercatoRound.deadline_at <= now
                )
            ).scalars()
        ]
        for round_id, number, league_id in due:
            try:
                if resolve_round(session, round_id):
                    session.commit()
                    log.info("mercato round %s of league %s resolved", number, league_id)
                else:
                    session.rollback()
            except Exception:
                session.rollback()
                log.exception(
                    "league %s: mercato round %s failed", league_id, number
                )

        # A league whose 7 days are up closes even if no round was due.
        league_ids = list(
            session.execute(
                select(League.id).where(League.status == LeagueStatus.MERCATO)
            ).scalars()
        )
        for league_id in league_ids:
            try:
                league = session.get(League, league_id)
                if league is None:
                    continue
                current = session.execute(
                    select(MercatoRound).where(
                        MercatoRound.league_id == league.id,
                        MercatoRound.resolved_at.is_(None),
                    ).order_by(MercatoRound.number)
                ).scalars().first()
                if current is not None and mercato_should_close(session, league, current):
                    close_mercato(session, league)
                    session.commit()
                    log.info("mercato of league %s closed", league.id)
                else:
                    session.rollback()
            except Exception:
                session.rollback()
                log.exception("league %s: closing the mercato failed", league_id)
    finally:
        session.close()


def build_scheduler() -> BackgroundScheduler:
    settings = get_settings()
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_ingestion,
        IntervalTrigger(seconds=settings.ingest_interval_seconds),
        id="ingestion",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_mercato_deadlines,
        IntervalTrigger(minutes=1),
        id="mercato-deadlines",
        max_instances=1,
        coalesce=True,
    )
    return scheduler


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    scheduler = build_scheduler()
    scheduler.start()
    log.info("scheduler started; ctrl-c to stop")
    try:
        import time

        while True:
            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == "__main__":
    main()
