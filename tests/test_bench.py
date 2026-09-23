"""The benchmark harness: observations, repair, and a whole run without a model.

The run itself is exercised with heuristic agents, so the loop -- mercato by rounds,
lineups, matches, standings -- is covered without a server anywhere near it. The
model-driven agent is covered against a scripted completion function, including the
ways a small model actually fails.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from fixtures.league import make_session
from mpg.bench.agents import HeuristicAgent, LlmAgent, extract_json
from mpg.bench.ollama import Completion
from mpg.bench.runner import run_bench
from mpg.bench.types import (
    BidDecision,
    LineupAnswer,
    LineupView,
    MercatoAnswer,
    MercatoView,
    PlayerCard,
)
from mpg.bench.validation import clamp_bids, repair_lineup
from mpg.bench.views import lineup_view, mercato_view
from mpg.db.models import Championship, Participant, Performance, Roster
from mpg.engine.lines import Line
from mpg.ingest.client import MpgClient
from mpg.ingest.jobs import ingest_clubs, ingest_pool
from mpg.mercato.rules import quota_satisfied
from mpg.mercato.service import squad_positions

DATA = Path(__file__).parent / "fixtures" / "data"
PAST_WEEK, PLAY_WEEK = 4, 5


class Offline(MpgClient):
    def __init__(self) -> None:
        pass

    def clubs(self) -> dict:
        return json.loads((DATA / "clubs_l1.json").read_text())

    def players_pool(self, championship_id: int, season: int | None = None) -> dict:
        return json.loads((DATA / "pool_l1_2026.json").read_text())


@pytest.fixture
def ligue1():
    session = make_session()
    session.add(Championship(id=1, code="fr_l1", name="Ligue 1", current_season=2026))
    session.flush()
    ingest_clubs(session, Offline())
    ingest_pool(session, Offline(), 1)
    session.flush()
    yield session
    session.close()


def cards(lines: list[Line], quotation: int = 10) -> tuple[PlayerCard, ...]:
    return tuple(
        PlayerCard(f"p{i}", f"P{i}", line, quotation + i, past_ratings=(5.0,))
        for i, line in enumerate(lines)
    )


SQUAD_LINES = [Line.G] * 2 + [Line.D] * 6 + [Line.M] * 6 + [Line.A] * 4


# ------------------------------------------------------------- a whole run, no model

def test_a_full_run_with_heuristic_agents(ligue1):
    """Mercato by rounds, then a lineup, then the match -- end to end."""
    session = ligue1
    agents = [HeuristicAgent(name="alpha"), HeuristicAgent(name="beta", premium=0.3)]

    result = run_bench(session, agents, game_weeks=[PLAY_WEEK], mercato_rounds=5)

    assert len(result.agents) == 2
    assert result.game_weeks == [PLAY_WEEK]

    for report in result.agents:
        # Nobody ends a run unable to field a team.
        assert report.squad_complete, f"{report.name} cannot field a lineup"
        assert quota_satisfied(squad_positions(session, report.participant_id))
        assert report.rank in (1, 2)
        assert report.points in (0, 1, 3)
        # A heuristic agent never fails: it is the control arm.
        assert report.failures == 0
        assert report.reliability == 1.0

    # The fixture really was played, and the table adds up.
    assert sum(row["played"] for row in result.table) == 2
    assert sum(r.goals_for for r in result.agents) == sum(
        r.goals_against for r in result.agents
    )
    assert sum(r.points for r in result.agents) in (2, 3)


def test_the_mercato_actually_spent_the_budget(ligue1):
    session = ligue1
    result = run_bench(
        session, [HeuristicAgent(name="alpha"), HeuristicAgent(name="beta")],
        game_weeks=[PLAY_WEEK],
    )
    for report in result.agents:
        assert report.budget_spent > 0, "an agent that bought nothing did not play"
        rosters = session.execute(
            select(Roster).where(Roster.participant_id == report.participant_id)
        ).scalars().all()
        assert len(rosters) >= 18
        # And no player is owned twice across the league.
    owned = [
        row.player_id for row in session.execute(select(Roster)).scalars()
    ]
    assert len(owned) == len(set(owned))


# ------------------------------------------------------------------- the observation

def test_the_lineup_view_never_leaks_the_week_being_played(ligue1):
    """The rule that makes the benchmark mean anything."""
    session = ligue1
    run_bench(session, [HeuristicAgent(name="a"), HeuristicAgent(name="b")],
              game_weeks=[PLAY_WEEK])

    participant = session.execute(select(Participant)).scalars().first()
    view = lineup_view(session, participant, PLAY_WEEK)

    played = {
        row.player_id: row.rating
        for row in session.execute(
            select(Performance).where(Performance.game_week_number == PLAY_WEEK)
        ).scalars()
    }
    earlier = {
        row.player_id: row.rating
        for row in session.execute(
            select(Performance).where(Performance.game_week_number == PAST_WEEK)
        ).scalars()
    }
    assert view.squad

    leaked = []
    for card in view.squad:
        this_week = played.get(card.player_id)
        before = earlier.get(card.player_id)
        # The card may carry the earlier rating, never the one about to be scored --
        # unless the two happen to be equal, which proves nothing either way.
        if this_week is not None and this_week in card.past_ratings and this_week != before:
            leaked.append(card.player_id)
    assert not leaked, f"the rating of game week {PLAY_WEEK} reached the agent: {leaked}"

    # What it does carry is the earlier form.
    with_form = [card for card in view.squad if card.past_ratings]
    assert with_form, "the agent should see the previous game week's ratings"


def test_the_mercato_view_shows_only_its_own_bids(ligue1):
    from mpg.bench.runner import build_league
    from mpg.mercato.service import current_round, open_mercato, place_bid

    session = ligue1
    league, by_participant = build_league(
        session, [HeuristicAgent(name="a"), HeuristicAgent(name="b")]
    )
    open_mercato(session, league)
    round_ = current_round(session, league.id)
    first, second = (session.get(Participant, pid) for pid in by_participant)

    pool = mercato_view(session, first, round_).free_players
    target = pool[0].player_id
    place_bid(session, second, round_, target, pool[0].quotation + 7)

    view = mercato_view(session, first, round_)
    assert view.own_bids == (), "another participant's bid reached this view"
    # And the player looks exactly like any other free player.
    assert any(card.player_id == target for card in view.free_players)
    assert view.budget == 500


# --------------------------------------------------------- what small models get wrong

def scripted(text: str) -> Completion:
    return Completion(text=text, seconds=0.1, tokens=42)


def test_a_model_that_answers_prose_falls_back_without_breaking(ligue1):
    agent = LlmAgent(name="bavard", complete=lambda _prompt: scripted("Je pense que..."))
    view = MercatoView("T", 1, 5, 500, {Line.G: 2, Line.D: 6, Line.M: 6, Line.A: 4},
                       (), cards([Line.G] * 4 + [Line.D] * 8 + [Line.M] * 8 + [Line.A] * 6))

    bids, call = agent.bid(view)

    assert bids, "the fallback must keep the run going"
    assert call.failure == "unparseable"
    assert call.model == "bavard"


def test_json_wrapped_in_prose_is_still_read():
    assert extract_json('Voici:\n```json\n{"bids": []}\n```\nvoilà') == {"bids": []}
    assert extract_json('blah {"a": 1} puis {"b": 2}') == {"a": 1}
    assert extract_json("pas de json ici") is None
    assert extract_json("") is None


def test_a_bid_under_the_quotation_is_raised_not_dropped():
    free = cards([Line.A] * 3, quotation=20)
    view = MercatoView("T", 1, 5, 500, {Line.A: 4}, (), free)
    answer = MercatoAnswer(bids=[BidDecision(free[0].player_id, 1)])

    kept, repair = clamp_bids(answer, view)

    assert len(kept) == 1
    assert kept[0].amount == free[0].quotation
    assert any("under its quotation" in problem for problem in repair.problems)


def test_a_bid_that_would_break_the_budget_is_dropped():
    free = cards([Line.A] * 3, quotation=20)
    view = MercatoView("T", 1, 5, 30, {Line.A: 4}, (), free)
    answer = MercatoAnswer(bids=[BidDecision(card.player_id, 25) for card in free])

    kept, repair = clamp_bids(answer, view)

    assert sum(bid.amount for bid in kept) <= 30
    assert any("would not leave enough" in problem for problem in repair.problems)


def test_a_bid_on_a_player_it_does_not_see_is_refused():
    view = MercatoView("T", 1, 5, 500, {Line.A: 4}, (), cards([Line.A] * 2))
    answer = MercatoAnswer(bids=[BidDecision("mbappe", 50)])

    kept, repair = clamp_bids(answer, view)

    assert kept == []
    assert any("not a free player" in problem for problem in repair.problems)


def test_an_illegal_lineup_is_repaired_into_a_legal_one():
    squad = cards(SQUAD_LINES)
    view = LineupView("T", 5, squad, ("4-4-2", "4-3-3"), {})
    answer = LineupAnswer(
        formation="6-6-6",
        starters=["inconnu", squad[0].player_id, squad[0].player_id],
        bench=[],
        captain=squad[0].player_id,          # the goalkeeper
    )

    fixed, repair = repair_lineup(answer, view)

    assert fixed.formation == "4-4-2"
    assert len(fixed.starters) == 11
    assert len(fixed.bench) == 7
    assert len(set(fixed.starters) & set(fixed.bench)) == 0
    assert fixed.captain is None, "a goalkeeper cannot be captain"
    # A goalkeeper is on the bench, which the rules require.
    by_id = {card.player_id: card for card in squad}
    assert any(by_id[pid].line is Line.G for pid in fixed.bench)
    assert repair.problems


def test_a_repaired_lineup_is_accepted_by_the_real_validator(ligue1):
    """The repair is only worth anything if the game then takes it."""
    from mpg.services.lineups import validate_lineup

    session = ligue1
    result = run_bench(session, [HeuristicAgent(name="a"), HeuristicAgent(name="b")],
                       game_weeks=[PLAY_WEEK])
    participant = session.get(Participant, result.agents[0].participant_id)
    view = lineup_view(session, participant, PLAY_WEEK)

    nonsense = LineupAnswer(formation="9-9-9", starters=["x"], bench=["y"])
    fixed, _repair = repair_lineup(nonsense, view)

    validate_lineup(
        session, participant, formation=fixed.formation,
        starters=fixed.starters, bench=fixed.bench,
    )


def test_the_report_counts_failures_and_time():
    squad = cards(SQUAD_LINES)
    view = LineupView("T", 5, squad, ("4-4-2",), {})
    agent = LlmAgent(name="cassé", complete=lambda _p: Completion("", 1.5, 0, "timeout"))

    answer, call = agent.pick(view)

    assert call.failure == "timeout"
    assert call.seconds == 1.5
    assert len(answer.starters) == 11, "the fallback still produced a legal team"


# ---------------------------------------------------- short handles, long real ids

def test_cards_carry_a_short_handle(ligue1):
    """Real ids look like `mpg_championship_player_512126`; agents read `A07`."""
    from mpg.bench.runner import build_league
    from mpg.mercato.service import current_round, open_mercato

    session = ligue1
    league, by_participant = build_league(
        session, [HeuristicAgent(name="a"), HeuristicAgent(name="b")]
    )
    open_mercato(session, league)
    round_ = current_round(session, league.id)
    participant = session.get(Participant, next(iter(by_participant)))

    view = mercato_view(session, participant, round_)
    assert view.free_players
    for card in view.free_players[:20]:
        assert card.handle, "every card needs a handle"
        assert len(card.handle) <= 4
        assert card.handle[0] in "GDMA"
    # Handles are unique within a view, so resolution is never ambiguous.
    handles = [card.handle for card in view.free_players]
    assert len(handles) == len(set(handles))


@pytest.mark.parametrize(
    "given",
    [
        "A01",                                    # the handle
        "a01",                                    # the handle, lower case
        "mpg_championship_player_999",            # the real id
        "999",                                    # the numeric tail, which models emit
    ],
)
def test_a_player_can_be_named_several_ways(given):
    from dataclasses import replace

    free = (
        replace(
            PlayerCard("mpg_championship_player_999", "Untel", Line.A, 12),
            handle="A01",
        ),
    )
    view = MercatoView("T", 1, 5, 500, {Line.A: 4}, (), free)

    kept, repair = clamp_bids(MercatoAnswer(bids=[BidDecision(given, 20)]), view)

    assert len(kept) == 1, repair.problems
    assert kept[0].player_id == "mpg_championship_player_999"


def test_an_ambiguous_name_is_refused_rather_than_guessed():
    """Two players whose numeric tails collide must not resolve to either."""
    from dataclasses import replace

    free = (
        replace(PlayerCard("club_a_7", "Un", Line.A, 10), handle="A01"),
        replace(PlayerCard("club_b_7", "Deux", Line.A, 10), handle="A02"),
    )
    view = MercatoView("T", 1, 5, 500, {Line.A: 4}, (), free)

    kept, _repair = clamp_bids(MercatoAnswer(bids=[BidDecision("7", 20)]), view)

    assert kept == [], "an ambiguous tail must not be resolved"
    # The unambiguous handles still work.
    kept, _ = clamp_bids(MercatoAnswer(bids=[BidDecision("A02", 20)]), view)
    assert kept[0].player_id == "club_b_7"


def test_a_lineup_can_be_given_in_handles(ligue1):
    session = ligue1
    result = run_bench(session, [HeuristicAgent(name="a"), HeuristicAgent(name="b")],
                       game_weeks=[PLAY_WEEK])
    participant = session.get(Participant, result.agents[0].participant_id)
    view = lineup_view(session, participant, PLAY_WEEK)

    by_line = view.by_line()
    answer = LineupAnswer(
        formation="4-4-2",
        starters=[by_line[Line.G][0].handle]
        + [c.handle for c in by_line[Line.D][:4]]
        + [c.handle for c in by_line[Line.M][:4]]
        + [c.handle for c in by_line[Line.A][:2]],
        bench=[by_line[Line.G][1].handle]
        + [c.handle for c in by_line[Line.D][4:6]]
        + [c.handle for c in by_line[Line.M][4:6]]
        + [c.handle for c in by_line[Line.A][2:4]],
        captain=by_line[Line.A][0].handle,
    )

    fixed, repair = repair_lineup(answer, view)

    assert repair.clean, repair.problems
    assert len(fixed.starters) == 11
    # Handles were resolved back to real ids the game will accept.
    assert all(pid.startswith("mpg_") for pid in fixed.starters)
    assert fixed.captain and fixed.captain.startswith("mpg_")
