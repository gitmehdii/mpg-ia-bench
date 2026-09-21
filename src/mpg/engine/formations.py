"""Formations and the slot structure of an XI (spec 3.3)."""

from __future__ import annotations

from collections import Counter

from mpg.engine.lines import Line

#: Legal formations, as (defenders, midfielders, forwards). One goalkeeper always.
FORMATIONS: dict[str, tuple[int, int, int]] = {
    "4-4-2": (4, 4, 2),
    "4-3-3": (4, 3, 3),
    "3-5-2": (3, 5, 2),
    "5-3-2": (5, 3, 2),
    "5-4-1": (5, 4, 1),
    "3-4-3": (3, 4, 3),
    "4-5-1": (4, 5, 1),
    "4-2-4": (4, 2, 4),
}

#: 4-2-4 is only reachable through the 424 bonus (spec 3.5).
BONUS_ONLY_FORMATIONS = frozenset({"4-2-4"})

STARTERS = 11
BENCH_SIZE = 7


def slots_of(formation: str) -> list[Line]:
    """The 11 slots of a formation, goalkeeper first then defence, midfield, attack."""
    try:
        defenders, midfielders, forwards = FORMATIONS[formation]
    except KeyError:
        raise ValueError(f"unknown formation: {formation!r}") from None
    return [Line.G, *[Line.D] * defenders, *[Line.M] * midfielders, *[Line.A] * forwards]


def formation_name(counts: dict[Line, int] | Counter[Line]) -> str | None:
    """Reverse lookup: the formation matching a line-count breakdown, if any."""
    shape = (counts.get(Line.D, 0), counts.get(Line.M, 0), counts.get(Line.A, 0))
    for name, candidate in FORMATIONS.items():
        if candidate == shape and counts.get(Line.G, 0) == 1:
            return name
    return None


def infer_formation(lines: list[Line]) -> str | None:
    """Infer the formation from the lines actually fielded."""
    return formation_name(Counter(lines))


def is_legal(formation: str, *, bonus_424: bool = False) -> bool:
    """Is this formation playable for this game week?"""
    if formation not in FORMATIONS:
        return False
    if formation in BONUS_ONLY_FORMATIONS:
        return bonus_424
    return True
