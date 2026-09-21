"""Squads of the acceptance scenarios: game week 5 of Ligue 1 2026-27 (spec 5).

Ratings and goals are the official MPG figures for the fixtures of 20 September 2026.
Every squad is built by a factory so each test gets its own mutable copy.
"""

from __future__ import annotations

from mpg.engine.models import Bonuses, PlayerEntry, TeamSheet

GK, CB, FB, DM, AM, FW = 10, 20, 21, 30, 31, 40


def _p(
    name: str,
    ultra: int,
    rating: float | None = None,
    goals: int = 0,
    *,
    played: bool | None = None,
) -> PlayerEntry:
    """A squad player. `rating=None` means he did not play in real life."""
    return PlayerEntry(
        player_id=name,
        ultra_position=ultra,
        name=name,
        played=rating is not None if played is None else played,
        rating=rating,
        real_goals=goals,
    )


# --------------------------------------------------------------- Les Parigots (PSG / OL)

def parigots_starters() -> list[PlayerEntry]:
    return [
        _p("Greif", GK, 7.0),
        _p("Marquinhos", CB, 7.0, 1),
        _p("Nuno Mendes", FB, 6.0),
        _p("Pacho", CB, 5.5),
        _p("Athekame", FB, 8.0, 1),
        _p("Vitinha", DM, 6.0),
        _p("Morton", DM, 7.0),
        _p("Tolisso", AM, 8.5, 1),
        _p("Doué", AM, 7.0),
        _p("Nuamah", FW, 8.0, 2),
        _p("Ferran Torres", FW, None),          # did not play
    ]


def parigots_bench() -> list[PlayerEntry]:
    return [
        _p("de Lange", GK, 7.0),
        _p("Niakhaté", CB, 6.0),
        _p("Alexsandro", CB, 5.5),
        _p("Bidstrup", DM, 7.0),
        _p("João Neves", DM, 6.0),
        _p("Openda", FW, 5.5),
        _p("Kvaratskhelia", FW, 5.0),
    ]


def parigots(bonuses: Bonuses | None = None, **kwargs) -> TeamSheet:
    return TeamSheet(
        participant_id="Les Parigots",
        starters=parigots_starters(),
        bench=parigots_bench(),
        formation="4-4-2",
        bonuses=bonuses or Bonuses(),
        **kwargs,
    )


# ----------------------------------------------------------------- Les Provinciaux

def provinciaux_starters() -> list[PlayerEntry]:
    return [
        _p("Diouf", GK, 7.5),
        _p("O. Camara", CB, 6.0),
        _p("Arcus", FB, 6.0),
        _p("Sané", CB, 6.0),
        _p("Lefort", FB, 6.0),
        _p("Cásseres", DM, 6.0),
        _p("Bretelle", DM, 7.0),
        _p("Jørgensen", AM, 8.0, 1),
        _p("Kebbal", AM, 7.0, 1),
        _p("Sbaï", FW, 8.0, 1),
        _p("Amoura", FW, 7.0, 1),
    ]


def provinciaux_bench() -> list[PlayerEntry]:
    return [
        _p("Restes", GK, 6.0),
        _p("Bacher", CB, 6.0),
        _p("Coppola", CB, 5.5),
        _p("D. Coulibaly", DM, 6.0),
        _p("Belkhdim", DM, 6.0),
        _p("Tengstedt", FW, 7.0, 2),            # scored twice, but stayed on the bench
        _p("Sinayoko", FW, 7.0),
    ]


def provinciaux(bonuses: Bonuses | None = None, **kwargs) -> TeamSheet:
    return TeamSheet(
        participant_id="Les Provinciaux",
        starters=provinciaux_starters(),
        bench=provinciaux_bench(),
        formation="4-4-2",
        bonuses=bonuses or Bonuses(),
        **kwargs,
    )


# ------------------------------------------------- Les Naufragés (worst ratings of GW5)

def naufrages_starters() -> list[PlayerEntry]:
    return [
        _p("Mvogo", GK, 4.0),
        _p("Touba", CB, 3.0),
        _p("Seko", CB, 3.5),
        _p("Emerson", FB, 4.0),
        _p("Aguilar", FB, 4.0),
        _p("Thomasson", DM, 3.0),
        _p("Rongier", DM, 3.0),
        _p("Reyna", AM, 4.0),
        _p("Avom", AM, 4.0),
        _p("Dembélé", FW, 2.5),
        _p("Maupay", FW, 4.0),
    ]


def naufrages_bench() -> list[PlayerEntry]:
    return [
        _p("Samba", GK, 4.0),
        _p("Ngoy", CB, 3.5),
        _p("Cresswell", CB, 3.0),
        _p("M. Camara", DM, 4.0),
        _p("Merlin", FB, 3.5),
        _p("Nordin", FW, 4.0),
        _p("I. Baldé", FW, 3.5),
    ]


def naufrages(bonuses: Bonuses | None = None, **kwargs) -> TeamSheet:
    return TeamSheet(
        participant_id="Les Naufragés",
        starters=naufrages_starters(),
        bench=naufrages_bench(),
        formation="4-4-2",
        bonuses=bonuses or Bonuses(),
        **kwargs,
    )
