"""Lines of play, ultra-positions and the MPG gauntlet (spec 1.5, 3.6, 4.2)."""

from __future__ import annotations

from enum import IntEnum, StrEnum


class Line(StrEnum):
    """A line of play. Values are the single-letter codes used by the spec."""

    G = "G"
    D = "D"
    M = "M"
    A = "A"


class UltraPosition(IntEnum):
    """Fine-grained position (`ultraPosition` in the MPG API). Drives all logic."""

    GOALKEEPER = 10
    CENTRE_BACK = 20
    FULL_BACK = 21
    DEFENSIVE_MIDFIELDER = 30
    ATTACKING_MIDFIELDER = 31
    FORWARD = 40


ULTRA_TO_LINE: dict[int, Line] = {
    UltraPosition.GOALKEEPER: Line.G,
    UltraPosition.CENTRE_BACK: Line.D,
    UltraPosition.FULL_BACK: Line.D,
    UltraPosition.DEFENSIVE_MIDFIELDER: Line.M,
    UltraPosition.ATTACKING_MIDFIELDER: Line.M,
    UltraPosition.FORWARD: Line.A,
}

#: Order used to measure the distance between lines for mandatory substitutions (spec 4.3).
LINE_ORDER: tuple[Line, ...] = (Line.G, Line.D, Line.M, Line.A)

#: Opposing lines a field player must cross to score an MPG goal, in order (spec 3.6).
GAUNTLET: dict[Line, tuple[Line, ...]] = {
    Line.D: (Line.A, Line.M, Line.D, Line.G),
    Line.M: (Line.M, Line.D, Line.G),
    Line.A: (Line.D, Line.G),
}

#: Minimum rating required to score an MPG goal (golden rule 2).
MPG_GOAL_FLOOR = 5.0

#: A goalkeeper at this rating or above cancels one real goal (spec 3.6, "arrêt MPG").
MPG_SAVE_THRESHOLD = 8.0

#: Ratings are clamped to this interval once bonuses are applied (invariant 9).
RATING_MIN = 1.0
RATING_MAX = 10.0

#: Rating of the phantom player that fills an empty slot (spec 3.4).
ROTALDO_RATING = 2.5

#: One conceded own goal per this many phantom players in the final XI (spec 3.4).
ROTALDO_PER_OWN_GOAL = 3


def line_of(ultra_position: int) -> Line:
    """Return the line of play for an `ultraPosition` code."""
    try:
        return ULTRA_TO_LINE[ultra_position]
    except KeyError:
        raise ValueError(f"unknown ultraPosition: {ultra_position!r}") from None


def line_distance(a: Line, b: Line) -> int:
    """Number of lines skipped when a player of line `a` fills a slot of line `b`."""
    return abs(LINE_ORDER.index(a) - LINE_ORDER.index(b))


def clamp_rating(value: float) -> float:
    """Clamp a rating to [1, 10] (invariant 9)."""
    return min(RATING_MAX, max(RATING_MIN, value))
