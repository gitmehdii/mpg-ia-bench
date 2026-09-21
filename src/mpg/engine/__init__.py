"""Pure resolution engine: no database, no framework, no I/O.

Everything the engine needs arrives as dataclasses and it hands a result back, which
is what makes the acceptance scenarios of spec 5 runnable without a single fixture.
"""

from mpg.engine.flags import DEFAULT_FLAGS, EngineFlags
from mpg.engine.lines import GAUNTLET, Line, UltraPosition, line_of
from mpg.engine.models import (
    Bonuses,
    LiveSub,
    MatchContext,
    MatchResult,
    PlayerEntry,
    TacticalSub,
    TeamResult,
    TeamSheet,
)
from mpg.engine.resolver import resolve_match

__all__ = [
    "DEFAULT_FLAGS",
    "GAUNTLET",
    "Bonuses",
    "EngineFlags",
    "Line",
    "LiveSub",
    "MatchContext",
    "MatchResult",
    "PlayerEntry",
    "TacticalSub",
    "TeamResult",
    "TeamSheet",
    "UltraPosition",
    "line_of",
    "resolve_match",
]
