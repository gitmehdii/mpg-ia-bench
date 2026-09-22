"""Seed a ready-to-browse database: two accounts, a league, a resolved game week.

Uses the real game week 5 figures of Ligue 1 2026-27 -- the same ones the acceptance
scenarios run on -- so the report has something worth reading. No network needed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from mpg.api.security import hash_password
from mpg.config import MERCATO_BUDGET
from mpg.db.models import (
    BonusUsage,
    Championship,
    Club,
    League,
    LeagueMatch,
    LeagueStatus,
    Lineup,
    LineupSlot,
    Match,
    Participant,
    Performance,
    Player,
    Roster,
    TacticalSubRule,
    User,
)

GK, CB, FB, DM, AM, FW = 10, 20, 21, 30, 31, 40
SEASON, GAME_WEEK = 2026, 5
DEMO_PASSWORD = "motdepasse"

#: (name, ultraPosition, quotation, rating, goals). A rating of None means he did not play.
PARIGOTS = [
    ("Greif", GK, 13, 7.0, 0), ("Marquinhos", CB, 11, 7.0, 1),
    ("Nuno Mendes", FB, 21, 6.0, 0), ("Pacho", CB, 14, 5.5, 0),
    ("Athekame", FB, 8, 8.0, 1), ("Vitinha", DM, 28, 6.0, 0),
    ("Morton", DM, 12, 7.0, 0), ("Tolisso", AM, 30, 8.5, 1),
    ("Doué", AM, 19, 7.0, 0), ("Nuamah", FW, 9, 8.0, 2),
    ("Ferran Torres", FW, 24, None, 0),
    ("de Lange", GK, 10, 7.0, 0), ("Niakhaté", CB, 13, 6.0, 0),
    ("Alexsandro", CB, 12, 5.5, 0), ("Bidstrup", DM, 11, 7.0, 0),
    ("João Neves", DM, 26, 6.0, 0), ("Openda", FW, 26, 5.5, 0),
    ("Kvaratskhelia", FW, 33, 5.0, 0),
]

PROVINCIAUX = [
    ("Diouf", GK, 16, 7.5, 0), ("O. Camara", CB, 9, 6.0, 0), ("Arcus", FB, 8, 6.0, 0),
    ("Sané", CB, 10, 6.0, 0), ("Lefort", FB, 9, 6.0, 0),
    ("Cásseres", DM, 11, 6.0, 0), ("Bretelle", DM, 10, 7.0, 0),
    ("Jørgensen", AM, 18, 8.0, 1), ("Kebbal", AM, 17, 7.0, 1),
    ("Sbaï", FW, 15, 8.0, 1), ("Amoura", FW, 23, 7.0, 1),
    ("Restes", GK, 11, 6.0, 0), ("Bacher", CB, 8, 6.0, 0), ("Coppola", CB, 9, 5.5, 0),
    ("D. Coulibaly", DM, 10, 6.0, 0), ("Belkhdim", DM, 8, 6.0, 0),
    ("Tengstedt", FW, 14, 7.0, 2), ("Sinayoko", FW, 12, 7.0, 0),
]

NAUFRAGES = [
    ("Mvogo", GK, 14, 4.0, 0), ("Touba", CB, 9, 3.0, 0), ("Seko", CB, 8, 3.5, 0),
    ("Emerson", FB, 10, 4.0, 0), ("Aguilar", FB, 9, 4.0, 0),
    ("Thomasson", DM, 11, 3.0, 0), ("Rongier", DM, 12, 3.0, 0),
    ("Reyna", AM, 10, 4.0, 0), ("Avom", AM, 8, 4.0, 0),
    ("Dembélé", FW, 34, 2.5, 0), ("Maupay", FW, 11, 4.0, 0),
    ("Samba", GK, 12, 4.0, 0), ("Ngoy", CB, 7, 3.5, 0), ("Cresswell", CB, 8, 3.0, 0),
    ("M. Camara", DM, 9, 4.0, 0), ("Merlin", FB, 10, 3.5, 0),
    ("Nordin", FW, 12, 4.0, 0), ("I. Baldé", FW, 9, 3.5, 0),
]

FORMATION = "4-4-2"


def _user(session: Session, email: str, name: str) -> User:
    existing = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing:
        return existing
    user = User(email=email, display_name=name, password_hash=hash_password(DEMO_PASSWORD))
    session.add(user)
    session.flush()
    return user


def _players(session: Session, squad: list[tuple], match_id: str) -> list[Player]:
    out = []
    for name, ultra, quotation, rating, goals in squad:
        player = session.get(Player, name)
        if player is None:
            player = Player(
                id=name, last_name=name, ultra_position=ultra, club_id="demo_club",
                championship_id=1, quotation=quotation,
            )
            session.add(player)
        session.flush()
        # A squad can be seeded for more than one demo league, so the rating is
        # written once and reused rather than inserted again.
        if rating is not None:
            known = session.execute(
                select(Performance).where(
                    Performance.player_id == name, Performance.match_id == match_id
                )
            ).scalar_one_or_none()
            if known is None:
                session.add(
                    Performance(
                        player_id=name, match_id=match_id, game_week_number=GAME_WEEK,
                        season=SEASON, rating=rating, goals_scored=goals,
                    )
                )
        out.append(player)
    session.flush()
    return out


def seed(session: Session) -> dict:
    """Build the demo league and resolve its game week. Returns what to tell the user."""

    now = datetime.now(UTC)
    if session.get(Championship, 1) is None:
        session.add(
            Championship(id=1, code="fr_l1", name="Ligue 1", current_season=SEASON)
        )
    if session.get(Club, "demo_club") is None:
        session.add(Club(id="demo_club", name="Ligue 1", championship_id=1))
    session.flush()

    for index, (home, away) in enumerate((("demo_club", "demo_club"),), start=1):
        match_id = f"demo_match_{index}"
        if session.get(Match, match_id) is None:
            session.add(
                Match(
                    id=match_id, championship_id=1, season=SEASON,
                    game_week_number=GAME_WEEK, date=now - timedelta(days=2),
                    home_club_id=home, away_club_id=away, status=1,
                )
            )
    session.flush()

    alice = _user(session, "alice@example.com", "Alice")
    bob = _user(session, "bob@example.com", "Bob")

    built = []
    for code, name, away_user, away_name, away_squad, bonuses in (
        (
            "DEMO01", "Démo — écart maximal", bob, "Les Naufragés", NAUFRAGES,
            {"home": [("defense", None), ("captain", "Tolisso")], "away": []},
        ),
        (
            "DEMO02", "Démo — match serré", bob, "Les Provinciaux", PROVINCIAUX,
            {
                "home": [("defense", None), ("captain", "Tolisso"), ("suarez", None)],
                "away": [("defense", None), ("captain", "Sbaï"), ("cheat_code", None)],
            },
        ),
    ):
        existing = session.execute(
            select(League).where(League.code == code)
        ).scalar_one_or_none()
        if existing is not None:
            built.append(existing)
            continue
        built.append(
            _build_league(session, code, name, now, alice, away_user, away_name,
                          away_squad, bonuses)
        )
    return _summary(session, built)


def _build_league(session, code, name, now, home_user, away_user, away_name,
                  away_squad, bonuses):
    league = League(
        name=name, code=code, championship_id=1, size=2, return_legs=True,
        status=LeagueStatus.RUNNING, created_by=home_user.id,
        first_game_week=GAME_WEEK, mercato_closed_at=now,
    )
    session.add(league)
    session.flush()

    participants = []
    for user, team_name, squad, side in (
        (home_user, "Les Parigots", PARIGOTS, "home"),
        (away_user, away_name, away_squad, "away"),
    ):
        participant = Participant(
            league_id=league.id, user_id=user.id, team_name=team_name,
            budget=MERCATO_BUDGET, is_admin=side == "home", mercato_closed=True,
        )
        session.add(participant)
        session.flush()
        participants.append(participant)

        _players(session, squad, "demo_match_1")
        for row in squad:
            session.add(
                Roster(
                    participant_id=participant.id, player_id=row[0],
                    bought_at=now, bought_price=row[2],
                )
            )

        lineup = Lineup(
            participant_id=participant.id, game_week_number=GAME_WEEK,
            formation=FORMATION, submitted_at=now,
        )
        session.add(lineup)
        session.flush()
        for order, row in enumerate(squad[:11]):
            session.add(
                LineupSlot(lineup_id=lineup.id, player_id=row[0], role="starter", order=order)
            )
        for order, row in enumerate(squad[11:]):
            session.add(
                LineupSlot(lineup_id=lineup.id, player_id=row[0], role="bench", order=order)
            )
        for bonus_type, target in bonuses[side]:
            session.add(
                BonusUsage(
                    participant_id=participant.id, game_week_number=GAME_WEEK,
                    bonus_type=bonus_type, target_player_id=target,
                )
            )
        if side == "home":
            # Threshold above Pacho's bonus-inclusive 6.0, so the rule actually fires
            # and the report shows a tactical substitution.
            session.add(TacticalSubRule(
                lineup_id=lineup.id, starter_id="Pacho", sub_id="Niakhaté", threshold=6.5,
            ))
    session.flush()

    fixture = LeagueMatch(
        league_id=league.id, game_week_number=GAME_WEEK,
        home_participant_id=participants[0].id, away_participant_id=participants[1].id,
    )
    session.add(fixture)
    session.flush()

    from mpg.services.matchday import resolve_league_match

    resolve_league_match(session, fixture)
    session.flush()
    return league


def _summary(session: Session, leagues: list[League]) -> dict:
    out = []
    for league in leagues:
        fixture = session.execute(
            select(LeagueMatch).where(LeagueMatch.league_id == league.id)
        ).scalars().first()
        out.append({
            "name": league.name,
            "league_id": league.id,
            "fixture_id": fixture.id if fixture else None,
            "score": (fixture.home_score, fixture.away_score) if fixture else None,
        })
    return {"leagues": out, "password": DEMO_PASSWORD}
