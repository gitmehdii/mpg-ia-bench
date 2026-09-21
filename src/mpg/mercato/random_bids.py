"""Bids generated for a participant who never validated in time (spec 3.2, 4.4.4).

MPG does not publish its algorithm; this one is a choice of this build, kept behind
the `RANDOM_BID_STRATEGY` flag. Its invariants are what the acceptance test M4 checks:
a random bid never exceeds the budget, always leaves enough to complete the squad at
the floor price, and goes for the lines that are short.
"""

from __future__ import annotations

from collections.abc import Iterable
from math import ceil
from random import Random

from mpg.config import MERCATO_BASE_ROUNDS
from mpg.engine.lines import Line
from mpg.mercato.types import BidInput, FreePlayer

#: A random bid goes up to 20 % over the quotation.
MAX_PREMIUM = 0.20

#: The draw picks among the this many best-quoted players the budget allows.
SHORTLIST = 10

#: Floor price of any player, used to size the reserve.
FLOOR_PRICE = 1


def generate_random_bids(
    participant_id: int,
    budget: int,
    deficit: dict[Line, int],
    free_players: Iterable[FreePlayer],
    round_number: int,
    rng: Random,
) -> list[BidInput]:
    """Bid for roughly a third of the missing slots, on the lines that are short."""
    deficit = {line: count for line, count in deficit.items() if count > 0}
    slots = sum(deficit.values())
    if slots == 0 or budget < FLOOR_PRICE:
        return []

    rounds_left = max(1, MERCATO_BASE_ROUNDS - round_number + 1)
    envelope = budget // rounds_left
    targets = max(1, ceil(slots / 3))

    available: dict[Line, list[FreePlayer]] = {}
    for player in free_players:
        available.setdefault(player.line, []).append(player)
    for players in available.values():
        # Deterministic order so the same seed always draws the same player.
        players.sort(key=lambda p: (-p.quotation, p.player_id))

    bids: list[BidInput] = []
    taken: set[str] = set()
    committed = 0

    for _ in range(targets):
        if not deficit:
            break
        # The line that is shortest, ties broken by the seeded generator.
        worst = max(deficit.values())
        contenders = sorted(line for line, count in deficit.items() if count == worst)
        line = contenders[0] if len(contenders) == 1 else rng.choice(contenders)

        # Keep back enough to fill the remaining slots at the floor price.
        reserve = (slots - 1) * FLOOR_PRICE
        ceiling = min(envelope - committed, budget - committed - reserve)
        if ceiling < FLOOR_PRICE:
            break

        candidates = [
            p for p in available.get(line, [])
            if p.player_id not in taken and p.quotation <= ceiling
        ]
        if not candidates:
            # Nothing affordable on that line: stop asking for it this round.
            deficit.pop(line, None)
            continue

        player = rng.choice(candidates[:SHORTLIST])
        premium = 1 + rng.uniform(0, MAX_PREMIUM)
        amount = max(player.quotation, min(ceiling, round(player.quotation * premium)))
        bids.append(BidInput(participant_id, player.player_id, amount, is_random=True))

        taken.add(player.player_id)
        committed += amount
        slots -= 1
        deficit[line] -= 1
        if deficit[line] <= 0:
            deficit.pop(line)

    return bids
