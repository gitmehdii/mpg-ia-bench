"""M8: a closed bid stays closed (spec 4.4.2).

Rather than checking a handful of routes by hand, this sweeps every GET endpoint the
API publishes and asserts that none of them leaks a rival's bid -- not the amount, not
the existence, not an aggregate that would let one be deduced.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from fixtures.league import seed_pool
from mpg.api.app import app
from mpg.db.base import Base
from mpg.db.models import Bid, LeagueStatus, MercatoRound, Participant
from mpg.db.session import get_db

#: Distinctive amounts, so finding one in a payload cannot be a coincidence.
SECRET_AMOUNTS = {"A": 317, "B": 419, "D": 233}
SECRET_PLAYERS = {"A": "Dembélé", "B": "Tolisso", "D": "Openda"}


@pytest.fixture
def api():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # TestClient runs the app in another thread, so the in-memory database has to be
    # a single shared connection.
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()

    def override():
        yield session

    app.dependency_overrides[get_db] = override
    client = TestClient(app)
    yield client, session
    app.dependency_overrides.clear()
    session.close()


def register(client: TestClient, label: str) -> str:
    response = client.post(
        "/auth/register",
        json={
            "email": f"{label.lower()}@example.com",
            "display_name": label,
            "password": "password123",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def league_in_mercato(api):
    client, session = api
    tokens = {label: register(client, label) for label in "ABCD"}

    seed_pool(session)
    created = client.post(
        "/leagues",
        json={"name": "Opacity", "championship_id": 1, "size": 4, "team_name": "Team A"},
        headers=auth(tokens["A"]),
    )
    assert created.status_code == 201, created.text
    league_id = created.json()["id"]

    for label in "BCD":
        joined = client.post(
            f"/leagues/{league_id}/join",
            json={"team_name": f"Team {label}"},
            headers=auth(tokens[label]),
        )
        assert joined.status_code == 200, joined.text

    opened = client.post(f"/leagues/{league_id}/mercato/open", headers=auth(tokens["A"]))
    assert opened.status_code == 201, opened.text

    # A, B and D each place a secret bid. C places none.
    for label, amount in SECRET_AMOUNTS.items():
        placed = client.post(
            f"/leagues/{league_id}/mercato/bids",
            json={"player_id": SECRET_PLAYERS[label], "amount": amount},
            headers=auth(tokens[label]),
        )
        assert placed.status_code == 201, placed.text

    return client, session, league_id, tokens


def _find_in_text(text: str, wanted: set[int]) -> set[int]:
    """Scan a rendered page for any of `wanted`, bounded by non-digits."""
    return {
        amount for amount in wanted if re.search(rf"(?<!\d){amount}(?!\d)", text)
    }


def _find_amounts(node: object, wanted: set[int]) -> set[int]:
    """Walk a decoded payload for any of `wanted`, as a number or inside a string.

    Walking the structure rather than grepping the serialised body means a secret
    cannot hide as a field value, and a coincidental digit run inside a longer number
    cannot raise a false alarm.
    """
    found: set[int] = set()
    if isinstance(node, bool):
        return found
    if isinstance(node, int):
        if node in wanted:
            found.add(node)
    elif isinstance(node, str):
        for amount in wanted:
            # Bounded by non-digits, so 233 does not match inside 12336.
            if re.search(rf"(?<!\d){amount}(?!\d)", node):
                found.add(amount)
    elif isinstance(node, dict):
        for key, value in node.items():
            found |= _find_amounts(key, wanted)
            found |= _find_amounts(value, wanted)
    elif isinstance(node, list):
        for item in node:
            found |= _find_amounts(item, wanted)
    return found


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"bids": [{"amount": 317}]}, {317}),          # a number, nested
        ({"note": "bid of 419 euros"}, {419}),         # hidden inside a string
        ([[{"a": {"b": 233}}]], {233}),                # deeply nested
        ({"total": 12336}, set()),                     # a longer number must not match
        ({"id": "pool_317_x"}, {317}),                 # bounded by non-digits, so it counts
        ({"budget": 500, "ok": True}, set()),          # a clean payload
    ],
)
def test_the_leak_detector_detects(payload, expected):
    """The sweep is only worth as much as its detector, so the detector is tested too."""
    assert _find_amounts(payload, {317, 419, 233}) == expected


def test_m8_no_get_endpoint_leaks_a_rival_bid(league_in_mercato):
    """Sweep every GET route in the OpenAPI schema as C and look for any leak."""
    client, session, league_id, tokens = league_in_mercato

    round_ = session.execute(select(MercatoRound)).scalars().first()
    fixture_id = 1
    substitutions = {
        "{league_id}": str(league_id),
        "{round_id}": str(round_.id),
        "{fixture_id}": str(fixture_id),
        "{player_id}": SECRET_PLAYERS["A"],
    }

    checked = 0
    for path, operations in app.openapi()["paths"].items():
        if "get" not in operations:
            continue
        url = path
        for placeholder, value in substitutions.items():
            url = url.replace(placeholder, value)
        if "{" in url:
            continue
        response = client.get(url, headers=auth(tokens["C"]))
        checked += 1
        if response.status_code >= 400:
            continue
        wanted = set(SECRET_AMOUNTS.values())
        if response.headers.get("content-type", "").startswith("application/json"):
            leaked = _find_amounts(response.json(), wanted)
        else:
            # The /ui screens render HTML, and they are swept exactly the same way.
            leaked = _find_in_text(response.text, wanted)
        assert not leaked, f"{url} leaks the bid amount(s) {sorted(leaked)}"

    assert checked >= 12, (
        "the sweep must reach the whole read surface, the /ui screens included"
    )


def test_m8_c_sees_only_its_own_bids(league_in_mercato):
    client, session, league_id, tokens = league_in_mercato

    own = client.get(f"/leagues/{league_id}/mercato/bids", headers=auth(tokens["C"]))
    assert own.status_code == 200
    assert own.json() == []

    # A does see its own.
    mine = client.get(f"/leagues/{league_id}/mercato/bids", headers=auth(tokens["A"]))
    assert [b["player_id"] for b in mine.json()] == [SECRET_PLAYERS["A"]]
    assert mine.json()[0]["amount"] == SECRET_AMOUNTS["A"]


def test_m8_the_free_player_list_does_not_hint_at_pressure(league_in_mercato):
    """A player under three bids must look exactly like a player under none."""
    client, session, league_id, tokens = league_in_mercato

    response = client.get(f"/leagues/{league_id}/mercato/players", headers=auth(tokens["C"]))
    assert response.status_code == 200
    players = {p["id"]: p for p in response.json()}

    bid_on = players[SECRET_PLAYERS["A"]]
    untouched = next(p for pid, p in players.items() if pid not in SECRET_PLAYERS.values())
    assert set(bid_on) == set(untouched), "the shape must not differ between the two"
    for field in bid_on:
        if field in ("id", "name", "quotation", "club_id", "ultra_position"):
            continue
        assert bid_on[field] == untouched[field], f"{field} betrays that there are bids"


def test_m8_budget_of_rivals_is_hidden_during_the_mercato(league_in_mercato):
    """A rival's remaining budget is a direct read on what they can still bid."""
    client, session, league_id, tokens = league_in_mercato

    response = client.get(f"/leagues/{league_id}", headers=auth(tokens["C"]))
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == str(LeagueStatus.MERCATO)

    me = session.execute(
        select(Participant).where(Participant.league_id == league_id)
    ).scalars().all()
    c_id = next(p.id for p in me if p.team_name == "Team C")

    for participant in payload["participants"]:
        if participant["id"] == c_id:
            assert participant["budget"] == 500
        else:
            assert participant["budget"] is None, "a rival's budget must stay hidden"


