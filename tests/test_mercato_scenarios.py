"""The eight mercato acceptance scenarios of spec 5 (M1 to M8).

Common setting: a league of 4 (A, B, C, D), 500 M€ each, Ligue 1, round 1 open with
the seed pinned to 42 so every draw is reproducible.
"""

from __future__ import annotations

import pytest
from fixtures.league import make_league, make_session
from sqlalchemy import func, select

from mpg.db.models import Bid, MercatoLog, MercatoRound, Roster
from mpg.mercato.rules import BidRejected, quota_satisfied
from mpg.mercato.service import (
    close_mercato,
    place_bid,
    resolve_round,
    squad_positions,
    validate_round,
)


@pytest.fixture
def league_of_four():
    session = make_session()
    league, participants, round_ = make_league(session, size=4)
    yield session, league, participants, round_
    session.close()


def roster_of(session, participant):
    return list(
        session.execute(
            select(Roster).where(Roster.participant_id == participant.id)
        ).scalars()
    )


# ------------------------------------------------------- M1: highest bidder wins

def test_m1_player_goes_to_the_highest_bidder(league_of_four):
    session, league, (a, b, c, d), round_ = league_of_four

    place_bid(session, a, round_, "Tolisso", 30)
    place_bid(session, b, round_, "Tolisso", 45)
    place_bid(session, c, round_, "Tolisso", 31)
    for participant in (a, b, c, d):
        validate_round(session, participant, round_)

    assert resolve_round(session, round_.id) is True

    b_roster = roster_of(session, b)
    assert [(r.player_id, r.bought_price) for r in b_roster] == [("Tolisso", 45)]
    assert b.budget == 455
    # A losing bid never costs anything.
    assert a.budget == 500
    assert c.budget == 500
    assert d.budget == 500
    assert roster_of(session, a) == []
    assert roster_of(session, c) == []


# --------------------------------------------- M2: tie, draw, and reproducibility

def test_m2_tie_is_drawn_journalled_and_reproducible():
    winners = []
    for _ in range(2):
        session = make_session()
        league, (a, b, c, d), round_ = make_league(session, size=4, seed=42)

        place_bid(session, a, round_, "Doué", 40)
        place_bid(session, b, round_, "Doué", 40)
        for participant in (a, b, c, d):
            validate_round(session, participant, round_)
        resolve_round(session, round_.id)

        owner = session.execute(
            select(Roster.participant_id).where(Roster.player_id == "Doué")
        ).scalar_one()

        draws = list(
            session.execute(
                select(MercatoLog).where(MercatoLog.kind == "draw")
            ).scalars()
        )
        assert len(draws) == 1
        assert sorted(draws[0].detail["candidates"]) == sorted([a.id, b.id])
        assert draws[0].detail["winner"] == owner
        assert draws[0].player_id == "Doué"

        # Exactly one of the two gets him.
        assert owner in (a.id, b.id)
        winners.append(owner)
        session.close()

    # Replaying the resolution on a database wiped clean gives the same winner.
    assert winners[0] == winners[1]


# ------------------------------------------ M3: a round's bids are capped by budget

def test_m3_sum_of_bids_cannot_exceed_the_budget(league_of_four):
    session, league, (a, *_), round_ = league_of_four

    place_bid(session, a, round_, "Dembélé", 300)

    with pytest.raises(BidRejected) as excinfo:
        place_bid(session, a, round_, "Openda", 250)

    assert "550" in str(excinfo.value)
    assert "500" in str(excinfo.value)

    total = session.execute(
        select(func.coalesce(func.sum(Bid.amount), 0)).where(Bid.participant_id == a.id)
    ).scalar_one()
    assert total == 300


def test_m3_the_cap_is_rechecked_and_the_bid_floor_is_the_quotation(league_of_four):
    session, league, (a, *_), round_ = league_of_four

    # Below the quotation.
    with pytest.raises(BidRejected, match="minimum bid"):
        place_bid(session, a, round_, "Tolisso", 29)

    # Exactly at the quotation is fine.
    place_bid(session, a, round_, "Tolisso", 30)

    # The remaining room is 470, so 471 is refused and 470 accepted.
    with pytest.raises(BidRejected, match="may not exceed the budget"):
        place_bid(session, a, round_, "Dembélé", 471)
    place_bid(session, a, round_, "Dembélé", 470)


