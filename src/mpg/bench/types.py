"""What an agent sees, and what it answers. Pure dataclasses, like the engine.

Two rules shape everything here.

*Opacity.* An observation carries only what that participant is allowed to know. The
mercato view holds its own bids, its own budget and its own squad, and nothing about
anyone else -- the same rule the API enforces, applied to agents.

*No foresight.* The lineup view never carries the ratings of the game week being
played. A manager picks a team before the matches, and a benchmark that handed the
model the answers would measure nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mpg.engine.lines import Line


@dataclass(frozen=True, slots=True)
class PlayerCard:
    """A player as an agent sees him: what was knowable before kick-off."""

    player_id: str
    name: str
    line: Line
    quotation: int
    #: A short, readable handle such as "A07". Real ids look like
    #: `mpg_championship_player_512126`, and small models truncate them to the digits,
    #: which turns a football benchmark into a string-copying one.
    handle: str = ""
    club: str | None = None
    #: Ratings from earlier game weeks, oldest first. Never the one being played.
    past_ratings: tuple[float, ...] = ()
    past_goals: int = 0
    #: Game weeks this player was rated in, out of the ones on record before this one.
    #: Counted from ingested results only, so it carries no knowledge of the future --
    #: the API's season totals would, since they run to the end of the season.
    appearances: int = 0
    weeks_known: int = 0

    @property
    def average_rating(self) -> float | None:
        return sum(self.past_ratings) / len(self.past_ratings) if self.past_ratings else None

    @property
    def availability(self) -> float | None:
        """Share of the known game weeks this player was actually rated in.

        The single most useful thing a manager can know that a quotation does not say:
        a starter who did not play is replaced by the bench, and failing that by a
        phantom rated 2.5.
        """
        if not self.weeks_known:
            return None
        return self.appearances / self.weeks_known


@dataclass(frozen=True, slots=True)
class OwnBid:
    player_id: str
    amount: int


@dataclass(frozen=True, slots=True)
class MercatoView:
    """Everything the agent may know when it bids."""

    team_name: str
    round_number: int
    rounds_remaining: int
    budget: int
    #: How many players are still needed per line to satisfy the quota.
    deficit: dict[Line, int]
    squad: tuple[PlayerCard, ...]
    free_players: tuple[PlayerCard, ...]
    own_bids: tuple[OwnBid, ...] = ()

    @property
    def missing(self) -> int:
        return sum(self.deficit.values())

    @property
    def committed(self) -> int:
        return sum(bid.amount for bid in self.own_bids)


@dataclass(frozen=True, slots=True)
class LineupView:
    """Everything the agent may know when it picks a team."""

    team_name: str
    game_week: int
    squad: tuple[PlayerCard, ...]
    formations: tuple[str, ...]
    #: Bonuses still available this season, by name.
    bonuses_left: dict[str, int]
    opponent_name: str | None = None
    at_home: bool = True

    def by_line(self) -> dict[Line, list[PlayerCard]]:
        out: dict[Line, list[PlayerCard]] = {}
        for card in self.squad:
            out.setdefault(card.line, []).append(card)
        return out


# --------------------------------------------------------------------------- answers


@dataclass(frozen=True, slots=True)
class BidDecision:
    player_id: str
    amount: int


@dataclass(slots=True)
class MercatoAnswer:
    bids: list[BidDecision] = field(default_factory=list)
    #: Free-text reasoning, kept for the report. Never acted on.
    note: str = ""


@dataclass(slots=True)
class LineupAnswer:
    formation: str = "4-4-2"
    starters: list[str] = field(default_factory=list)
    bench: list[str] = field(default_factory=list)
    captain: str | None = None
    note: str = ""


@dataclass(slots=True)
class AgentCall:
    """One decision, with what it cost and whether it was usable (spec: the bench
    measures reliability as much as skill)."""

    kind: str
    model: str
    seconds: float = 0.0
    tokens: int = 0
    #: Set when the answer could not be used as given and the fallback took over.
    failure: str | None = None
    raw: str = ""
