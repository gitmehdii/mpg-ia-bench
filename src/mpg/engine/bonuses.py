"""Bonus and malus arithmetic (spec 3.5).

`apply_bonuses` is deliberately re-runnable: the resolver calls it once on the
submitted XI to get the ratings the tactical substitutions are evaluated against
(invariant 4.1.5), then again on the final XI so that line averages are computed
after substitutions and Rotaldos (invariant 4.1.7).
"""

from __future__ import annotations

from dataclasses import replace

from mpg.engine.flags import DEFAULT_FLAGS, EngineFlags
from mpg.engine.lines import Line, clamp_rating
from mpg.engine.models import Bonuses


def resolve_mirrors(
    home: Bonuses, away: Bonuses, *, flags: EngineFlags = DEFAULT_FLAGS
) -> tuple[Bonuses, Bonuses, list[str]]:
    """Step 1 of spec 3.6: turn each Miroir against its poser.

    A Miroir takes the single limited bonus the opponent posed and hands it to the
    mirroring team, which now applies it against the opponent. Défense and Capitaine
    are unlimited and never travel. A 424 that gets mirrored surrenders the +0.5 of
    its defence to the mirroring team (spec 3.5, 424 entry).

    Both mirrors are read against the pre-mirror state, so two Miroirs cancel out.
    """
    log: list[str] = []
    if not flags.mirror_steals_limited_bonus:
        return home, away, log
    if not (home.mirror or away.mirror):
        return home, away, log

    new_home, new_away = replace(home), replace(away)

    def steal(thief: Bonuses, victim_before: Bonuses, victim: Bonuses, who: str) -> None:
        for name in victim_before.limited_posed():
            if name == "mirror":
                continue
            if name == "formation_424":
                # The mirroring side keeps its own shape but pockets the defensive boost.
                thief.defense = True
                log.append(f"{who}: Miroir -> takes the +0.5 defence of the opposing 4-2-4")
                continue
            value = getattr(victim_before, name)
            setattr(thief, name, value)
            setattr(victim, name, None if name == "mcdo_target" else False)
            log.append(f"{who}: Miroir -> turns the opposing {name} around")

    if home.mirror:
        steal(new_home, away, new_away, "home")
        new_home.mirror = False
    if away.mirror:
        steal(new_away, home, new_home, "away")
        new_away.mirror = False
    if home.mirror and away.mirror:
        log.append("both sides posed a Miroir: they cancel out")

    return new_home, new_away, log


def apply_bonuses(
    ratings: dict[str, float],
    lines: dict[str, Line],
    own: Bonuses,
    opponent: Bonuses,
    *,
    include_cheat_code: bool = False,
) -> tuple[dict[str, float], dict[str, list[str]]]:
    """Apply own bonuses and the opponent's malus to an XI.

    `include_cheat_code` is False for step 2a of spec 3.6 and True once the final XI
    is known (step 3): the Cheat Code lands after substitutions (invariant 4.1.4).
    Returns the adjusted ratings and, per player, the list of what touched them.
    """
    out = dict(ratings)
    notes: dict[str, list[str]] = {pid: [] for pid in ratings}

    def bump(pid: str, delta: float, label: str) -> None:
        if pid not in out:
            return
        out[pid] = out[pid] + delta
        notes[pid].append(label)

    # --- Défense: unlimited, 4 starting defenders give +0.5 each, 5 give +1 each.
    if own.defense:
        defenders = [pid for pid, line in lines.items() if line is Line.D and pid in out]
        delta = {4: 0.5, 5: 1.0}.get(len(defenders), 0.0)
        for pid in defenders:
            bump(pid, delta, f"Défense +{delta}")

    # --- Zahia: +0.5 on every starter of its own team, goalkeeper included.
    if own.zahia:
        for pid in list(out):
            bump(pid, 0.5, "Zahia +0.5")

    # --- McDo+: +1 on one starter of the team's choosing, goalkeeper allowed.
    mcdo = own.mcdo_target
    if mcdo:
        bump(mcdo, 1.0, "McDo+ +1")

    # --- Capitaine: +0.5 on one starter, goalkeeper excluded, not cumulable with McDo.
    captain = own.captain_target
    if captain and captain in out and lines.get(captain) is not Line.G:
        if captain == mcdo:
            notes[captain].append("Capitaine lost (not cumulable with McDo+)")
        else:
            bump(captain, 0.5, "Capitaine +0.5")

    # --- Suarez: -1 on the opposing goalkeeper, substitute goalkeeper included.
    if opponent.suarez:
        for pid, line in lines.items():
            if line is Line.G and pid in out:
                bump(pid, -1.0, "Suarez -1")

    # --- Cheat Code 18-26: -0.5 on every opposing field player, goalkeeper excluded.
    if include_cheat_code and opponent.cheat_code:
        for pid, line in lines.items():
            if line is not Line.G and pid in out:
                bump(pid, -0.5, "Cheat Code -0.5")

    return {pid: clamp_rating(value) for pid, value in out.items()}, notes
