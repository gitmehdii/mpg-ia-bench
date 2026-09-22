"""Leagues, fixture lists, lineups, bonus quotas, standings and ingestion."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from fixtures.league import make_league, make_session
from mpg.config import BONUS_QUOTAS
from mpg.db.models import (
    LeagueMatch,
    LeagueStatus,
    Roster,
    User,
)
from mpg.services.calendar import full_calendar, round_robin
from mpg.services.leagues import LeagueError, create_league, generate_fixtures, join_league
from mpg.services.lineups import BonusError, LineupError, pose_bonus, save_lineup
from mpg.services.standings import as_table, standings

GK, CB, FB, DM, AM, FW = 10, 20, 21, 30, 31, 40


@pytest.fixture
def league4():
    session = make_session()
    league, participants, round_ = make_league(session, size=4)
    yield session, league, participants
    session.close()


# ------------------------------------------------------------------------ calendar

@pytest.mark.parametrize("size", [2, 4, 6, 8, 10])
def test_every_pair_meets_once_each_way(size):
    ids = list(range(1, size + 1))
    weeks = full_calendar(ids)
    assert len(weeks) == 2 * (size - 1)
    pairs = [(h, a) for week in weeks for h, a in week]
    assert len(pairs) == len(set(pairs)) == size * (size - 1)
    # Home advantage is shared out evenly, which matters because the home side wins
    # tied duels (golden rule 1).
    home_counts = {pid: sum(1 for h, _ in pairs if h == pid) for pid in ids}
    assert set(home_counts.values()) == {size - 1}


def test_a_league_without_return_legs_plays_half_as_many_weeks():
    assert len(full_calendar([1, 2, 3, 4], return_legs=False)) == 3


def test_an_odd_number_of_participants_is_refused():
    with pytest.raises(ValueError, match="even number"):
        round_robin([1, 2, 3])


def test_fixtures_are_laid_over_the_championship_game_weeks(league4):
    session, league, participants = league4
    league.first_game_week = 5
    fixtures = generate_fixtures(session, league)
    weeks = sorted({f.game_week_number for f in fixtures})
    assert weeks == [5, 6, 7, 8, 9, 10]
    # Generating twice does not duplicate the fixture list.
    assert len(generate_fixtures(session, league)) == len(fixtures)


# ------------------------------------------------------------------------- leagues

def test_league_size_is_restricted(league4):
    session, _league, _participants = league4
    user = User(email="x@example.com", display_name="X", password_hash="x")
    session.add(user)
    session.flush()
    with pytest.raises(LeagueError, match="2, 4, 6, 8 or 10"):
        create_league(
            session, name="Bad", championship_id=1, size=5, creator=user, team_name="T"
        )


def test_a_full_league_refuses_new_participants(league4):
    session, league, _participants = league4
    league.status = LeagueStatus.CREATED
    user = User(email="e@example.com", display_name="E", password_hash="x")
    session.add(user)
    session.flush()
    with pytest.raises(LeagueError, match="full"):
        join_league(session, league, user, "Team E")


# ------------------------------------------------------------------------ lineups

def _give_squad(session, participant, extra: dict[int, int] | None = None) -> dict[int, list[str]]:
    """Hand a participant a legal 2/6/6/4 squad and return its ids by position."""
    wanted = {GK: 2, CB: 3, FB: 3, DM: 3, AM: 3, FW: 4}
    wanted.update(extra or {})
    squad: dict[int, list[str]] = {}
    for ultra, count in wanted.items():
        squad[ultra] = []
        for index in range(1, count + 1):
            player_id = f"pool_{ultra}_{index:02d}"
            session.add(
                Roster(
                    participant_id=participant.id,
                    player_id=player_id,
                    bought_at=datetime(2026, 9, 21, tzinfo=UTC),
                    bought_price=1,
                )
            )
            squad[ultra].append(player_id)
    session.flush()
    return squad


def test_a_valid_442_is_accepted(league4):
    session, league, (a, *_) = league4
    squad = _give_squad(session, a)
    starters = [
        squad[GK][0],
        *squad[CB][:2], *squad[FB][:2],
        *squad[DM][:2], *squad[AM][:2],
        *squad[FW][:2],
    ]
    bench = [squad[GK][1], squad[CB][2], squad[FB][2], squad[DM][2], squad[AM][2],
             squad[FW][2], squad[FW][3]]
    lineup = save_lineup(
        session, a, 1, formation="4-4-2", starters=starters, bench=bench
    )
    assert len(lineup.slots) == 18


def test_the_bench_must_hold_a_goalkeeper(league4):
    session, league, (a, *_) = league4
    squad = _give_squad(session, a, extra={GK: 2, CB: 4})
    starters = [squad[GK][0], *squad[CB][:2], *squad[FB][:2], *squad[DM][:2],
                *squad[AM][:2], *squad[FW][:2]]
    bench = [squad[CB][2], squad[CB][3], squad[FB][2], squad[DM][2], squad[AM][2],
             squad[FW][2], squad[FW][3]]
    with pytest.raises(LineupError, match="goalkeeper"):
        save_lineup(session, a, 1, formation="4-4-2", starters=starters, bench=bench)


def test_the_xi_must_match_the_formation(league4):
    session, league, (a, *_) = league4
    squad = _give_squad(session, a)
    starters = [squad[GK][0], *squad[CB][:2], *squad[FB][:2], *squad[DM][:2],
                *squad[AM][:2], *squad[FW][:2]]
    bench = [squad[GK][1], squad[CB][2], squad[FB][2], squad[DM][2], squad[AM][2],
             squad[FW][2], squad[FW][3]]
    with pytest.raises(LineupError, match="does not match"):
        save_lineup(session, a, 1, formation="4-3-3", starters=starters, bench=bench)


def test_the_424_needs_its_bonus():
    """The 4-2-4 needs the bonus, and the bonus itself only exists from 6 participants
    upwards (spec 3.5)."""
    session = make_session()
    league, (a, *_), _round = make_league(session, size=6)
    squad = _give_squad(session, a, extra={FW: 5})
    starters = [squad[GK][0], *squad[CB][:2], *squad[FB][:2], *squad[DM][:2],
                *squad[FW][:4]]
    bench = [squad[GK][1], squad[CB][2], squad[FB][2], squad[DM][2], squad[AM][0],
             squad[AM][1], squad[FW][4]]
    with pytest.raises(LineupError, match="424 bonus"):
        save_lineup(session, a, 1, formation="4-2-4", starters=starters, bench=bench)

    pose_bonus(session, a, 1, "formation_424")
    lineup = save_lineup(session, a, 1, formation="4-2-4", starters=starters, bench=bench)
    assert lineup.formation == "4-2-4"


def test_a_player_outside_the_squad_is_refused(league4):
    session, league, (a, *_) = league4
    squad = _give_squad(session, a)
    starters = [squad[GK][0], *squad[CB][:2], *squad[FB][:2], *squad[DM][:2],
                *squad[AM][:2], squad[FW][0], "Tolisso"]
    bench = [squad[GK][1], squad[CB][2], squad[FB][2], squad[DM][2], squad[AM][2],
             squad[FW][2], squad[FW][3]]
    with pytest.raises(LineupError, match="not in the squad"):
        save_lineup(session, a, 1, formation="4-4-2", starters=starters, bench=bench)


# ------------------------------------------------------------------------- bonuses

def test_only_one_limited_bonus_per_game_week(league4):
    session, league, (a, *_) = league4
    _give_squad(session, a)
    pose_bonus(session, a, 1, "valise")
    with pytest.raises(BonusError, match="only one bonus per game week"):
        pose_bonus(session, a, 1, "mcdo", target_player_id="pool_40_01")


def test_defense_and_captain_are_unlimited(league4):
    session, league, (a, *_) = league4
    _give_squad(session, a)
    pose_bonus(session, a, 1, "valise")
    pose_bonus(session, a, 1, "defense")
    pose_bonus(session, a, 1, "captain", target_player_id="pool_40_01")
    # And again the next game week, with no quota in the way.
    for week in range(2, 12):
        pose_bonus(session, a, week, "defense")
        pose_bonus(session, a, week, "captain", target_player_id="pool_40_01")


def test_the_captain_can_never_be_the_goalkeeper(league4):
    session, league, (a, *_) = league4
    _give_squad(session, a)
    with pytest.raises(BonusError, match="goalkeeper"):
        pose_bonus(session, a, 1, "captain", target_player_id="pool_10_01")


def test_season_quota_is_enforced_per_league_size(league4):
    session, league, (a, *_) = league4
    _give_squad(session, a)
    assert BONUS_QUOTAS["valise"][4] == 1
    pose_bonus(session, a, 1, "valise")
    with pytest.raises(BonusError, match="quota exhausted"):
        pose_bonus(session, a, 2, "valise")


def test_a_bonus_unavailable_at_this_size_is_refused(league4):
    session, league, (a, *_) = league4
    _give_squad(session, a)
    # The Cheat Code only exists from 8 participants upwards.
    assert BONUS_QUOTAS["cheat_code"][4] == 0
    with pytest.raises(BonusError, match="not available in a league of 4"):
        pose_bonus(session, a, 1, "cheat_code")


# ----------------------------------------------------------------------- standings

def _resolve(session, league, week, home, away, home_score, away_score):
    session.add(
        LeagueMatch(
            league_id=league.id,
            game_week_number=week,
            home_participant_id=home.id,
            away_participant_id=away.id,
            home_score=home_score,
            away_score=away_score,
            resolved_at=datetime(2026, 9, 21, tzinfo=UTC),
        )
    )
    session.flush()


def test_points_win_draw_loss(league4):
    session, league, (a, b, c, d) = league4
    _resolve(session, league, 1, a, b, 3, 1)
    _resolve(session, league, 1, c, d, 2, 2)

    table = {row["team_name"]: row for row in as_table(standings(session, league))}
    assert table["Team A"]["points"] == 3
    assert table["Team B"]["points"] == 0
    assert table["Team C"]["points"] == 1
    assert table["Team D"]["points"] == 1
    assert table["Team A"]["rank"] == 1


def test_goal_difference_then_attack_break_the_tie(league4):
    session, league, (a, b, c, d) = league4
    # A and B both end on 3 points; A has the better difference.
    _resolve(session, league, 1, a, c, 5, 0)
    _resolve(session, league, 1, b, d, 2, 0)

    table = as_table(standings(session, league))
    assert [row["team_name"] for row in table[:2]] == ["Team A", "Team B"]

    # Same difference now, so the better attack decides.
    session.execute(select(LeagueMatch))
    for fixture in session.execute(select(LeagueMatch)).scalars():
        if fixture.home_participant_id == b.id:
            fixture.home_score, fixture.away_score = 7, 2
    session.flush()
    table = as_table(standings(session, league))
    assert table[0]["team_name"] == "Team B"


def test_head_to_head_breaks_a_full_tie(league4):
    session, league, (a, b, c, d) = league4
    # A and B are level on points, difference and goals scored, so the head-to-head
    # result decides.
    _resolve(session, league, 1, a, b, 2, 1)
    _resolve(session, league, 2, b, a, 1, 2)
    _resolve(session, league, 3, a, c, 1, 2)
    _resolve(session, league, 4, b, c, 1, 2)
    _resolve(session, league, 5, c, a, 1, 1)
    _resolve(session, league, 6, c, b, 1, 1)

    table = as_table(standings(session, league))
    ranks = {row["team_name"]: row["rank"] for row in table}
    assert ranks["Team A"] < ranks["Team B"]


def test_an_unresolved_fixture_is_not_counted(league4):
    session, league, (a, b, c, d) = league4
    session.add(
        LeagueMatch(
            league_id=league.id, game_week_number=1,
            home_participant_id=a.id, away_participant_id=b.id,
        )
    )
    session.flush()
    table = {row["team_name"]: row for row in as_table(standings(session, league))}
    assert table["Team A"]["played"] == 0


# ----------------------------------------------------------------------- ingestion

def test_a_revised_rating_flags_the_fixture_instead_of_recomputing_it(league4):
    """Spec 4.5: never change a published result in silence."""
    from mpg.ingest.jobs import _flag_affected_fixtures
    from mpg.services.lineups import save_lineup

    session, league, (a, b, c, d) = league4
    squad = _give_squad(session, a)
    starters = [squad[GK][0], *squad[CB][:2], *squad[FB][:2], *squad[DM][:2],
                *squad[AM][:2], *squad[FW][:2]]
    bench = [squad[GK][1], squad[CB][2], squad[FB][2], squad[DM][2], squad[AM][2],
             squad[FW][2], squad[FW][3]]
    save_lineup(session, a, 1, formation="4-4-2", starters=starters, bench=bench)
    _resolve(session, league, 1, a, b, 2, 1)

    flagged = _flag_affected_fixtures(session, squad[FW][0], 1)
    session.flush()

    assert flagged == 1
    fixture = session.execute(select(LeagueMatch)).scalars().first()
    assert fixture.needs_recompute is True
    # The published score is untouched.
    assert (fixture.home_score, fixture.away_score) == (2, 1)
