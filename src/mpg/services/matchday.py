"""Bridge between the database and the pure engine (spec 3.6, 3.8, 4.5).

The engine knows nothing about rows, so everything it needs is assembled here and the
result is written back as a score plus a stored report, which is what lets a fixture be
explained later without recomputing it.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from mpg.db.models import (
    BonusUsage,
    League,
    LeagueMatch,
    Lineup,
    LineupSlot,
    LiveSubRecord,
    Match,
    Participant,
    Performance,
    Player,
    TacticalSubRule,
)
from mpg.engine.flags import DEFAULT_FLAGS, EngineFlags
from mpg.engine.models import (
    Bonuses,
    LiveSub,
    MatchContext,
    MatchResult,
    PlayerEntry,
    TacticalSub,
    TeamSheet,
)
from mpg.engine.resolver import resolve_match

#: Maps a stored bonus row onto the engine's bonus set.
BONUS_FIELDS = {
    "defense": "defense",
    "zahia": "zahia",
    "valise": "valise",
    "suarez": "suarez",
    "mirror": "mirror",
    "cheat_code": "cheat_code",
    "tonton_pat": "tonton_pat",
    "formation_424": "formation_424",
}


def load_bonuses(session: Session, participant_id: int, game_week: int) -> Bonuses:
    rows = session.execute(
        select(BonusUsage).where(
            BonusUsage.participant_id == participant_id,
            BonusUsage.game_week_number == game_week,
        )
    ).scalars().all()

    bonuses = Bonuses()
    for row in rows:
        if row.bonus_type == "mcdo":
            bonuses.mcdo_target = row.target_player_id
        elif row.bonus_type == "captain":
            bonuses.captain_target = row.target_player_id
        elif row.bonus_type in BONUS_FIELDS:
            setattr(bonuses, BONUS_FIELDS[row.bonus_type], True)
    return bonuses


def _performance_index(
    session: Session, player_ids: list[str], game_week: int
) -> dict[str, Performance]:
    if not player_ids:
        return {}
    rows = session.execute(
        select(Performance).where(
            Performance.player_id.in_(player_ids),
            Performance.game_week_number == game_week,
        )
    ).scalars().all()
    return {row.player_id: row for row in rows}


def _match_index(session: Session, performances: dict[str, Performance]) -> dict[str, Match]:
    match_ids = {p.match_id for p in performances.values()}
    if not match_ids:
        return {}
    rows = session.execute(select(Match).where(Match.id.in_(match_ids))).scalars().all()
    return {row.id: row for row in rows}


def build_team_sheet(
    session: Session, participant: Participant, game_week: int, *, live_mode: bool = False
) -> TeamSheet:
    """Turn a stored lineup into the engine's input for one game week."""
    lineup = session.execute(
        select(Lineup).where(
            Lineup.participant_id == participant.id,
            Lineup.game_week_number == game_week,
        )
    ).scalar_one_or_none()
    if lineup is None:
        raise ValueError(
            f"participant {participant.id} has no lineup for game week {game_week}"
        )

    slots = sorted(lineup.slots, key=lambda s: (s.role != "starter", s.order))
    player_ids = [slot.player_id for slot in slots]
    players = {
        row.id: row
        for row in session.execute(select(Player).where(Player.id.in_(player_ids))).scalars()
    }
    performances = _performance_index(session, player_ids, game_week)
    matches = _match_index(session, performances)

    def entry(slot: LineupSlot) -> PlayerEntry:
        player = players[slot.player_id]
        performance = performances.get(slot.player_id)
        match = matches.get(performance.match_id) if performance else None
        # Spec 3.8: a match brought forward or postponed hands everyone a 5 and drops
        # their goals; the player is deemed present but cannot come on mid-weekend.
        neutralised = bool(match and (match.is_advanced or match.is_postponed))
        kickoff_passed = bool(
            match and match.date and match.date.replace(tzinfo=match.date.tzinfo or UTC) <= _now()
        )
        return PlayerEntry(
            player_id=player.id,
            ultra_position=player.ultra_position,
            name=player.display_name,
            played=bool(performance and performance.rating is not None),
            rating=performance.rating if performance else None,
            real_goals=performance.goals_scored if performance else 0,
            own_goals=performance.own_goals if performance else 0,
            match_started=kickoff_passed,
            neutralised=neutralised,
        )

    starters = [entry(s) for s in slots if s.role == "starter"]
    bench = [entry(s) for s in slots if s.role == "bench"]

    tactical = [
        TacticalSub(rule.starter_id, rule.sub_id, rule.threshold)
        for rule in session.execute(
            select(TacticalSubRule).where(TacticalSubRule.lineup_id == lineup.id)
        ).scalars()
    ]
    live = [
        LiveSub(rule.starter_id, rule.sub_id, rule.order)
        for rule in session.execute(
            select(LiveSubRecord).where(LiveSubRecord.lineup_id == lineup.id)
        ).scalars()
    ]

    return TeamSheet(
        participant_id=str(participant.id),
        starters=starters,
        bench=bench,
        formation=lineup.formation,
        bonuses=load_bonuses(session, participant.id, game_week),
        tactical_subs=tactical,
        live_mode=live_mode or lineup.live_mode,
        live_subs=live,
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _serialise(result: MatchResult) -> dict:
    """A JSON report of the fixture, enough to explain the score without recomputing."""

    def side(team) -> dict:
        data = asdict(team)
        data["line_averages"] = {str(k): v for k, v in team.line_averages.items()}
        for slot in data["final_xi"]:
            slot["slot_line"] = str(slot["slot_line"])
        for goal in data["goals"]:
            goal["line"] = str(goal["line"]) if goal["line"] else None
        return data

    return {
        "home": side(result.home),
        "away": side(result.away),
        "context": asdict(result.context),
        "generated_at": _now().isoformat(),
    }


def resolve_league_match(
    session: Session,
    fixture: LeagueMatch,
    *,
    flags: EngineFlags = DEFAULT_FLAGS,
    force: bool = False,
) -> MatchResult:
    """Resolve one league fixture and store its score and report."""
    if fixture.resolved_at is not None and not force:
        raise ValueError("this fixture is already resolved; pass force=True to replay it")

    league = session.get(League, fixture.league_id)
    home = session.get(Participant, fixture.home_participant_id)
    away = session.get(Participant, fixture.away_participant_id)

    context = MatchContext(
        game_week=fixture.game_week_number,
        return_legs=league.return_legs,
        playoff=fixture.is_playoff,
    )
    home_sheet = build_team_sheet(
        session, home, fixture.game_week_number, live_mode=league.live_mode
    )
    away_sheet = build_team_sheet(
        session, away, fixture.game_week_number, live_mode=league.live_mode
    )

    result = resolve_match(home_sheet, away_sheet, context, flags=flags)

    fixture.home_score = result.home.score
    fixture.away_score = result.away.score
    fixture.resolved_at = _now()
    fixture.needs_recompute = False
    fixture.report = _serialise(result)

    # Spec 3.5: the Valise is paid for whether or not it cancelled anything.
    if result.home.valise_compensation:
        home.budget += result.home.valise_compensation // 1_000_000
    if result.away.valise_compensation:
        away.budget += result.away.valise_compensation // 1_000_000

    session.flush()
    return result


def resolve_game_week(
    session: Session, league: League, game_week: int, *, flags: EngineFlags = DEFAULT_FLAGS
) -> list[MatchResult]:
    fixtures = session.execute(
        select(LeagueMatch).where(
            LeagueMatch.league_id == league.id,
            LeagueMatch.game_week_number == game_week,
            LeagueMatch.resolved_at.is_(None),
        )
    ).scalars().all()
    return [resolve_league_match(session, fixture, flags=flags) for fixture in fixtures]


def flag_for_recompute(session: Session, player_id: str, game_week: int) -> list[LeagueMatch]:
    """Spec 4.5: a rating changed after the fact. Flag, never recompute silently."""
    affected = session.execute(
        select(LeagueMatch)
        .join(Lineup, Lineup.game_week_number == LeagueMatch.game_week_number)
        .join(LineupSlot, LineupSlot.lineup_id == Lineup.id)
        .where(
            LineupSlot.player_id == player_id,
            LeagueMatch.game_week_number == game_week,
            LeagueMatch.resolved_at.is_not(None),
            Lineup.participant_id.in_(
                [LeagueMatch.home_participant_id, LeagueMatch.away_participant_id]
            ),
        )
    ).scalars().unique().all()
    for fixture in affected:
        fixture.needs_recompute = True
    session.flush()
    return list(affected)
