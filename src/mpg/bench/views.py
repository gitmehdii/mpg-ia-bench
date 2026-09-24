"""Building an agent's observation from the database.

This is where the two rules of the bench are actually enforced.

*Opacity.* A mercato view is built from one participant's own rows only. There is no
query here that reads another participant's bids or budget.

*No foresight.* A lineup view carries ratings from game weeks strictly earlier than the
one being played. Handing a model the ratings of the matches it is about to be scored
on would make the benchmark measure nothing at all.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from mpg.bench.types import LineupView, MercatoView, OwnBid, PlayerCard
from mpg.config import BONUS_QUOTAS, MERCATO_BASE_ROUNDS, UNLIMITED_BONUSES
from mpg.db.models import (
    Bid,
    BonusUsage,
    Club,
    League,
    MercatoRound,
    Participant,
    Performance,
    Player,
    Roster,
)
from mpg.engine.formations import FORMATIONS, is_legal
from mpg.engine.lines import line_of
from mpg.mercato.rules import quota_deficit
from mpg.mercato.service import free_players, owned_player_ids


def known_game_weeks(
    session: Session, before_game_week: int, season: int | None = None
) -> int:
    """How many game weeks are on record before this one, within one season.

    The denominator of availability. Taken from what was ingested rather than from the
    championship calendar, because a week nobody has results for tells an agent nothing.
    The season filter matters as soon as a database holds more than one: game week 4 of
    two different seasons would otherwise be read as the same week.
    """
    query = select(Performance.game_week_number).where(
        Performance.game_week_number < before_game_week,
        Performance.rating.is_not(None),
    )
    if season is not None:
        query = query.where(Performance.season == season)
    return len(set(session.execute(query.distinct()).scalars()))


def _past_form(
    session: Session, player_ids: list[str], before_game_week: int,
    season: int | None = None,
) -> dict[str, tuple[list[float], int, int]]:
    """Ratings, goals and appearances from earlier game weeks of the same season."""
    if not player_ids:
        return {}
    query = select(Performance).where(
        Performance.player_id.in_(player_ids),
        Performance.game_week_number < before_game_week,
        Performance.rating.is_not(None),
    )
    if season is not None:
        query = query.where(Performance.season == season)
    rows = session.execute(query.order_by(Performance.game_week_number)).scalars().all()
    out: dict[str, tuple[list[float], int, int]] = {}
    for row in rows:
        ratings, goals, played = out.setdefault(row.player_id, ([], 0, 0))
        ratings.append(row.rating)
        out[row.player_id] = (ratings, goals + row.goals_scored, played + 1)
    return out


#: Letter used in a handle, per line.
HANDLE_LETTER = {"G": "G", "D": "D", "M": "M", "A": "A"}


def _with_handles(cards: list[PlayerCard]) -> list[PlayerCard]:
    """Give each card a short handle such as `A07`, numbered per line.

    Real ids look like `mpg_championship_player_512126`. Small models truncate those to
    the digits and then bid on a player nobody recognises, which measures string
    handling rather than football.
    """
    from dataclasses import replace

    counters: dict[str, int] = {}
    out = []
    for card in cards:
        letter = HANDLE_LETTER[str(card.line)]
        counters[letter] = counters.get(letter, 0) + 1
        out.append(replace(card, handle=f"{letter}{counters[letter]:02d}"))
    return out


def _cards(
    session: Session, players: list[Player], *, before_game_week: int | None = None,
    season: int | None = None,
) -> list[PlayerCard]:
    clubs = {
        row.id: row.name
        for row in session.execute(
            select(Club).where(Club.id.in_([p.club_id for p in players if p.club_id]))
        ).scalars()
    }
    form = (
        _past_form(session, [p.id for p in players], before_game_week, season)
        if before_game_week is not None
        else {}
    )
    weeks = (
        known_game_weeks(session, before_game_week, season)
        if before_game_week is not None
        else 0
    )
    cards = []
    for player in players:
        ratings, goals, played = form.get(player.id, ([], 0, 0))
        cards.append(
            PlayerCard(
                player_id=player.id,
                name=player.display_name,
                line=line_of(player.ultra_position),
                quotation=player.quotation or 1,
                club=clubs.get(player.club_id),
                past_ratings=tuple(ratings),
                past_goals=goals,
                appearances=played,
                weeks_known=weeks,
            )
        )
    return _with_handles(cards)


def players_with_results(
    session: Session, game_weeks: list[int], season: int | None = None
) -> set[str]:
    """Players the data actually covers on the weeks being replayed.

    The public API serves only a window of results: on the three weeks of a past
    season it returns a rating for roughly a fifth of the pool, where a real game week
    covers about forty percent. The rest are missing *data*, not real absences, and the
    engine cannot tell the two apart -- it reads no rating as "did not play" and fields
    a phantom. Restricting the benchmark's universe to the covered players is what keeps
    a run from measuring the size of the gap in the API rather than the managers.
    """
    query = select(Performance.player_id).where(
        Performance.game_week_number.in_(game_weeks),
        Performance.rating.is_not(None),
    )
    if season is not None:
        query = query.where(Performance.season == season)
    return set(session.execute(query.distinct()).scalars())


def squad_players(session: Session, participant_id: int) -> list[Player]:
    return list(
        session.execute(
            select(Player)
            .join(Roster, Roster.player_id == Player.id)
            .where(Roster.participant_id == participant_id, Roster.sold_at.is_(None))
        ).scalars()
    )


def mercato_view(
    session: Session,
    participant: Participant,
    round_: MercatoRound,
    *,
    pool_limit: int = 400,
    before_game_week: int | None = None,
    season: int | None = None,
    eligible: set[str] | None = None,
) -> MercatoView:
    """What this participant may know before bidding. Nothing about anyone else."""
    league = session.get(League, participant.league_id)
    squad = squad_players(session, participant.id)

    owned = owned_player_ids(session, league.id)
    free = free_players(session, league, owned, eligible)
    free.sort(key=lambda p: (-p.quotation, p.player_id))
    free = free[:pool_limit]
    free_rows = {
        row.id: row
        for row in session.execute(
            select(Player).where(Player.id.in_([p.player_id for p in free]))
        ).scalars()
    }

    own_bids = tuple(
        OwnBid(row.player_id, row.amount)
        for row in session.execute(
            select(Bid).where(
                Bid.round_id == round_.id, Bid.participant_id == participant.id
            )
        ).scalars()
    )

    return MercatoView(
        team_name=participant.team_name,
        round_number=round_.number,
        rounds_remaining=max(1, MERCATO_BASE_ROUNDS - round_.number + 1),
        budget=participant.budget,
        deficit=quota_deficit([p.ultra_position for p in squad]),
        squad=tuple(
            _cards(session, squad, before_game_week=before_game_week, season=season)
        ),
        free_players=tuple(
            _cards(
                session,
                [free_rows[p.player_id] for p in free if p.player_id in free_rows],
                before_game_week=before_game_week,
                season=season,
            )
        ),
        own_bids=own_bids,
    )


def lineup_view(
    session: Session,
    participant: Participant,
    game_week: int,
    *,
    opponent: Participant | None = None,
    at_home: bool = True,
    season: int | None = None,
) -> LineupView:
    """What this participant may know before picking a team.

    `before_game_week=game_week` is the whole point: the cards carry form from earlier
    weeks and never a rating from the one about to be played.
    """
    squad = squad_players(session, participant.id)
    league = session.get(League, participant.league_id)

    used = session.execute(
        select(BonusUsage).where(BonusUsage.participant_id == participant.id)
    ).scalars().all()
    left: dict[str, int] = {}
    for key, by_size in BONUS_QUOTAS.items():
        total = by_size.get(league.size, 0)
        if total:
            left[key] = total - sum(1 for row in used if row.bonus_type == key)
    for key in sorted(UNLIMITED_BONUSES):
        left[key] = -1                      # -1 reads as unlimited

    return LineupView(
        team_name=participant.team_name,
        game_week=game_week,
        squad=tuple(
            _cards(session, squad, before_game_week=game_week, season=season)
        ),
        formations=tuple(name for name in FORMATIONS if is_legal(name)),
        bonuses_left=left,
        opponent_name=opponent.team_name if opponent else None,
        at_home=at_home,
    )
