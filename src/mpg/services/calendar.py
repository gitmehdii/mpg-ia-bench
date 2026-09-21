"""Fixture list generation (spec 3.1, 3.7). Pure: it works on ids, not on rows."""

from __future__ import annotations


def round_robin(participant_ids: list[int]) -> list[list[tuple[int, int]]]:
    """Single round robin by the circle method: n-1 game weeks of n/2 fixtures.

    Returns a list of game weeks, each a list of (home, away) pairs. The first id is
    held fixed and the rest rotate, and home advantage alternates per game week so
    nobody ends up always receiving -- which matters, because the home side wins tied
    duels (golden rule 1).
    """
    if len(participant_ids) < 2:
        return []
    if len(participant_ids) % 2:
        raise ValueError("a league needs an even number of participants")

    rotation = list(participant_ids)
    fixed, rotating = rotation[0], rotation[1:]
    weeks: list[list[tuple[int, int]]] = []

    for week in range(len(rotation) - 1):
        order = [fixed, *rotating]
        pairs: list[tuple[int, int]] = []
        for index in range(len(order) // 2):
            home = order[index]
            away = order[len(order) - 1 - index]
            # Alternate so home advantage is shared out evenly.
            pairs.append((home, away) if (week + index) % 2 == 0 else (away, home))
        weeks.append(pairs)
        rotating = [rotating[-1], *rotating[:-1]]

    return weeks


def full_calendar(
    participant_ids: list[int], *, return_legs: bool = True
) -> list[list[tuple[int, int]]]:
    """The whole fixture list, with the return leg mirrored if the league plays one."""
    first_leg = round_robin(participant_ids)
    if not return_legs:
        return first_leg
    second_leg = [[(away, home) for home, away in week] for week in first_leg]
    return [*first_leg, *second_leg]
