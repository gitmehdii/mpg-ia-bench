"""French rendering of a match result, shared by the CLI and the web screens.

The engine speaks in data; everything a player reads is worded here, once, so the
terminal report and the HTML report cannot drift apart. Code, comments and the README
stay in English -- only the output is French.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from mpg.engine.lines import Line
from mpg.engine.models import Duel, FinalPlayer, GoalEvent, TeamResult

# --------------------------------------------------------------------------- wording

LINE_LABEL: dict[Line, str] = {
    Line.G: "gardien",
    Line.D: "défense",
    Line.M: "milieu",
    Line.A: "attaque",
}

#: How a line is named when it is the one being crossed.
OPPOSING_LINE_LABEL: dict[Line, str] = {
    Line.G: "gardien",
    Line.D: "défense adverse",
    Line.M: "milieu adverse",
    Line.A: "attaque adverse",
}

LINE_INITIAL: dict[Line, str] = {Line.G: "G", Line.D: "D", Line.M: "M", Line.A: "A"}

SUBSTITUTION_LABEL: dict[str, str] = {
    "tactical": "tactique",
    "mandatory": "obligatoire",
    "live": "live",
}

SKIP_REASON_LABEL: dict[str, str] = {
    "goalkeeper": "un gardien ne marque jamais de but MPG",
    "already_scored": "a déjà marqué un but réel",
    "below_floor": "sous le plancher de 5,0",
}

BONUS_LABEL: dict[str, str] = {
    "defense": "Défense",
    "zahia": "Zahia",
    "mcdo": "McDo+",
    "mcdo_target": "McDo+",
    "captain": "Capitaine",
    "captain_target": "Capitaine",
    "valise": "Valise à Nanard",
    "suarez": "Suarez",
    "mirror": "Miroir",
    "cheat_code": "Cheat Code 18-26",
    "tonton_pat": "Tonton Pat'",
    "formation_424": "424",
}

#: The engine labels its adjustments with the MPG bonus names already; only this one
#: carries an English phrase, so it is reworded rather than parsed.
ADJUSTMENT_REWRITES: dict[str, str] = {
    "Capitaine lost (not cumulable with McDo+)": "Capitaine perdu (non cumulable avec McDo+)",
}

GOAL_KIND_LABEL: dict[str, str] = {
    "real": "but réel",
    "mpg": "but MPG",
    "own_goal": "CSC adverse",
    "rotaldo_own_goal": "CSC de punition (Rotaldos)",
}


# ------------------------------------------------------------------------ formatting


def number(value: float, decimals: int = 1) -> str:
    """A number the French way: decimal comma, fixed number of decimals.

    Rounds half away from zero, which is what a reader expects: a line average of
    3.625 reads as 3,63. Python's own formatting rounds half to even and would show
    3,62. This is display only -- the engine compares the exact values.
    """
    quantum = Decimal(1).scaleb(-decimals)
    rounded = Decimal(repr(value)).quantize(quantum, rounding=ROUND_HALF_UP)
    return f"{rounded:f}".replace(".", ",")


def rating(value: float) -> str:
    """A player's rating, to one decimal: 6,0."""
    return number(value, 1)


def average(value: float | None) -> str:
    """A line average, to two decimals: 3,63. Reads as a dash when the line is empty."""
    return "—" if value is None else number(value, 2)


def signed(value: float) -> str:
    """A bonus or a cost, always carrying its sign: +0,5 or -1,0."""
    return ("+" if value > 0 else "") + number(value, 1)


def adjustment(label: str) -> str:
    return ADJUSTMENT_REWRITES.get(label, label)


def adjustments(slot: FinalPlayer) -> list[str]:
    return [adjustment(label) for label in slot.adjustments]


def bonus_name(key: str) -> str:
    return BONUS_LABEL.get(key, key)


# ----------------------------------------------------------------------- the gauntlet


