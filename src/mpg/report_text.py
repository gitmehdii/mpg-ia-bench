"""The match report as plain text, for the terminal.

Shares its wording with the web screens through `mpg.presentation`, so the two cannot
say different things about the same match.
"""

from __future__ import annotations

from mpg import presentation as fr
from mpg.engine.lines import Line
from mpg.engine.models import MatchResult, TeamResult

WIDTH = 78
LINE_ORDER = (Line.G, Line.D, Line.M, Line.A)


def _rule(char: str = "=") -> str:
    return char * WIDTH


def _header(result: MatchResult) -> list[str]:
    home, away = result.home, result.away
    title = f"{home.participant_id}  {home.score} - {away.score}  {away.participant_id}"
    return [
        _rule(),
        f" Journée {result.context.game_week}".ljust(WIDTH),
        f" {title}".ljust(WIDTH),
        _rule(),
    ]


def _squad(team: TeamResult) -> list[str]:
    out = ["", f"{team.participant_id}", _rule("-")]
    for line in LINE_ORDER:
        for slot in [s for s in team.final_xi if s.slot_line is line]:
            marks = []
            if slot.real_goals:
                marks.append(fr.plural(slot.real_goals, "but réel", "buts réels"))
            if slot.own_goals:
                marks.append(fr.plural(slot.own_goals, "CSC", "CSC"))
            if slot.mpg_goal:
                marks.append("BUT MPG")
            if slot.is_rotaldo:
                marks.append("Rotaldo")
            detail = fr.adjustments(slot)
            suffix = ""
            if marks:
                suffix += "  " + ", ".join(marks)
            if detail:
                suffix += f"   [{', '.join(detail)}]"
            name = "Rotaldo" if slot.is_rotaldo else slot.name
            out.append(
                f"  {fr.LINE_INITIAL[slot.slot_line]}  {name[:24]:24} "
                f"{fr.rating(slot.rating):>5}{suffix}"
            )
    averages = "  ".join(
        f"{fr.LINE_LABEL[line]} {fr.average(team.line_averages.get(line))}"
        for line in (Line.A, Line.M, Line.D, Line.G)
    )
    out.append(f"  Moyennes de ligne : {averages}")
    return out


def _substitutions(team: TeamResult) -> list[str]:
    sentences = [
        fr.substitution_sentence(slot)
        for slot in team.final_xi
        if slot.replacement_kind is not None
    ]
    sentences = [s for s in sentences if s]
    rotaldos = [slot for slot in team.final_xi if slot.is_rotaldo]
    if rotaldos:
        sentences.append(
            f"{fr.plural(len(rotaldos), 'Rotaldo', 'Rotaldos')} comblent un poste vide"
        )
    if not sentences:
        return []
    return ["", "  Remplacements"] + [f"    {s}" for s in sentences]


def _gauntlets(team: TeamResult) -> list[str]:
    runners = fr.runners(team)
    if not runners:
        return []
    out = ["", "  Buts MPG, duel par duel"]
    for slot in runners:
        out.append(
            f"    {slot.name} ({fr.LINE_INITIAL[slot.slot_line]}) {fr.rating(slot.rating)}"
        )
        for sentence in fr.gauntlet_lines(slot):
            out.append(f"      {sentence}")
        if slot.mpg_goal:
            out.append("      BUT MPG")
        else:
            why = fr.why_no_goal(slot)
            out.append(f"      pas de but : {why}" if why else "      pas de but")
        out.append("")
    return out


def _outcome(team: TeamResult, opponent: TeamResult) -> list[str]:
    out = ["", f"  Score : {team.score}  ({' | '.join(fr.score_breakdown(team))})"]
    keeper = next(
        (s.rating for s in opponent.final_xi if s.slot_line is Line.G), None
    )
    if team.saved and keeper is not None:
        out.append(
            f"    Arrêt MPG du gardien adverse ({fr.rating(keeper)}) : "
            f"{fr.plural(team.saved, 'but réel annulé', 'buts réels annulés')}"
        )
    cancelled = [e for e in team.goals if e.cancelled_by]
    for event in cancelled:
        out.append(f"    {fr.goal_sentence(event)}")
    return out


def render_match_report(result: MatchResult) -> str:
    """The whole fixture, explained."""
    lines = _header(result)
    for team, opponent in ((result.home, result.away), (result.away, result.home)):
        lines += _squad(team)
        lines += _substitutions(team)
        lines += _gauntlets(team)
        lines += _outcome(team, opponent)
        lines.append("")
    return "\n".join(lines)