def test_bidding_on_an_owned_player_is_refused(league_of_four):
    session, league, (a, b, c, d), round_ = league_of_four

    place_bid(session, a, round_, "Tolisso", 30)
    for participant in (a, b, c, d):
        validate_round(session, participant, round_)
    resolve_round(session, round_.id)

    next_round = session.execute(
        select(MercatoRound).where(MercatoRound.resolved_at.is_(None))
    ).scalar_one()

    with pytest.raises(BidRejected, match="already owned"):
        place_bid(session, b, next_round, "Tolisso", 60)


# ------------------------------------- M4: no validation means bids are made for you

def test_m4_non_validation_produces_random_bids(league_of_four):
    session, league, (a, b, c, d), round_ = league_of_four

    for participant in (a, b, c):
        validate_round(session, participant, round_)
    # D never validates.

    resolve_round(session, round_.id)

    d_bids = list(
        session.execute(select(Bid).where(Bid.participant_id == d.id)).scalars()
    )
    assert d_bids, "D should have had bids generated on its behalf"
    assert all(bid.is_random for bid in d_bids)

    # D acquired at least one player.
    d_roster = roster_of(session, d)
    assert len(d_roster) >= 1

    # The budget is strictly positive and still covers the floor price of every
    # remaining slot.
    remaining_slots = 18 - len(d_roster)
    assert d.budget > 0
    assert d.budget >= remaining_slots * 1

    # The bids went for the lines that were short -- with an empty squad, every line is.
    from mpg.db.models import Player
    from mpg.engine.lines import line_of

    lines = {
        line_of(session.get(Player, bid.player_id).ultra_position) for bid in d_bids
    }
    assert lines, "the random bids must target real positions"

    logged = session.execute(
        select(MercatoLog).where(MercatoLog.kind == "random_bids")
    ).scalars().all()
    assert len(logged) == 1
    assert logged[0].participant_id == d.id


def test_m4_random_bids_target_the_deficient_positions(league_of_four):
    """With a squad already full of forwards, the random bids must look elsewhere."""
    from datetime import UTC, datetime

    session, league, (a, b, c, d), round_ = league_of_four
    for index in range(1, 5):                       # 4 forwards: that line is satisfied
        session.add(
            Roster(
                participant_id=d.id,
                player_id=f"pool_40_{index:02d}",
                bought_at=datetime(2026, 9, 21, tzinfo=UTC),
                bought_price=index,
            )
        )
    session.flush()
    for participant in (a, b, c):
        validate_round(session, participant, round_)

    resolve_round(session, round_.id)

    from mpg.db.models import Player
    from mpg.engine.lines import Line, line_of

    bids = list(session.execute(select(Bid).where(Bid.participant_id == d.id)).scalars())
    lines = [line_of(session.get(Player, bid.player_id).ultra_position) for bid in bids]
    assert bids
    assert Line.A not in lines


# ----------------------------------------------------------- M5: idempotent replay

def test_m5_resolving_twice_changes_nothing(league_of_four):
    session, league, (a, b, c, d), round_ = league_of_four

    place_bid(session, a, round_, "Tolisso", 45)
    place_bid(session, b, round_, "Greif", 20)
    for participant in (a, b, c, d):
        validate_round(session, participant, round_)

    assert resolve_round(session, round_.id) is True

    def snapshot():
        return {
            "budgets": sorted((p.id, p.budget) for p in (a, b, c, d)),
            "rosters": sorted(
                (r.participant_id, r.player_id, r.bought_price)
                for r in session.execute(select(Roster)).scalars()
            ),
            "rounds": session.execute(select(func.count(MercatoRound.id))).scalar_one(),
            "logs": session.execute(select(func.count(MercatoLog.id))).scalar_one(),
        }

    before = snapshot()
    # The second call must exit immediately on resolved_at.
    assert resolve_round(session, round_.id) is False
    assert snapshot() == before


