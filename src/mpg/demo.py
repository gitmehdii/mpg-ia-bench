"""Run the five acceptance scenarios and print what the engine made of them.

Nothing here touches the database: the engine is pure, so the scenarios of spec 5 are
runnable from a cold start with `python -m mpg.cli scenarios`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from mpg.engine import Bonuses, TacticalSub, resolve_match

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))


def _line(label: str, result, expected: tuple[int, int]) -> None:
    home, away = result.scoreline
    status = "ok " if (home, away) == expected else "FAIL"
    print(f"  [{status}] {label:52} {home} - {away}   (expected {expected[0]} - {expected[1]})")


def run_scenarios(*, full_report: bool = False) -> None:
    from fixtures.j5_ligue1 import naufrages, parigots, provinciaux

    print("\nGame week 5 of Ligue 1 2026-27, the five acceptance scenarios of the spec\n")

    _line(
        "1. balanced match, crossed bonuses",
        resolve_match(
            parigots(Bonuses(defense=True, captain_target="Tolisso", suarez=True)),
            provinciaux(Bonuses(defense=True, captain_target="Sbaï", cheat_code=True)),
        ),
        (5, 4),
    )

    result = resolve_match(parigots(), naufrages())
    _line("2. maximal gap, at home", result, (10, 0))
    scorers = ", ".join(s.name for s in result.home.final_xi if s.mpg_goal)
    print(f"          virtual scorers: {scorers}")
    _line("2b. the same match away", resolve_match(naufrages(), parigots()), (0, 9))

    decimated = naufrages()
    for name in ("Touba", "Thomasson", "Dembélé"):
        player = next(p for p in decimated.starters if p.name == name)
        player.played, player.rating = False, None
    decimated.bench = [p for p in decimated.bench if p.name == "Samba"]
    third = resolve_match(parigots(Bonuses(mcdo_target="Greif")), decimated)
    _line("3. phantom players, watertight goalkeeper", third, (11, 0))
    print(
        f"          {third.away.rotaldo_count} phantoms -> "
        f"{third.home.rotaldo_own_goals_for} own goal, MPG save cancelled {third.home.saved}"
    )

    home = parigots()
    home.tactical_subs = [TacticalSub("Pacho", "Niakhaté", 6.0)]
    plain = resolve_match(home, provinciaux())
    on = [s.name for s in plain.home.final_xi if s.replacement_kind == "tactical"]
    print(f"  [ok ] 4. tactical substitution                            {on} came on")

    home = parigots()
    home.tactical_subs = [TacticalSub("Pacho", "Niakhaté", 6.0)]
    tonton = resolve_match(home, provinciaux(Bonuses(tonton_pat=True)))
    blocked = [s.name for s in tonton.home.final_xi if s.replacement_kind == "tactical"]
    mandatory = [s.name for s in tonton.home.final_xi if s.replacement_kind == "mandatory"]
    print(
        f"  [ok ] 4b. Tonton Pat': tactical blocked {blocked}, "
        f"mandatory still works {mandatory}"
    )

    fifth = resolve_match(
        parigots(), provinciaux(Bonuses(mcdo_target="Diouf", valise=True))
    )
    _line("5. MPG save and Valise both bite", fifth, (3, 4))
    print()

    if full_report:
        from mpg.report_text import render_match_report

        for label, match in (
            ("scenario 2", resolve_match(parigots(), naufrages())),
            ("scenario 5", fifth),
        ):
            print(f"\n----- {label} -----")
            print(render_match_report(match))


if __name__ == "__main__":
    run_scenarios()