def test_m8_a_participant_cannot_delete_another_bid(league_in_mercato):
    """C withdrawing "the bid on Dembélé" must not touch A's."""
    client, session, league_id, tokens = league_in_mercato

    response = client.delete(
        f"/leagues/{league_id}/mercato/bids/{SECRET_PLAYERS['A']}",
        headers=auth(tokens["C"]),
    )
    assert response.status_code == 204

    remaining = session.execute(
        select(Bid).where(Bid.player_id == SECRET_PLAYERS["A"])
    ).scalars().all()
    assert len(remaining) == 1, "A's bid must still be there"
    assert remaining[0].amount == SECRET_AMOUNTS["A"]


def test_m8_outsider_cannot_read_the_league_at_all(api):
    client, session = api
    tokens = {label: register(client, label) for label in "AZ"}
    seed_pool(session)
    created = client.post(
        "/leagues",
        json={"name": "Private", "championship_id": 1, "size": 4, "team_name": "Team A"},
        headers=auth(tokens["A"]),
    )
    league_id = created.json()["id"]
    client.post(f"/leagues/{league_id}/mercato/open", headers=auth(tokens["A"]))

    for url in (
        f"/leagues/{league_id}",
        f"/leagues/{league_id}/mercato/bids",
        f"/leagues/{league_id}/mercato/players",
        f"/leagues/{league_id}/standings",
    ):
        response = client.get(url, headers=auth(tokens["Z"]))
        assert response.status_code == 403, f"{url} answered {response.status_code}"


def test_m8_unauthenticated_access_is_refused(api):
    client, _session = api
    assert client.get("/leagues/1/mercato/bids").status_code == 401
