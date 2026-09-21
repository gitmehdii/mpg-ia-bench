"""Types shared by the mercato algorithms. Pure, like the engine."""

from __future__ import annotations

from dataclasses import dataclass, field

from mpg.engine.lines import Line


@dataclass(frozen=True, slots=True)
class FreePlayer:
    """A player nobody in the league owns yet."""

    player_id: str
    line: Line
    quotation: int
    name: str = ""


@dataclass(frozen=True, slots=True)
class BidInput:
    """One closed bid as handed to the resolution algorithm."""

    participant_id: int
    player_id: str
    amount: int
    is_random: bool = False


@dataclass(frozen=True, slots=True)
class Allocation:
    """A player awarded to a participant at a price."""

    player_id: str
    participant_id: int
    price: int


@dataclass(slots=True)
class LogEntry:
    """An auditable event of a resolution (spec 4.4.3)."""

    kind: str
    message: str
    player_id: str | None = None
    participant_id: int | None = None
    detail: dict = field(default_factory=dict)


@dataclass(slots=True)
class AllocationResult:
    allocations: list[Allocation] = field(default_factory=list)
    budgets: dict[int, int] = field(default_factory=dict)
    log: list[LogEntry] = field(default_factory=list)

    def for_participant(self, participant_id: int) -> list[Allocation]:
        return [a for a in self.allocations if a.participant_id == participant_id]