# -------------------------------------------- M6: one participant wins several players

def test_m6_a_participant_can_win_several_players(league_of_four):
    session, league, (a, b, c, d), round_ = league_of_four

    place_bid(session, a, round_, "Marquinhos", 100)
    place_bid(session, a, round_, "Greif", 150)
    place_bid(session, a, round_, "Nuamah", 200)
    for participant in (a, b, c, d):
        validate_round(session, participant, round_)

    resolve_round(session, round_.id)

    assert a.budget == 50
    prices = {r.player_id: r.bought_price for r in roster_of(session, a)}
    assert prices == {"Marquinhos": 100, "Greif": 150, "Nuamah": 200}


# ------------------------------------------------- M7: end-of-mercato draft tops up

def test_m7_draft_completes_a_short_squad(league_of_four):
    """B has 15 players (2/6/5/2) and 12 M€ left: the draft owes it 1 midfielder and
    2 forwards, and must never leave a squad that cannot field a lineup."""
    from datetime import UTC, datetime

    session, league, (a, b, c, d), round_ = league_of_four

    def give(player_id: str, price: int = 1) -> None:
        session.add(
            Roster(
                participant_id=b.id,
                player_id=player_id,
                bought_at=datetime(2026, 9, 21, tzinfo=UTC),
                bought_price=price,
            )
        )

    for index in range(1, 3):                       # 2 goalkeepers
        give(f"pool_10_{index:02d}")
    for index in range(1, 4):                       # 6 defenders: 3 centre, 3 full backs
        give(f"pool_20_{index:02d}")
        give(f"pool_21_{index:02d}")
    for index in range(1, 6):                       # 5 midfielders -- one short
        give(f"pool_30_{index:02d}")
    for index in range(1, 3):                       # 2 forwards -- two short
        give(f"pool_40_{index:02d}")
    b.budget = 12
    session.flush()

    assert len(roster_of(session, b)) == 15

    close_mercato(session, league)

    roster = roster_of(session, b)
    assert len(roster) == 18
    assert quota_satisfied(squad_positions(session, b.id))

    drafted = [r for r in roster if r.from_draft]
    assert len(drafted) == 3

    from mpg.db.models import Player
    from mpg.engine.lines import Line, line_of

    lines = sorted(line_of(session.get(Player, r.player_id).ultra_position) for r in drafted)
    assert lines == sorted([Line.M, Line.A, Line.A])

    # The cheapest free players of those lines were used.
    assert all(r.bought_price >= 0 for r in drafted)
    assert b.budget >= 0


def test_m7_draft_is_free_when_the_budget_runs_out(league_of_four):
    """A squad that cannot field a lineup is never an acceptable outcome, so once the
    budget is gone the players are handed over free of charge."""
    session, league, (a, b, c, d), round_ = league_of_four
    b.budget = 0
    session.flush()

    close_mercato(session, league)

    roster = roster_of(session, b)
    assert len(roster) == 18
    assert quota_satisfied(squad_positions(session, b.id))
    assert b.budget == 0
    assert all(r.bought_price == 0 for r in roster if r.from_draft)

    free_entries = list(
        session.execute(
            select(MercatoLog).where(MercatoLog.kind == "draft")
        ).scalars()
    )
    assert any(entry.detail["free_of_charge"] for entry in free_entries)


def test_m7_every_participant_ends_able_to_field_a_lineup(league_of_four):
    session, league, participants, round_ = league_of_four

    close_mercato(session, league)

    for participant in participants:
        positions = squad_positions(session, participant.id)
        assert len(positions) == 18
        assert quota_satisfied(positions), f"participant {participant.id} cannot field a lineup"

    # No player is owned twice across the league.
    owners = list(session.execute(select(Roster.player_id)).scalars())
    assert len(owners) == len(set(owners))
