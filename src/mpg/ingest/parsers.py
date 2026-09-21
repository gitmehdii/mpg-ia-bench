"""Turning the API payloads into rows (spec 1.4, 1.5). Pure, so it is testable offline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def club_name(raw: dict) -> str:
    """Club names come as a locale map; French first, then anything available."""
    name = raw.get("name")
    if isinstance(name, str):
        return name
    if isinstance(name, dict):
        for locale in ("fr-FR", "en-GB", "es-ES"):
            if name.get(locale):
                return name[locale]
        if name:
            return next(iter(name.values()))
    return raw.get("shortName") or raw.get("id", "")


@dataclass(slots=True)
class ParsedClub:
    id: str
    name: str
    short_name: str | None
    championship_ids: list[int]
    primary_color: str | None


def parse_clubs(payload: dict) -> list[ParsedClub]:
    clubs = payload.get("championshipClubs", payload)
    out: list[ParsedClub] = []
    for club_id, raw in clubs.items():
        championships = raw.get("championships") or {}
        ids = [
            int(cid)
            for cid, body in championships.items()
            if str(cid).isdigit() and (body or {}).get("active")
        ]
        out.append(
            ParsedClub(
                id=raw.get("id", club_id),
                name=club_name(raw),
                short_name=raw.get("shortName"),
                championship_ids=ids,
                primary_color=raw.get("primaryColor"),
            )
        )
    return out


@dataclass(slots=True)
class ParsedMatch:
    id: str
    championship_id: int
    game_week_number: int
    date: datetime | None
    home_club_id: str | None
    away_club_id: str | None
    home_score: int | None
    away_score: int | None
    status: int | None


@dataclass(slots=True)
class ParsedPerformance:
    player_id: str
    match_id: str
    game_week_number: int
    #: None means the player did not take the field, which is what triggers the
    #: mandatory substitutions of spec 3.4.
    rating: float | None
    goals_scored: int


@dataclass(slots=True)
class ParsedPlayer:
    id: str
    first_name: str | None
    last_name: str
    position: int | None
    ultra_position: int
    quotation: int | None
    club_id: str | None
    country_code: str | None
    birth_date: date | None
    stats: dict = field(default_factory=dict)
    matches: list[ParsedMatch] = field(default_factory=list)
    performances: list[ParsedPerformance] = field(default_factory=list)


def parse_pool(payload: dict, championship_id: int) -> list[ParsedPlayer]:
    """Parse a players pool, including the ratings held in `nearestMatches`."""
    players: list[ParsedPlayer] = []
    for raw in payload.get("poolPlayers", payload if isinstance(payload, list) else []):
        stats = raw.get("stats") or {}
        parsed = ParsedPlayer(
            id=raw["id"],
            first_name=raw.get("firstName"),
            last_name=raw.get("lastName") or "",
            position=raw.get("position"),
            ultra_position=raw["ultraPosition"],
            quotation=raw.get("quotation"),
            club_id=raw.get("clubId"),
            country_code=raw.get("countryCode"),
            birth_date=_parse_date(raw.get("birthDate")),
            stats=stats,
        )

        for match in (stats.get("nearestMatches") or {}).get("matches") or []:
            home, away = match.get("home") or {}, match.get("away") or {}
            parsed.matches.append(
                ParsedMatch(
                    id=match["matchId"],
                    championship_id=match.get("championshipId", championship_id),
                    game_week_number=match.get("gameWeekNumber", 0),
                    date=_parse_datetime(match.get("date")),
                    home_club_id=home.get("clubId"),
                    away_club_id=away.get("clubId"),
                    home_score=home.get("score"),
                    away_score=away.get("score"),
                    status=match.get("status"),
                )
            )
            # A finished match with no rating means the player did not play.
            if match.get("status") == 1:
                parsed.performances.append(
                    ParsedPerformance(
                        player_id=parsed.id,
                        match_id=match["matchId"],
                        game_week_number=match.get("gameWeekNumber", 0),
                        rating=match.get("rating"),
                        goals_scored=match.get("goalsScored") or 0,
                    )
                )
        players.append(parsed)
    return players