def duel_sentence(duel: Duel, *, is_last: bool) -> str:
    """One crossing, worded.

    `is_last` drops the decrement on the duel that ends the run: the engine does apply
    it, but there is nothing after the goalkeeper for it to matter to, and showing
    "-0,5 -> 3,5" under a goal that was just scored only confuses the reader.
    """
    target = OPPOSING_LINE_LABEL[duel.line]
    if duel.outcome == "free":
        return f"vs {target} : ligne vide, traversée sans duel"

    opponent = average(duel.opponent)
    if duel.outcome == "lost":
        return f"vs {target} {opponent} : échec"
    if duel.outcome == "lost_on_tie":
        return f"vs {target} {opponent} : égalité, avantage à l'adversaire, échec"

    verdict = "passe" if duel.outcome == "won" else "égalité, avantage au domicile"
    if is_last or duel.cost == 0:
        return f"vs {target} {opponent} : {verdict}"
    return (
        f"vs {target} {opponent} : {verdict}, {signed(-duel.cost)} "
        f"→ {rating(duel.rating_after)}"
    )


def gauntlet_lines(slot: FinalPlayer) -> list[str]:
    """The whole run, one sentence per duel."""
    last = len(slot.duels) - 1
    return [
        duel_sentence(duel, is_last=(index == last and slot.mpg_goal))
        for index, duel in enumerate(slot.duels)
    ]


def why_no_goal(slot: FinalPlayer) -> str | None:
    """Why this player scored no MPG goal, in one phrase."""
    if slot.mpg_goal:
        return None
    if slot.mpg_skip_reason:
        return SKIP_REASON_LABEL.get(slot.mpg_skip_reason, slot.mpg_skip_reason)
    for duel in slot.duels:
        if duel.outcome == "lost":
            return (
                f"arrêté par {OPPOSING_LINE_LABEL[duel.line]} "
                f"({average(duel.opponent)}) à {rating(duel.rating_before)}"
            )
        if duel.outcome == "lost_on_tie":
            return (
                f"égalité avec {OPPOSING_LINE_LABEL[duel.line]} "
                f"({average(duel.opponent)}), avantage à l'adversaire"
            )
    return None


def runners(team: TeamResult) -> list[FinalPlayer]:
    """Players who actually ran the gauntlet, scorers first."""
    ran = [slot for slot in team.final_xi if slot.duels]
    return sorted(ran, key=lambda slot: (not slot.mpg_goal,))


# ---------------------------------------------------------------------------- squad


def substitution_sentence(slot: FinalPlayer) -> str | None:
    """"Openda entre pour Ferran Torres (obligatoire)"."""
    if slot.replacement_kind is None or slot.replaced is None:
        return None
    kind = SUBSTITUTION_LABEL.get(slot.replacement_kind, slot.replacement_kind)
    sentence = f"{slot.name} entre pour {slot.replaced} ({kind})"
    if slot.line_penalty:
        skipped = abs(int(slot.line_penalty))
        sentence += f", {signed(slot.line_penalty)} pour {skipped} ligne"
        sentence += "s sautées" if skipped > 1 else " sautée"
    return sentence


def goal_sentence(event: GoalEvent) -> str:
    kind = GOAL_KIND_LABEL.get(event.kind, event.kind)
    who = event.name or "—"
    sentence = f"{who} : {kind}"
    if event.cancelled_by == "save":
        sentence += " — annulé par l'arrêt MPG"
    elif event.cancelled_by == "valise":
        sentence += " — annulé par la Valise à Nanard"
    return sentence


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """"1 but réel", "5 buts réels" -- French agrees from 2 upwards."""
    word = singular if abs(count) < 2 else (plural_form or singular + "s")
    return f"{count} {word}"


def score_breakdown(team: TeamResult) -> list[str]:
    """How the total was arrived at, line by line."""
    parts = [
        plural(team.real_goals, "but réel", "buts réels"),
        plural(team.mpg_goals, "but MPG", "buts MPG"),
    ]
    if team.rotaldo_own_goals_for:
        parts.append(
            plural(team.rotaldo_own_goals_for, "CSC de punition", "CSC de punition")
            + " (Rotaldos adverses)"
        )
    if team.saved:
        parts.append(f"-{team.saved} arrêt MPG")
    if team.valise_cancelled:
        parts.append(f"-{team.valise_cancelled} Valise à Nanard")
    return parts
