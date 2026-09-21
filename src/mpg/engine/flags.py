"""Configuration flags for every point the official rules leave undocumented (spec 6).

These live in the pure engine so a resolution can be replayed with different
readings of the rules without touching the database or the API layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class EngineFlags:
    """Tunable readings of the ambiguous rules. Defaults are the spec's defaults."""

    #: Spec 6.1 -- "poste inférieur" in mandatory substitutions. "nearest" takes the
    #: closest line in either direction; "lower" only walks towards the goalkeeper.
    mandatory_sub_direction: Literal["nearest", "lower"] = "nearest"

    #: Spec 6.3 -- may the MPG save cancel a real own goal? The own goal is a real
    #: goal, and the rules only exclude own goals for the Valise.
    mpg_save_can_cancel_own_goal: bool = True

    #: Spec 6.4 -- MPG save resolves before the Valise; it is the constrained one.
    save_before_valise: bool = True

    #: Spec 6.5 -- one Valise cancels exactly one goal.
    goals_cancelled_per_valise: int = 1

    #: Additional call, not in spec 6: the numeric formula of spec 3.6 counts every
    #: real goal in the Valise pool, while the bonus table says a Valise never cancels
    #: an own goal. True follows the normative formula; False follows the table.
    #: The own goal conceded through Rotaldos is never cancellable either way
    #: (invariant 4.1.3), regardless of this flag.
    valise_can_cancel_real_own_goal: bool = True

    #: Additional call, not in spec 6: which line a substitute plays in when it fills a
    #: slot from another line. "slot" means it plays where the hole is (and carries the
    #: -1 per line skipped); "player" means it keeps its own line.
    out_of_position_line: Literal["slot", "player"] = "slot"

    #: Additional call, not in spec 6: a Miroir steals the opponent's single limited
    #: bonus of the day and applies it as if the mirroring team had posed it.
    #: Défense and Capitaine are unlimited and never mirrored.
    mirror_steals_limited_bonus: bool = True


DEFAULT_FLAGS = EngineFlags()
