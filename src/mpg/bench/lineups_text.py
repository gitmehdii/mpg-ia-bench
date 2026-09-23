"""The lineups of a game week, side by side, in the terminal.

Reads what each manager -- model or human -- actually submitted, next to what the
players went on to do. It is the view that explains a benchmark table: a squad bought
badly shows up here long before it shows up in the points.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from mpg import presentation as fr
from mpg.db.models import (
    BonusUsage,
    Lineup,
    Participant,
    Performance,
    Player,
)
from mpg.engine.lines import LINE_ORDER, line_of

WIDTH = 78


def render_lineups(session: Session, league_id: int, game_week: int) -> str:
    participants = session.execute(
        select(Participant).where(Participant.league_id == league_id).order_by(Participant.id)
    ).scalars().all()

    out: list[str] = []
    for participant in participants:
        lineup = session.execute(
            select(Lineup).where(
                Lineup.participant_id == participant.id,
                Lineup.game_week_number == game_week,
            )
        ).scalar_one_or_none()

        out += [
            "=" * WIDTH,
            f"  {participant.team_name}   —   {lineup.formation if lineup else 'aucune compo'}"
            f"   —   budget restant {participant.budget} M€",
            "=" * WIDTH,
        ]
        if lineup is None:
            out += ["  (aucune composition enregistrée)", ""]
            continue

        player_ids = [slot.player_id for slot in lineup.slots]
        players = {
            row.id: row
            for row in session.execute(select(Player).where(Player.id.in_(player_ids))).scalars()
        }
        current = {
            row.player_id: row
            for row in session.execute(
                select(Performance).where(
                    Performance.player_id.in_(player_ids),
                    Performance.game_week_number == game_week,
                )
            ).scalars()
        }
        earlier: dict[str, list[float]] = {}
        for row in session.execute(
            select(Performance).where(
                Performance.player_id.in_(player_ids),
                Performance.game_week_number < game_week,
                Performance.rating.is_not(None),
            ).order_by(Performance.game_week_number)
        ).scalars():
            earlier.setdefault(row.player_id, []).append(row.rating)

        bonuses = session.execute(
            select(BonusUsage).where(
                BonusUsage.participant_id == participant.id,
                BonusUsage.game_week_number == game_week,
            )
        ).scalars().all()
        captain = next((b.target_player_id for b in bonuses if b.bonus_type == "captain"), None)
        posed = ", ".join(fr.bonus_name(b.bonus_type) for b in bonuses)
        if posed:
            out.append(f"  Bonus : {posed}")

        for role, label in (("starter", "TITULAIRES"), ("bench", "BANC")):
            out.append(f"  {label}")
            slots = [slot for slot in lineup.slots if slot.role == role]
            slots.sort(
                key=lambda slot: (
                    LINE_ORDER.index(line_of(players[slot.player_id].ultra_position)),
                    slot.order,
                )
            )
            for slot in slots:
                player = players[slot.player_id]
                line = line_of(player.ultra_position)
                performance = current.get(player.id)
                rating = (
                    fr.rating(performance.rating)
                    if performance and performance.rating is not None
                    else "—"
                )
                goals = (
                    f"  {fr.plural(performance.goals_scored, 'but', 'buts')}"
                    if performance and performance.goals_scored
                    else ""
                )
                form = earlier.get(player.id)
                shown = (
                    "forme " + "/".join(fr.rating(value) for value in form[-3:])
                    if form
                    else "pas de forme"
                )
                mark = "  (C)" if player.id == captain else ""
                out.append(
                    f"    {line}  {player.display_name[:26]:26} cote {player.quotation or 0:>2}"
                    f"  {shown:<18} J{game_week} {rating:>4}{goals}{mark}"
                )

        absent = sum(
            1 for slot in lineup.slots
            if slot.role == "starter"
            and (slot.player_id not in current or current[slot.player_id].rating is None)
        )
        if absent:
            out.append(
                f"  {fr.plural(absent, 'titulaire n’a pas joué', 'titulaires n’ont pas joué')}"
            )
        out.append("")
    return "\n".join(out)
