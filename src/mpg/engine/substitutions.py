"""Building the final XI: tactical, mandatory and live substitutions, then Rotaldos.

Invariant 4.1.1 is enforced in one place, `_compatible`: a goalkeeper never replaces a
field player and a field player never replaces a goalkeeper. Without it a bench down to
a single goalkeeper plugs holes in defence, the phantom count is wrong and the
punishment own goal never lands.
"""

from __future__ import annotations

from mpg.engine.flags import DEFAULT_FLAGS, EngineFlags
from mpg.engine.formations import infer_formation, slots_of
from mpg.engine.lines import (
    LINE_ORDER,
    ROTALDO_PER_OWN_GOAL,
    ROTALDO_RATING,
    Line,
    line_distance,
)
from mpg.engine.models import FinalPlayer, PlayerEntry, TeamSheet

MAX_TACTICAL_SUBS = 5
TONTON_PAT_LIVE_CAP = 2


def _compatible(candidate_line: Line, slot_line: Line) -> bool:
    """Invariant 4.1.1: the goalkeeper slot is watertight in both directions."""
    return (candidate_line is Line.G) == (slot_line is Line.G)


def _slot_lines(sheet: TeamSheet) -> list[Line]:
    """The slot structure to fill. Falls back to the shape actually fielded."""
    if sheet.formation:
        return slots_of(sheet.formation)
    lines = [player.line for player in sheet.starters]
    inferred = infer_formation(lines)
    if inferred:
        return slots_of(inferred)
    # Not a legal shape: keep the submitted one so the report still explains itself.
    return sorted(lines, key=LINE_ORDER.index)


def _seat_starters(sheet: TeamSheet) -> tuple[list[tuple[Line, PlayerEntry | None]], list[str]]:
    """Seat the submitted starters into the formation's slots, by line."""
    log: list[str] = []
    remaining = list(sheet.starters)
    seats: list[tuple[Line, PlayerEntry | None]] = []
    for slot in _slot_lines(sheet):
        match = next((p for p in remaining if p.line is slot), None)
        if match is not None:
            remaining.remove(match)
            seats.append((slot, match))
        else:
            seats.append((slot, None))
            log.append(f"empty {slot} slot in the submitted XI")
    for leftover in remaining:
        # More players of a line than the formation holds: the service layer rejects
        # this, but the engine still seats them rather than losing them silently.
        seats.append((leftover.line, leftover))
        log.append(f"{leftover.name or leftover.player_id} seated outside the formation")
    return seats, log


def mandatory_sub(
    slot_line: Line,
    starter: PlayerEntry | None,
    bench: list[PlayerEntry],
    used: set[str],
    *,
    flags: EngineFlags = DEFAULT_FLAGS,
) -> tuple[PlayerEntry, float] | None:
    """Spec 4.3: first the same ultraPosition, then the same line, then the nearest line.

    Returns the substitute and the rating penalty (-1 per line skipped), or None.
    """

    def eligible(player: PlayerEntry) -> bool:
        return (
            player.player_id not in used
            and player.has_effectively_played()
            and not player.neutralised  # cannot come on during the weekend (spec 3.8)
            and _compatible(player.line, slot_line)
        )

    # 1. same ultraPosition as the player who has to be replaced
    if starter is not None:
        for player in bench:
            if eligible(player) and player.ultra_position == starter.ultra_position:
                return player, 0.0

    # 2. same line
    for player in bench:
        if eligible(player) and player.line is slot_line:
            return player, 0.0

    # 3. closest line, -1 point per line skipped
    candidates = [p for p in bench if eligible(p)]
    if not candidates:
        return None
    if flags.mandatory_sub_direction == "lower":
        candidates = [
            p for p in candidates if LINE_ORDER.index(p.line) > LINE_ORDER.index(slot_line)
        ] or candidates
    best = min(
        candidates,
        key=lambda p: (line_distance(p.line, slot_line), bench.index(p)),
    )
    return best, -1.0 * line_distance(best.line, slot_line)


