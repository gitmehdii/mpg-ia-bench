"""Ingestion jobs (spec 1.6, 4.5).

Three things this has to get right:

* quotations are historised, never overwritten -- the mercato and the market need
  the series;
* performances are upserted on (player_id, match_id), because a rating can change
  retroactively after a postponed match;
* a rating that changes under an already-resolved fixture flags it for recompute
  rather than quietly changing a published result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from mpg.config import CHAMPIONSHIPS, get_settings
from mpg.db.models import (
    Championship,
    Club,
    LeagueMatch,
    Lineup,
    LineupSlot,
    Match,
    Performance,
    Player,
    PlayerQuotation,
)
from mpg.ingest.client import MpgClient
from mpg.ingest.parsers import ParsedPlayer, parse_clubs, parse_pool


@dataclass(slots=True)
class IngestReport:
    players: int = 0
    new_players: int = 0
    quotations: int = 0
    matches: int = 0
    performances: int = 0
    revised_performances: int = 0
    flagged_fixtures: int = 0
    clubs: int = 0
    departures: int = 0
    notes: list[str] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(UTC)


def ingest_championships(session: Session, client: MpgClient) -> int:
    payload = client.active_championships()
    count = 0
    for raw_id, body in (payload.get("championships") or {}).items():
        if not str(raw_id).isdigit():
            continue
        championship_id = int(raw_id)
        row = session.get(Championship, championship_id)
        if row is None:
            label = CHAMPIONSHIPS.get(championship_id, str(championship_id))
            row = Championship(id=championship_id, code=label, name=label)
            session.add(row)
        row.first_game_week = body.get("firstGameWeekNumber")
        row.last_game_week = body.get("lastGameWeekNumber")
        start = body.get("startDate")
        if start:
            row.current_season = int(start[:4])
        count += 1
    session.flush()
    return count


def ingest_clubs(
    session: Session, client: MpgClient, preferred_championship: int | None = None
) -> int:
    """630 KB that rarely changes (spec 1.6): call it once and keep it.

    A club can be active in several championships at once -- a side that has just come
    up appears in both divisions -- so the one it is attached to is chosen
    deterministically, and only ever among the championships already in the database.
    Picking an unknown one would break the foreign key.
    """
    known = set(session.execute(select(Championship.id)).scalars())
    parsed = parse_clubs(client.clubs())
    count = 0
    for club in parsed:
        row = session.get(Club, club.id)
        if row is None:
            row = Club(id=club.id)
            session.add(row)
        row.name = club.name
        row.short_name = club.short_name
        row.primary_color = club.primary_color

        candidates = [cid for cid in sorted(club.championship_ids) if cid in known]
        if preferred_championship in candidates:
            row.championship_id = preferred_championship
        elif candidates:
            row.championship_id = candidates[0]
        else:
            row.championship_id = None
        count += 1
    session.flush()
    return count


def _upsert_player(session: Session, parsed: ParsedPlayer, championship_id: int,
                   report: IngestReport, observed_at: datetime) -> Player:
    player = session.get(Player, parsed.id)
    if player is None:
        player = Player(id=parsed.id, last_name=parsed.last_name,
                        ultra_position=parsed.ultra_position)
        session.add(player)
        report.new_players += 1
    player.first_name = parsed.first_name
    player.last_name = parsed.last_name
    player.position = parsed.position
    player.ultra_position = parsed.ultra_position
    player.club_id = parsed.club_id
    player.championship_id = championship_id
    player.country_code = parsed.country_code
    player.birth_date = parsed.birth_date
    player.stats = parsed.stats
    player.left_championship = False

    if parsed.quotation is not None:
        # Historise, never overwrite: only append when the value actually moved.
        latest = session.execute(
            select(PlayerQuotation)
            .where(PlayerQuotation.player_id == player.id)
            .order_by(PlayerQuotation.observed_at.desc())
        ).scalars().first()
        if latest is None or latest.quotation != parsed.quotation:
            session.add(
                PlayerQuotation(
                    player_id=player.id,
                    championship_id=championship_id,
                    observed_at=observed_at,
                    quotation=parsed.quotation,
                )
            )
            report.quotations += 1
        player.quotation = parsed.quotation
    return player


def _upsert_match(session: Session, parsed, season: int) -> Match:
    match = session.get(Match, parsed.id)
    if match is None:
        match = Match(id=parsed.id, championship_id=parsed.championship_id, season=season,
                      game_week_number=parsed.game_week_number)
        session.add(match)
    match.game_week_number = parsed.game_week_number
    match.date = parsed.date
    match.home_club_id = parsed.home_club_id
    match.away_club_id = parsed.away_club_id
    match.home_score = parsed.home_score
    match.away_score = parsed.away_score
    match.status = parsed.status
    return match


def _flag_affected_fixtures(session: Session, player_id: str, game_week: int) -> int:
    """Spec 4.5: mark, never recompute in silence."""
    participant_ids = list(
        session.execute(
            select(Lineup.participant_id)
            .join(LineupSlot, LineupSlot.lineup_id == Lineup.id)
            .where(LineupSlot.player_id == player_id, Lineup.game_week_number == game_week)
        ).scalars()
    )
    if not participant_ids:
        return 0
    fixtures = session.execute(
        select(LeagueMatch).where(
            LeagueMatch.game_week_number == game_week,
            LeagueMatch.resolved_at.is_not(None),
            LeagueMatch.home_participant_id.in_(participant_ids)
            | LeagueMatch.away_participant_id.in_(participant_ids),
        )
    ).scalars().all()
    for fixture in fixtures:
        fixture.needs_recompute = True
    return len(fixtures)


def ingest_pool(
    session: Session,
    client: MpgClient,
    championship_id: int,
    season: int | None = None,
) -> IngestReport:
    """Refresh one championship's pool: players, quotations, matches and ratings."""
    report = IngestReport()
    observed_at = _now()
    payload = client.players_pool(championship_id, season)
    parsed_players = parse_pool(payload, championship_id)

    championship = session.get(Championship, championship_id)
    effective_season = season or (championship.current_season if championship else observed_at.year)

    seen: set[str] = set()
    for parsed in parsed_players:
        _upsert_player(session, parsed, championship_id, report, observed_at)
        seen.add(parsed.id)
        report.players += 1

        for parsed_match in parsed.matches:
            _upsert_match(session, parsed_match, effective_season)
            report.matches += 1
        session.flush()

        for performance in parsed.performances:
            existing = session.execute(
                select(Performance).where(
                    Performance.player_id == performance.player_id,
                    Performance.match_id == performance.match_id,
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    Performance(
                        player_id=performance.player_id,
                        match_id=performance.match_id,
                        game_week_number=performance.game_week_number,
                        season=effective_season,
                        rating=performance.rating,
                        goals_scored=performance.goals_scored,
                    )
                )
                report.performances += 1
                continue

            changed = (
                existing.rating != performance.rating
                or existing.goals_scored != performance.goals_scored
            )
            if changed:
                existing.rating = performance.rating
                existing.goals_scored = performance.goals_scored
                existing.revised_at = observed_at
                report.revised_performances += 1
                report.flagged_fixtures += _flag_affected_fixtures(
                    session, performance.player_id, performance.game_week_number
                )
    session.flush()

    # Spec 3.2: a player who left the championship is refunded at cost, so mark the
    # departures rather than deleting rows other tables point at.
    if not season:
        known = session.execute(
            select(Player).where(
                Player.championship_id == championship_id, Player.left_championship.is_(False)
            )
        ).scalars().all()
        for player in known:
            if player.id not in seen:
                player.left_championship = True
                report.departures += 1
                report.notes.append(f"{player.display_name} has left the championship")
    session.flush()
    return report


def ingest_history(
    session: Session, client: MpgClient, championship_id: int, seasons: list[int]
) -> list[IngestReport]:
    """`nearestMatches` only covers a few game weeks, so a full history is built by
    walking the seasons one at a time (spec 1.4)."""
    return [ingest_pool(session, client, championship_id, season) for season in seasons]


def refresh_all(session: Session, championship_id: int | None = None) -> IngestReport:
    settings = get_settings()
    championship_id = championship_id or settings.default_championship
    with MpgClient() as client:
        ingest_championships(session, client)
        if session.execute(select(Club).limit(1)).first() is None:
            ingest_clubs(session, client, championship_id)
        return ingest_pool(session, client, championship_id)
