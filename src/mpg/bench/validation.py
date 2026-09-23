"""Turning what a model answered into something the game will accept.

A small model will hand back a player it does not own, an amount under the quotation,
eleven starters with two goalkeepers, or nothing usable at all. None of that may crash
a run: every answer is repaired where it can be and refused where it cannot, and what
was wrong is recorded, because reliability is half of what this bench measures.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mpg.bench.types import (
    BidDecision,
    LineupAnswer,
    LineupView,
    MercatoAnswer,
    MercatoView,
)
from mpg.engine.formations import BENCH_SIZE, STARTERS, is_legal, slots_of
from mpg.engine.lines import Line


@dataclass(slots=True)
class Repair:
    """What had to be corrected, for the report."""

    problems: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        self.problems.append(message)

    @property
    def clean(self) -> bool:
        return not self.problems


def clamp_bids(
    answer: MercatoAnswer, view: MercatoView, *, max_bids: int = 6
) -> tuple[list[BidDecision], Repair]:
    """Keep the bids the rules would accept, in the order the agent gave them.

    A bid below the quotation is raised to it rather than dropped: the intent was to
    buy the player, and the floor is the rule. A bid that would break the budget is
    dropped, because there is no honest way to guess which one the agent meant to keep.
    """
    repair = Repair()
    by_id = {card.player_id: card for card in view.free_players}
    owned = {card.player_id for card in view.squad}

    kept: list[BidDecision] = []
    seen: set[str] = set()
    committed = 0
    budget_left = view.budget

    for bid in answer.bids:
        if len(kept) >= max_bids:
            repair.note(f"more than {max_bids} bids, the extras were dropped")
            break
        card = by_id.get(bid.player_id)
        if card is None:
            if bid.player_id in owned:
                repair.note(f"{bid.player_id} is already in the squad")
            else:
                repair.note(f"{bid.player_id} is not a free player")
            continue
        if bid.player_id in seen:
            repair.note(f"{bid.player_id} bid on twice")
            continue

        amount = int(bid.amount)
        if amount < card.quotation:
            repair.note(
                f"{bid.player_id} bid at {amount} under its quotation {card.quotation}, raised"
            )
            amount = card.quotation

        # Keep back enough to fill the remaining slots at the floor price, exactly as
        # the random strategy does, so a run cannot end with an unfieldable squad.
        still_missing = max(0, view.missing - len(kept) - 1)
        if committed + amount + still_missing > budget_left:
            repair.note(
                f"{bid.player_id} at {amount} would not leave enough to complete the squad"
            )
            continue

        kept.append(BidDecision(bid.player_id, amount))
        seen.add(bid.player_id)
        committed += amount

    return kept, repair


def repair_lineup(
    answer: LineupAnswer, view: LineupView
) -> tuple[LineupAnswer, Repair]:
    """Make a lineup legal, keeping as much of the agent's intent as possible."""
    repair = Repair()
    squad = {card.player_id: card for card in view.squad}

    formation = answer.formation if answer.formation in view.formations else None
    if formation is None:
        repair.note(f"formation {answer.formation!r} unusable, fell back on 4-4-2")
        formation = "4-4-2"
    if not is_legal(formation):
        formation = "4-4-2"

    wanted = slots_of(formation)
    needed: dict[Line, int] = {}
    for line in wanted:
        needed[line] = needed.get(line, 0) + 1

    # Keep the agent's picks that exist and are not duplicated, line by line.
    chosen: dict[Line, list[str]] = {line: [] for line in (Line.G, Line.D, Line.M, Line.A)}
    used: set[str] = set()
    for player_id in answer.starters:
        card = squad.get(player_id)
        if card is None:
            repair.note(f"{player_id} is not in the squad")
            continue
        if player_id in used:
            repair.note(f"{player_id} picked twice")
            continue
        if len(chosen[card.line]) >= needed.get(card.line, 0):
            repair.note(f"too many {card.line} for a {formation}, {player_id} dropped")
            continue
        chosen[card.line].append(player_id)
        used.add(player_id)

    # Fill whatever is short with the best remaining player of that line.
    def best_remaining(line: Line) -> str | None:
        candidates = [
            card for card in view.squad
            if card.line is line and card.player_id not in used
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda c: (-(c.average_rating or 0), -c.quotation, c.player_id))
        return candidates[0].player_id

    for line, count in needed.items():
        while len(chosen[line]) < count:
            pick = best_remaining(line)
            if pick is None:
                repair.note(f"not enough {line} in the squad to fill the {formation}")
                break
            repair.note(f"a {line} slot was left empty, filled with {pick}")
            chosen[line].append(pick)
            used.add(pick)

    # Lay the picks out in slot order, so the XI reads goalkeeper first.
    remaining = {line: list(picks) for line, picks in chosen.items()}
    starters: list[str] = []
    for line in wanted:
        if remaining[line]:
            starters.append(remaining[line].pop(0))

    # The bench: what the agent asked for, then filled to seven with a goalkeeper first.
    bench: list[str] = []
    for player_id in answer.bench:
        if player_id in squad and player_id not in used and player_id not in bench:
            bench.append(player_id)
            used.add(player_id)
    has_keeper = any(squad[pid].line is Line.G for pid in bench)
    if not has_keeper:
        keeper = best_remaining(Line.G)
        if keeper:
            repair.note("no goalkeeper on the bench, one was added")
            bench.insert(0, keeper)
            used.add(keeper)
    for card in sorted(view.squad, key=lambda c: (-(c.average_rating or 0), c.player_id)):
        if len(bench) >= BENCH_SIZE:
            break
        if card.player_id not in used:
            bench.append(card.player_id)
            used.add(card.player_id)
    bench = bench[:BENCH_SIZE]

    if len(starters) != STARTERS:
        repair.note(f"{len(starters)} starters instead of {STARTERS}")
    if len(bench) != BENCH_SIZE:
        repair.note(f"{len(bench)} substitutes instead of {BENCH_SIZE}")

    captain = answer.captain
    if captain is not None:
        if captain not in starters:
            repair.note("the captain is not a starter, dropped")
            captain = None
        elif squad[captain].line is Line.G:
            repair.note("the captain cannot be the goalkeeper, dropped")
            captain = None

    return (
        LineupAnswer(
            formation=formation, starters=starters, bench=bench,
            captain=captain, note=answer.note,
        ),
        repair,
    )