def build_final_xi(
    sheet: TeamSheet,
    ratings: dict[str, float],
    adjustments: dict[str, list[str]],
    *,
    opponent_tonton_pat: bool = False,
    flags: EngineFlags = DEFAULT_FLAGS,
) -> tuple[list[FinalPlayer], list[str]]:
    """Steps 2b to 2d of spec 3.6, for one side.

    `ratings` holds the bonus-inclusive ratings of every squad player except the Cheat
    Code, which lands later (invariant 4.1.4). Tactical thresholds are read off those
    ratings (invariant 4.1.5).
    """
    log: list[str] = []
    seats, seat_log = _seat_starters(sheet)
    log.extend(seat_log)

    by_id = {player.player_id: player for player in sheet.all_players()}
    bench = list(sheet.bench)
    used: set[str] = {p.player_id for _, p in seats if p is not None}

    # Which slot each occupant sits in, and how it got there.
    occupants: list[dict] = [
        {"slot": slot, "player": player, "kind": None, "replaced": None, "penalty": 0.0}
        for slot, player in seats
    ]

    # ---------------------------------------------------------------- 2b. tactical
    if sheet.live_mode:
        log.append("live mode: tactical and mandatory substitutions are disabled")
    elif opponent_tonton_pat:
        log.append("Tonton Pat': no tactical substitution is carried out")
    else:
        applied = 0
        for rule in sheet.tactical_subs:
            if applied >= MAX_TACTICAL_SUBS:
                log.append("tactical substitution limit reached (5)")
                break
            seat = next(
                (s for s in occupants
                 if s["player"] is not None and s["player"].player_id == rule.starter_id),
                None,
            )
            if seat is None:
                continue
            starter = seat["player"]
            if starter.line is Line.G or seat["slot"] is Line.G:
                log.append(f"tactical substitution ignored on the goalkeeper {starter.name}")
                continue
            sub = by_id.get(rule.sub_id)
            if sub is None or sub.player_id in used or sub not in bench:
                continue
            if not sub.has_effectively_played() or sub.neutralised:
                continue
            if not _compatible(sub.line, seat["slot"]):
                continue
            # A starter who did not play is below any threshold, and the tactical rule
            # takes priority over the mandatory one (spec 3.4).
            note = ratings.get(starter.player_id) if starter.has_effectively_played() else None
            if note is not None and note >= rule.threshold:
                continue
            seat["player"] = sub
            seat["kind"] = "tactical"
            seat["replaced"] = starter.player_id
            used.add(sub.player_id)
            used.discard(starter.player_id)
            applied += 1
            shown = "did not play" if note is None else f"{note:g}"
            log.append(
                f"tactical: {sub.name or sub.player_id} on for "
                f"{starter.name or starter.player_id} ({shown} < {rule.threshold:g})"
            )

    # ----------------------------------------------------------------- live changes
    if sheet.live_mode and sheet.live_subs:
        cap = TONTON_PAT_LIVE_CAP if opponent_tonton_pat else len(sheet.live_subs)
        if opponent_tonton_pat:
            log.append("Tonton Pat': only the first two live changes count")
        changed: set[str] = set()
        for rule in sorted(sheet.live_subs, key=lambda r: r.order)[:cap]:
            seat = next(
                (s for s in occupants
                 if s["player"] is not None and s["player"].player_id == rule.starter_id),
                None,
            )
            if seat is None or rule.starter_id in changed:
                continue
            if seat["slot"] is Line.G:
                log.append("live mode: the goalkeeper cannot be changed")
                continue
            sub = by_id.get(rule.sub_id)
            # Live changes draw on the whole squad, and only on players not yet started.
            if sub is None or sub.player_id in used or sub.match_started or sub.neutralised:
                continue
            if sub.line is not seat["player"].line:
                log.append(f"live: {sub.name or sub.player_id} is not in the same position")
                continue
            seat["player"] = sub
            seat["kind"] = "live"
            seat["replaced"] = rule.starter_id
            used.add(sub.player_id)
            changed.add(rule.starter_id)
            log.append(f"live: {sub.name or sub.player_id} on for {rule.starter_id}")

    # --------------------------------------------------------------- 2c. mandatory
    # In live mode the only mandatory substitution left is the goalkeeper's, decided
    # before the first kick-off (spec 3.4).
    for seat in occupants:
        occupant = seat["player"]
        if occupant is not None and occupant.has_effectively_played():
            continue
        if sheet.live_mode and seat["slot"] is not Line.G:
            continue
        original = occupant
        found = mandatory_sub(seat["slot"], original, bench, used, flags=flags)
        if found is None:
            continue
        sub, penalty = found
        seat["player"] = sub
        seat["kind"] = "mandatory"
        seat["replaced"] = original.player_id if original is not None else None
        seat["penalty"] = penalty
        used.add(sub.player_id)
        if original is not None:
            used.discard(original.player_id)
        who = (
            (original.name or original.player_id)
            if original
            else f"the empty {seat['slot']} slot"
        )
        extra = f" ({penalty:g} for {abs(int(penalty))} line(s) skipped)" if penalty else ""
        log.append(f"mandatory: {sub.name or sub.player_id} on for {who}{extra}")

    # ----------------------------------------------------------------- 2d. Rotaldos
    final: list[FinalPlayer] = []
    rotaldo_index = 0
    for seat in occupants:
        occupant = seat["player"]
        slot: Line = seat["slot"]
        if occupant is None or not occupant.has_effectively_played():
            rotaldo_index += 1
            final.append(
                FinalPlayer(
                    slot_line=slot,
                    player_id=f"rotaldo:{sheet.participant_id}:{rotaldo_index}",
                    name=f"Rotaldo {rotaldo_index}",
                    rating=ROTALDO_RATING,
                    is_rotaldo=True,
                    replaced=occupant.player_id if occupant is not None else None,
                )
            )
            missing = (occupant.name or occupant.player_id) if occupant else f"empty {slot} slot"
            log.append(f"Rotaldo fills the {slot} slot ({missing})")
            continue

        line = slot if flags.out_of_position_line == "slot" else occupant.line
        rating = ratings.get(occupant.player_id, occupant.effective_rating() or 0.0)
        final.append(
            FinalPlayer(
                slot_line=line,
                player_id=occupant.player_id,
                name=occupant.name or occupant.player_id,
                rating=rating + seat["penalty"],
                real_goals=occupant.effective_real_goals(),
                own_goals=occupant.effective_own_goals(),
                replaced=seat["replaced"],
                replacement_kind=seat["kind"],
                line_penalty=seat["penalty"],
                adjustments=list(adjustments.get(occupant.player_id, [])),
            )
        )

    return final, log


def rotaldo_own_goals(final_xi: list[FinalPlayer]) -> int:
    """Spec 3.4: one conceded own goal for every three phantom players in the final XI."""
    return sum(1 for slot in final_xi if slot.is_rotaldo) // ROTALDO_PER_OWN_GOAL
