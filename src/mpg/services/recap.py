"""Rendering the recap of a stored fixture."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from mpg import presentation as fr
from mpg.db.models import BonusUsage, LeagueMatch, Participant
from mpg.match_recap import render_recap
from mpg.report_view import rehydrate


def recap_of(session: Session, fixture: LeagueMatch) -> str:
    """The side-by-side recap of a resolved fixture, read back from its report."""
    if not fixture.report:
        return "  (cette journée n\u2019a pas encore été résolue)"

    result = rehydrate(fixture.report)
    names = {
        row.id: row.team_name
        for row in session.execute(
            select(Participant).where(
                Participant.id.in_(
                    [fixture.home_participant_id, fixture.away_participant_id]
                )
            )
        ).scalars()
    }

    def bonuses(participant_id: int) -> tuple[list[str], str | None]:
        rows = session.execute(
            select(BonusUsage).where(
                BonusUsage.participant_id == participant_id,
                BonusUsage.game_week_number == fixture.game_week_number,
            )
        ).scalars().all()
        captain = next((r.target_player_id for r in rows if r.bonus_type == "captain"), None)
        labels = []
        for row in rows:
            label = fr.bonus_name(row.bonus_type)
            if row.target_player_id and row.bonus_type in ("captain", "mcdo"):
                label += " sur " + (row.target_player_id.rsplit("_", 1)[-1])
            labels.append(label)
        return labels, captain

    home_bonuses, home_captain = bonuses(fixture.home_participant_id)
    away_bonuses, away_captain = bonuses(fixture.away_participant_id)

    return render_recap(
        result,
        home_name=names.get(fixture.home_participant_id, ""),
        away_name=names.get(fixture.away_participant_id, ""),
        home_captain=home_captain, away_captain=away_captain,
        home_bonuses=home_bonuses, away_bonuses=away_bonuses,
    )


def recap_game_week(session: Session, league_id: int, game_week: int) -> str:
    """Every fixture of a game week, one after the other."""
    fixtures = session.execute(
        select(LeagueMatch).where(
            LeagueMatch.league_id == league_id,
            LeagueMatch.game_week_number == game_week,
        ).order_by(LeagueMatch.id)
    ).scalars().all()
    if not fixtures:
        return f"  (aucun match pour la journée {game_week})"
    return "\n\n".join(recap_of(session, fixture) for fixture in fixtures)
