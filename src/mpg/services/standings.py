"""League table and its five tie-breaks (spec 3.7)."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from mpg.config import POINTS_DRAW, POINTS_LOSS, POINTS_WIN
from mpg.db.models import League, LeagueMatch, Participant


@dataclass(slots=True)
class Row:
    participant_id: int
    team_name: str = ""
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    goals_for: int = 0
    goals_against: int = 0
    away_goals: int = 0
    #: Points and goals in head-to-head fixtures, keyed by the other participant.
    head_to_head: dict[int, list[int]] = field(default_factory=dict)

    @property
    def points(self) -> int:
        return self.won * POINTS_WIN + self.drawn * POINTS_DRAW + self.lost * POINTS_LOSS

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against


def _record(row: Row, opponent_id: int, scored: int, conceded: int, *, away: bool) -> None:
    row.played += 1
    row.goals_for += scored
    row.goals_against += conceded
    if away:
        row.away_goals += scored
    if scored > conceded:
        row.won += 1
        points = POINTS_WIN
    elif scored == conceded:
        row.drawn += 1
        points = POINTS_DRAW
    else:
        row.lost += 1
        points = POINTS_LOSS
    tally = row.head_to_head.setdefault(opponent_id, [0, 0, 0])
    tally[0] += points
    tally[1] += scored
    tally[2] += conceded


def compute_rows(session: Session, league: League) -> list[Row]:
    participants = list(
        session.execute(
            select(Participant).where(Participant.league_id == league.id).order_by(Participant.id)
        ).scalars()
    )
    rows = {p.id: Row(p.id, p.team_name) for p in participants}

    fixtures = session.execute(
        select(LeagueMatch).where(
            LeagueMatch.league_id == league.id, LeagueMatch.resolved_at.is_not(None)
        )
    ).scalars().all()
    for fixture in fixtures:
        home, away = rows.get(fixture.home_participant_id), rows.get(fixture.away_participant_id)
        if home is None or away is None:
            continue
        _record(home, away.participant_id, fixture.home_score, fixture.away_score, away=False)
        _record(away, home.participant_id, fixture.away_score, fixture.home_score, away=True)
    return list(rows.values())


def standings(session: Session, league: League) -> list[Row]:
    """The table, sorted by points then the five tie-breaks of spec 3.7.

    In order: overall goal difference, best overall attack, points in head-to-head
    fixtures, head-to-head goal difference, then goals scored away (leagues with
    return legs only).
    """
    rows = compute_rows(session, league)

    def sort_key(row: Row):
        return (-row.points, -row.goal_difference, -row.goals_for)

    ordered = sorted(rows, key=sort_key)

    # The last three tie-breaks only make sense inside a group of participants that
    # are still level, so they are applied group by group.
    result: list[Row] = []
    index = 0
    while index < len(ordered):
        group = [ordered[index]]
        while index + 1 < len(ordered) and sort_key(ordered[index + 1]) == sort_key(group[0]):
            index += 1
            group.append(ordered[index])
        index += 1
        if len(group) > 1:
            group = _break_tie(group, return_legs=league.return_legs)
        result.extend(group)
    return result


def _break_tie(group: list[Row], *, return_legs: bool) -> list[Row]:
    members = {row.participant_id for row in group}

    def mini_league(row: Row) -> tuple[int, int]:
        points = scored = conceded = 0
        for opponent_id, (pts, sf, sa) in row.head_to_head.items():
            if opponent_id in members:
                points += pts
                scored += sf
                conceded += sa
        return points, scored - conceded

    def key(row: Row):
        points, difference = mini_league(row)
        away = row.away_goals if return_legs else 0
        return (-points, -difference, -away, row.participant_id)

    return sorted(group, key=key)


def as_table(rows: list[Row]) -> list[dict]:
    return [
        {
            "rank": rank,
            "participant_id": row.participant_id,
            "team_name": row.team_name,
            "played": row.played,
            "points": row.points,
            "won": row.won,
            "drawn": row.drawn,
            "lost": row.lost,
            "goals_for": row.goals_for,
            "goals_against": row.goals_against,
            "goal_difference": row.goal_difference,
            "away_goals": row.away_goals,
        }
        for rank, row in enumerate(rows, start=1)
    ]
