"""A fixture laid out side by side, the way a results page reads.

The full report explains every duel; this one is the glance: the two teams facing each
other line by line, the averages that decided the virtual goals, and where the score
came from.
"""

from __future__ import annotations

from mpg import presentation as fr
from mpg.engine.lines import Line
from mpg.engine.models import FinalPlayer, MatchResult, TeamResult

WIDTH = 78
NAME = 22
SIDE = NAME + 9
LINES = ((Line.G, "GARDIEN"), (Line.D, "DÉFENSE"), (Line.M, "MILIEU"), (Line.A, "ATTAQUE"))


def team_average(team: TeamResult) -> float | None:
    """Mean rating of the whole final XI, goalkeeper included."""
    ratings = [slot.rating for slot in team.final_xi]
    return sum(ratings) / len(ratings) if ratings else None


def _markers(slot: FinalPlayer, captain: str | None) -> str:
    marks = []
    if slot.real_goals:
        marks.append("B" * slot.real_goals)
    if slot.mpg_goal:
        marks.append("V")
    if slot.own_goals:
        marks.append("csc")
    if slot.is_rotaldo:
        marks.append("R")
    if slot.replacement_kind:
        marks.append({"tactical": "t", "mandatory": "o", "live": "l"}[slot.replacement_kind])
    if captain and slot.player_id == captain:
        marks.append("C")
    return ("[" + "".join(marks) + "]") if marks else ""


def _cell(slot: FinalPlayer | None, captain: str | None, *, right: bool) -> str:
    if slot is None:
        return " " * SIDE
    name = ("Rotaldo" if slot.is_rotaldo else slot.name)[:NAME]
    mark = _markers(slot, captain)
    rating = fr.rating(slot.rating)
    if right:
        return f"{rating:>4} {mark:<4} {name:<{NAME}}"
    return f"{name:>{NAME}} {mark:>4} {rating:>4}"


def render_recap(
    result: MatchResult,
    *,
    home_name: str = "",
    away_name: str = "",
    home_captain: str | None = None,
    away_captain: str | None = None,
    home_bonuses: list[str] | None = None,
    away_bonuses: list[str] | None = None,
) -> str:
    home, away = result.home, result.away
    home_name = home_name or str(home.participant_id)
    away_name = away_name or str(away.participant_id)

    out = ["═" * WIDTH]
    score = f"{home.score} - {away.score}"
    out.append(f"  JOURNÉE {result.context.game_week}".ljust(WIDTH - len(score) - 2) + score)
    out.append(f"  {home_name[:30]:<30}{'':>14}{away_name[:30]:>30}")

    home_avg, away_avg = team_average(home), team_average(away)
    left = f"moyenne {fr.average(home_avg)}" if home_avg is not None else ""
    right = f"moyenne {fr.average(away_avg)}" if away_avg is not None else ""
    out.append(f"  {left:<30}{'':>14}{right:>30}")
    out.append("═" * WIDTH)

    for line, label in LINES:
        home_slots = [slot for slot in home.final_xi if slot.slot_line is line]
        away_slots = [slot for slot in away.final_xi if slot.slot_line is line]
        if not home_slots and not away_slots:
            continue
        home_line = fr.average(home.line_averages.get(line))
        away_line = fr.average(away.line_averages.get(line))
        header = f" {label} "
        out.append("")
        out.append(f"{header:─^{WIDTH - 16}}  {home_line:>5} │ {away_line:<5}")
        for index in range(max(len(home_slots), len(away_slots))):
            left_cell = _cell(home_slots[index] if index < len(home_slots) else None,
                              home_captain, right=False)
            right_cell = _cell(away_slots[index] if index < len(away_slots) else None,
                               away_captain, right=True)
            out.append(f"  {left_cell} │ {right_cell}")

    out.append("")
    out.append("─" * WIDTH)
    for team, name, bonuses in (
        (home, home_name, home_bonuses or []), (away, away_name, away_bonuses or []),
    ):
        detail = " · ".join(fr.score_breakdown(team))
        out.append(f"  {name[:24]:<24} {team.score:>2}   {detail}")
        if bonuses:
            out.append(f"  {'':<24}      bonus : {', '.join(bonuses)}")
        scorers = [
            f"{slot.name}{' (MPG)' if slot.mpg_goal else ''}"
            for slot in team.final_xi
            if slot.real_goals or slot.mpg_goal
        ]
        if scorers:
            out.append(f"  {'':<24}      buteurs : {', '.join(scorers)}")
        if team.rotaldo_count:
            out.append(
                f"  {'':<24}      {fr.plural(team.rotaldo_count, 'Rotaldo', 'Rotaldos')} "
                f"(poste vide)"
            )
    out.append("")
    out.append("  [B] but réel   [V] but MPG   [C] capitaine   [R] Rotaldo")
    out.append("  [o] entré (obligatoire)   [t] (tactique)   [l] (live)   [csc] contre son camp")
    return "\n".join(line.rstrip() for line in out)
