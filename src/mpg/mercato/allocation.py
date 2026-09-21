"""Awarding players at the end of a closed-bid round (spec 4.4.3).

Two properties matter more than anything else here:

* the iteration order over players is sorted by id, so that two runs with the same
  seed cannot diverge once a participant wins several players;
* every draw goes through the round's seeded generator, so a resolution can be
  replayed identically for a dispute or a test.
"""

from __future__ import annotations

from collections.abc import Iterable
from random import Random

from mpg.mercato.types import Allocation, AllocationResult, BidInput, LogEntry


def allocate(
    bids: Iterable[BidInput],
    budgets: dict[int, int],
    rng: Random,
) -> AllocationResult:
    """Award every player who drew at least one bid, to the highest bidder.

    `budgets` is the remaining budget per participant; it is copied, not mutated.
    Ties are broken by a seeded draw and journalled. A winner who can no longer
    afford the bid -- impossible if the per-round cap of spec 4.4.2 held, but kept
    as a guard rail -- hands the player to the next candidate by descending amount.
    """
    result = AllocationResult(budgets=dict(budgets))
    by_player: dict[str, list[BidInput]] = {}
    for bid in bids:
        by_player.setdefault(bid.player_id, []).append(bid)

    # Deterministic iteration order: without it, the same seed can produce a
    # different outcome when one participant wins several players.
    for player_id in sorted(by_player):
        candidates = sorted(
            by_player[player_id], key=lambda b: (-b.amount, b.participant_id)
        )

        # Resolve the draw on the leading amount before walking the list, so the
        # order in which candidates are tried is itself reproducible.
        top = candidates[0].amount
        tied = [b for b in candidates if b.amount == top]
        if len(tied) > 1:
            winner = rng.choice(sorted(tied, key=lambda b: b.participant_id))
            result.log.append(
                LogEntry(
                    kind="draw",
                    message=(
                        f"draw on {player_id} at {top}: "
                        f"participant {winner.participant_id} wins"
                    ),
                    player_id=player_id,
                    participant_id=winner.participant_id,
                    detail={
                        "amount": top,
                        "candidates": sorted(b.participant_id for b in tied),
                        "winner": winner.participant_id,
                    },
                )
            )
            rest = [b for b in candidates if b is not winner]
            candidates = [winner, *rest]

        for candidate in candidates:
            remaining = result.budgets.get(candidate.participant_id, 0)
            if remaining >= candidate.amount:
                result.allocations.append(
                    Allocation(player_id, candidate.participant_id, candidate.amount)
                )
                result.budgets[candidate.participant_id] = remaining - candidate.amount
                break
            result.log.append(
                LogEntry(
                    kind="insufficient_budget",
                    message=(
                        f"participant {candidate.participant_id} cannot pay "
                        f"{candidate.amount} for {player_id} ({remaining} left)"
                    ),
                    player_id=player_id,
                    participant_id=candidate.participant_id,
                    detail={"amount": candidate.amount, "remaining": remaining},
                )
            )

    return result
