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


def run_ingestion() -> None:
    session = get_session_factory()()
    try:
        report = refresh_all(session)
        session.commit()
        log.info(
            "ingestion: %s players, %s new quotations, %s ratings, %s revised, %s fixtures flagged",
            report.players, report.quotations, report.performances,
            report.revised_performances, report.flagged_fixtures,
        )
    except Exception:
        session.rollback()
        log.exception("ingestion failed")
    finally:
        session.close()


def run_mercato_deadlines() -> None:
    """Resolve every round whose deadline has passed. Idempotent by `resolved_at`, so
    a double fire is harmless."""
    session = get_session_factory()()
    try:
        now = datetime.now(UTC)
        due = session.execute(
            select(MercatoRound).where(
                MercatoRound.resolved_at.is_(None), MercatoRound.deadline_at <= now
            )
        ).scalars().all()
        for round_ in due:
            if resolve_round(session, round_.id):
                log.info("mercato round %s of league %s resolved", round_.number, round_.league_id)
        # A league whose 7 days are up closes even if no round was due.
        leagues = session.execute(
            select(League).where(League.status == LeagueStatus.MERCATO)
        ).scalars().all()
        for league in leagues:
            current = session.execute(
                select(MercatoRound).where(
                    MercatoRound.league_id == league.id, MercatoRound.resolved_at.is_(None)
                ).order_by(MercatoRound.number)
            ).scalars().first()
            if current is not None and mercato_should_close(session, league, current):
                close_mercato(session, league)
                log.info("mercato of league %s closed", league.id)
        session.commit()
    except Exception:
        session.rollback()
        log.exception("mercato scheduling failed")
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
