"""Running a benchmark: a mercato by rounds, then lineups, then the matches.

The order is the game's own (spec 7): a league and its participants, a closed-bid
mercato round by round, a lineup per game week, then the fixtures resolved by the
engine. Agents only ever supply decisions; every rule is applied by the code that
already enforces it for a human.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from mpg.bench.agents import Agent
from mpg.bench.types import AgentCall
from mpg.bench.views import lineup_view, mercato_view
from mpg.config import MERCATO_BASE_ROUNDS
from mpg.db.models import (
    BonusUsage,
    Championship,
    League,
    LeagueMatch,
    LeagueStatus,
    Participant,
    User,
)
from mpg.mercato.rules import BidRejected, quota_satisfied
from mpg.mercato.service import (
    close_mercato,
    current_round,
    open_mercato,
    place_bid,
    resolve_round,
    squad_positions,
    validate_round,
)
from mpg.services.leagues import generate_fixtures
from mpg.services.lineups import LineupError, save_lineup
from mpg.services.matchday import resolve_game_week
from mpg.services.standings import as_table, standings

log = logging.getLogger(__name__)


@dataclass(slots=True)
class AgentReport:
    """What one agent did, and how well it held together."""

    name: str
    team_name: str
    participant_id: int
    calls: list[AgentCall] = field(default_factory=list)
    budget_spent: int = 0
    squad_complete: bool = False
    rank: int | None = None
    points: int = 0
    goals_for: int = 0
    goals_against: int = 0
    real_goals: int = 0
    mpg_goals: int = 0

    @property
    def failures(self) -> int:
        return sum(1 for call in self.calls if call.failure)

    @property
    def reliability(self) -> float:
        """Share of decisions the game could take as given."""
        return 1.0 - (self.failures / len(self.calls)) if self.calls else 1.0

    @property
    def seconds(self) -> float:
        return sum(call.seconds for call in self.calls)

    @property
    def tokens(self) -> int:
        return sum(call.tokens for call in self.calls)


@dataclass(slots=True)
class BenchResult:
    league_id: int
    agents: list[AgentReport]
    table: list[dict] = field(default_factory=list)
    game_weeks: list[int] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(UTC)


def build_league(
    session: Session, agents: list[Agent], *, championship_id: int = 1,
    first_game_week: int = 1, name: str = "Benchmark",
) -> tuple[League, dict[int, Agent]]:
    """One participant per agent. The size must be a legal league size."""
    league = League(
        name=name, code=f"BENCH{int(_now().timestamp()) % 100000:05d}",
        championship_id=championship_id, size=len(agents), return_legs=True,
        status=LeagueStatus.CREATED, first_game_week=first_game_week,
    )
    session.add(league)
    session.flush()

    by_participant: dict[int, Agent] = {}
    for index, agent in enumerate(agents):
        user = User(
            email=f"agent{index}-{league.code.lower()}@bench.local",
            display_name=agent.name, password_hash="x",
        )
        session.add(user)
        session.flush()
        participant = Participant(
            league_id=league.id, user_id=user.id, team_name=agent.name,
            budget=0, is_admin=index == 0,
        )
        session.add(participant)
        session.flush()
        by_participant[participant.id] = agent
    session.flush()
    return league, by_participant


def run_mercato(
    session: Session,
    league: League,
    agents: dict[int, Agent],
    reports: dict[int, AgentReport],
    *,
    rounds: int = MERCATO_BASE_ROUNDS,
    before_game_week: int | None = None,
    season: int | None = None,
) -> None:
    """The closed-bid mercato, round by round, exactly as a human would play it."""
    open_mercato(session, league)

    for _ in range(rounds):
        round_ = current_round(session, league.id)
        if round_ is None:
            break
        round_.deadline_at = _now() + timedelta(hours=1)
        session.flush()

        for participant_id, agent in agents.items():
            participant = session.get(Participant, participant_id)
            if participant.mercato_closed or participant.budget <= 0:
                continue
            view = mercato_view(
                session, participant, round_,
                before_game_week=before_game_week, season=season,
            )
            if view.missing == 0:
                validate_round(session, participant, round_)
                continue

            decisions, call = agent.bid(view)
            reports[participant_id].calls.append(call)
            for decision in decisions:
                try:
                    place_bid(session, participant, round_, decision.player_id, decision.amount)
                except BidRejected as error:
                    # The validator should have caught this; record it rather than stop.
                    call.failure = f"{call.failure + ' | ' if call.failure else ''}{error}"
            validate_round(session, participant, round_)

        resolve_round(session, round_.id)
        session.flush()

    league = session.get(League, league.id)
    if league.status is LeagueStatus.MERCATO:
        # The draft tops up whatever the agents failed to buy, so no run ends with a
        # squad that cannot be fielded.
        close_mercato(session, league)
        session.flush()

    for participant_id, report in reports.items():
        participant = session.get(Participant, participant_id)
        report.budget_spent = 500 - participant.budget
        report.squad_complete = quota_satisfied(squad_positions(session, participant_id))


def run_game_week(
    session: Session,
    league: League,
    agents: dict[int, Agent],
    reports: dict[int, AgentReport],
    game_week: int,
    season: int | None = None,
) -> None:
    """Every agent picks a team, then the fixtures of that week are resolved."""
    fixtures = session.execute(
        select(LeagueMatch).where(
            LeagueMatch.league_id == league.id, LeagueMatch.game_week_number == game_week
        )
    ).scalars().all()
    opponents: dict[int, tuple[int, bool]] = {}
    for fixture in fixtures:
        opponents[fixture.home_participant_id] = (fixture.away_participant_id, True)
        opponents[fixture.away_participant_id] = (fixture.home_participant_id, False)

    for participant_id, agent in agents.items():
        participant = session.get(Participant, participant_id)
        opponent_id, at_home = opponents.get(participant_id, (None, True))
        opponent = session.get(Participant, opponent_id) if opponent_id else None

        view = lineup_view(
            session, participant, game_week, opponent=opponent, at_home=at_home,
            season=season,
        )
        answer, call = agent.pick(view)
        reports[participant_id].calls.append(call)

        try:
            save_lineup(
                session, participant, game_week,
                formation=answer.formation, starters=answer.starters, bench=answer.bench,
            )
        except LineupError as error:
            call.failure = f"{call.failure + ' | ' if call.failure else ''}{error}"
            log.warning("lineup refused for %s: %s", agent.name, error)
            continue

        if answer.captain:
            already = session.execute(
                select(BonusUsage).where(
                    BonusUsage.participant_id == participant.id,
                    BonusUsage.game_week_number == game_week,
                    BonusUsage.bonus_type == "captain",
                )
            ).first()
            if already is None:
                session.add(BonusUsage(
                    participant_id=participant.id, game_week_number=game_week,
                    bonus_type="captain", target_player_id=answer.captain,
                ))
    session.flush()
    resolve_game_week(session, league, game_week)
    session.flush()


def run_bench(
    session: Session,
    agents: list[Agent],
    *,
    game_weeks: list[int],
    championship_id: int = 1,
    mercato_rounds: int = MERCATO_BASE_ROUNDS,
    name: str = "Benchmark",
) -> BenchResult:
    """A whole run: league, mercato, then one lineup and one set of fixtures per week."""
    league, by_participant = build_league(
        session, agents, championship_id=championship_id,
        first_game_week=game_weeks[0], name=name,
    )
    reports = {
        participant_id: AgentReport(
            name=agent.name,
            team_name=session.get(Participant, participant_id).team_name,
            participant_id=participant_id,
        )
        for participant_id, agent in by_participant.items()
    }

    championship = session.get(Championship, championship_id)
    season = championship.current_season if championship else None
    run_mercato(
        session, league, by_participant, reports, rounds=mercato_rounds,
        before_game_week=game_weeks[0], season=season,
    )
    generate_fixtures(session, league)
    session.flush()

    played = []
    for game_week in game_weeks:
        run_game_week(session, league, by_participant, reports, game_week, season=season)
        played.append(game_week)

    table = as_table(standings(session, league))
    by_id = {row["participant_id"]: row for row in table}
    for participant_id, report in reports.items():
        row = by_id.get(participant_id)
        if row:
            report.rank = row["rank"]
            report.points = row["points"]
            report.goals_for = row["goals_for"]
            report.goals_against = row["goals_against"]

    # The real/virtual split comes from the stored reports, at no extra cost.
    for fixture in session.execute(
        select(LeagueMatch).where(
            LeagueMatch.league_id == league.id, LeagueMatch.report.is_not(None)
        )
    ).scalars():
        for side, participant_id in (
            ("home", fixture.home_participant_id),
            ("away", fixture.away_participant_id),
        ):
            report = reports.get(participant_id)
            if report:
                body = fixture.report[side]
                report.real_goals += body.get("real_goals", 0)
                report.mpg_goals += body.get("mpg_goals", 0)

    return BenchResult(
        league_id=league.id,
        agents=sorted(reports.values(), key=lambda r: (r.rank or 99, r.name)),
        table=table,
        game_weeks=played,
    )
