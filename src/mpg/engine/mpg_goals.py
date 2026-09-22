"""Virtual ("MPG") goals: every field player crosses the opposing lines (spec 3.6, 4.2).

Ratings and bonuses are all multiples of 0.5, and line averages divide a sum of such
multiples by at most 5, so every value a tie can occur on is exactly representable in
binary floating point. Comparisons are therefore exact on purpose -- an epsilon here
would turn genuine near-misses into ties and break the twelfth-man rule.
"""

from __future__ import annotations

from mpg.engine.lines import GAUNTLET, MPG_GOAL_FLOOR, Line
from mpg.engine.models import Duel, FinalPlayer

#: Crossing cost: 1 point after the first duel won, then 0.5 per duel after that.
FIRST_DUEL_COST = 1.0
NEXT_DUEL_COST = 0.5


def line_averages(final_xi: list[FinalPlayer]) -> dict[Line, float | None]:
    """Mean rating per line of a final XI (invariant 4.1.7), None for an empty line.

    An empty line is crossed without a duel and without a decrement (invariant 4.1.8).
    """
    averages: dict[Line, float | None] = {}
    for line in (Line.A, Line.M, Line.D):
        ratings = [slot.rating for slot in final_xi if slot.slot_line is line]
        averages[line] = sum(ratings) / len(ratings) if ratings else None
    keepers = [slot.rating for slot in final_xi if slot.slot_line is Line.G]
    averages[Line.G] = keepers[0] if keepers else None
    return averages


def mpg_goals(
    attacking: list[FinalPlayer],
    defending: list[FinalPlayer],
    *,
    at_home: bool,
    attacker_wins_ties: bool,
) -> tuple[int, dict[Line, float | None]]:
    """Count the virtual goals `attacking` scores, flagging the scorers in place.

    `attacker_wins_ties` is True in a league without return legs or in a play-off;
    otherwise a tied duel goes to the home side (golden rule 1).
    """
    averages = line_averages(defending)
    goals = 0

    for scorer in attacking:
        scorer.mpg_goal = False
        scorer.duels = []
        scorer.mpg_skip_reason = None
        if scorer.slot_line is Line.G:
            scorer.mpg_skip_reason = "goalkeeper"
            continue                          # golden rule 5: a keeper never scores
        if scorer.real_goals > 0:
            scorer.mpg_skip_reason = "already_scored"
            continue                          # golden rule 3: already scored for real
        if scorer.rating < MPG_GOAL_FLOOR:
            scorer.mpg_skip_reason = "below_floor"
            continue                          # golden rule 2: 5.0 minimum

        current = scorer.rating
        duels_won = 0
        passed = True
        for line in GAUNTLET[scorer.slot_line]:
            opponent = averages.get(line)
            if opponent is None:
                # Empty line: free passage, no duel and no decrement (invariant 4.1.8).
                scorer.duels.append(
                    Duel(line, None, current, "free", 0.0, current)
                )
                continue
            before = current
            if current > opponent:
                outcome: str = "won"
            elif current == opponent:
                if attacker_wins_ties or at_home:
                    outcome = "won_on_tie"
                else:
                    scorer.duels.append(
                        Duel(line, opponent, before, "lost_on_tie", 0.0, before)
                    )
                    passed = False
                    break
            else:
                scorer.duels.append(Duel(line, opponent, before, "lost", 0.0, before))
                passed = False
                break
            duels_won += 1
            cost = FIRST_DUEL_COST if duels_won == 1 else NEXT_DUEL_COST
            current -= cost
            scorer.duels.append(Duel(line, opponent, before, outcome, cost, current))

        if passed:
            goals += 1
            scorer.mpg_goal = True

    return goals, averages
