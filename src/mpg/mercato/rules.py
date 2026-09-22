"""Squad quota and bid-entry rules (spec 3.2, 4.4.2). Pure functions."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from mpg.config import MERCATO_POSITION_QUOTA, MIN_SQUAD_SIZE
from mpg.engine.lines import Line, line_of


class BidRejected(ValueError):
    """A bid that breaks one of the rules of spec 4.4.2.

    `code` and `params` are carried alongside the message so a presentation layer can
    word the refusal in its own language. The message itself is unchanged, which is
    what the JSON API and its tests rely on.
    """

    def __init__(self, message: str, code: str = "", **params: object) -> None:
        super().__init__(message)
        self.code = code
        self.params = params


def squad_breakdown(ultra_positions: Iterable[int]) -> Counter[Line]:
    """Count a squad by line."""
    return Counter(line_of(ultra) for ultra in ultra_positions)


def quota_deficit(ultra_positions: Iterable[int]) -> dict[Line, int]:
    """How many players short of the quota the squad is, per line (spec 6.7)."""
    have = squad_breakdown(ultra_positions)
    return {
        Line(line): max(0, needed - have.get(Line(line), 0))
        for line, needed in MERCATO_POSITION_QUOTA.items()
    }


def missing_slots(ultra_positions: Iterable[int]) -> int:
    """Total number of players still needed to satisfy the quota."""
    return sum(quota_deficit(ultra_positions).values())


def quota_satisfied(ultra_positions: Iterable[int]) -> bool:
    """Spec 3.2: 18 players minimum, as 2 goalkeepers, 6 defenders, 6 midfielders,
    4 forwards -- the smallest squad that can field 11 starters and 7 substitutes
    including a goalkeeper."""
    positions = list(ultra_positions)
    return len(positions) >= MIN_SQUAD_SIZE and missing_slots(positions) == 0


def validate_bid(
    *,
    amount: int,
    quotation: int,
    budget: int,
    other_bids_total: int,
    player_owned_by: int | None,
    already_bid: bool,
) -> None:
    """Check one bid against spec 4.4.2, raising `BidRejected` with a clear reason.

    `other_bids_total` is the sum of the participant's other bids in the same round:
    the round's bids together may not exceed the budget, otherwise a participant could
    win more players than they can pay for.
    """
    if already_bid:
        raise BidRejected(
            "a bid on this player already exists for this round", "already_bid"
        )
    if player_owned_by is not None:
        raise BidRejected(
            "this player is already owned by a participant of the league", "owned"
        )
    if amount < quotation:
        raise BidRejected(
            f"the minimum bid on this player is its quotation ({quotation}), got {amount}",
            "below_quotation", quotation=quotation, amount=amount,
        )
    total = other_bids_total + amount
    if total > budget:
        raise BidRejected(
            f"the bids of a round may not exceed the budget: {other_bids_total} + "
            f"{amount} = {total} > {budget}",
            "over_budget", others=other_bids_total, amount=amount, total=total,
            budget=budget,
        )
