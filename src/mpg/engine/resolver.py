"""Resolution of one league fixture, in the order laid down by spec 3.6.

    1.  resolve the Miroirs
    2.  per side, build the final XI (own bonuses and Suarez, tactical, mandatory, Rotaldos)
    3.  apply the opposing Cheat Code to the final XI's field players
    4.  count the real goals
    5.  count the MPG goals
    6.  turn phantom players into conceded own goals
    7.  arm the MPG save and the Valise
    8.  work out the scores

The module is pure: it takes team sheets in and hands a `MatchResult` back.
"""

from __future__ import annotations

from mpg.engine.bonuses import apply_bonuses, resolve_mirrors
from mpg.engine.flags import DEFAULT_FLAGS, EngineFlags
from mpg.engine.lines import MPG_SAVE_THRESHOLD, Line
from mpg.engine.models import (
    Bonuses,
    FinalPlayer,
    GoalEvent,
    MatchContext,
    MatchResult,
    TeamResult,
    TeamSheet,
)
from mpg.engine.mpg_goals import mpg_goals
from mpg.engine.substitutions import build_final_xi, rotaldo_own_goals

#: Compensation the poser of a Valise owes its opponent, whether or not it bites.
VALISE_COMPENSATION = 5_000_000

#: Line priority a Valise follows when picking the goal it cancels (spec 3.5).
VALISE_LINE_PRIORITY = (Line.D, Line.M, Line.A)


def _squad_ratings(sheet: TeamSheet) -> tuple[dict[str, float], dict[str, Line]]:
    """Ratings of every squad player who has a rating, plus their line."""
    ratings: dict[str, float] = {}
    lines: dict[str, Line] = {}
    for player in sheet.all_players():
        rating = player.effective_rating()
        if rating is None:
            continue
        ratings[player.player_id] = rating
        lines[player.player_id] = player.line
    return ratings, lines


def _starter_lines(sheet: TeamSheet) -> dict[str, Line]:
    return {player.player_id: player.line for player in sheet.starters}


def _build_side(
    sheet: TeamSheet,
    own: Bonuses,
    opponent: Bonuses,
    *,
    flags: EngineFlags,
) -> tuple[list[FinalPlayer], list[str]]:
    """Steps 2a to 2d for one side."""
    ratings, lines = _squad_ratings(sheet)

    # 2a. Own bonuses and the opponent's malus, Cheat Code excluded. Positional bonuses
    # (Défense, Zahia) are read off the XI actually on the pitch, so they are scoped to
    # the starters; the substitutes are rated as if they had started, which is what they
    # end up doing when they come on.
    starter_lines = _starter_lines(sheet)
    adjusted, notes = apply_bonuses(ratings, starter_lines, own, opponent)

    # 2b-2d. Tactical, then mandatory substitutions, then phantom players.
    final_xi, log = build_final_xi(
        sheet,
        adjusted,
        notes,
        opponent_tonton_pat=opponent.tonton_pat,
        flags=flags,
    )

    # The XI changed, so the positional bonuses are re-read on the final XI
    # (invariant 4.1.7) and the Cheat Code lands now (invariant 4.1.4).
    final_lines = {slot.player_id: slot.slot_line for slot in final_xi if not slot.is_rotaldo}
    final_base = {
        slot.player_id: ratings.get(slot.player_id, slot.rating) + slot.line_penalty
        for slot in final_xi
        if not slot.is_rotaldo
    }
    final_adjusted, final_notes = apply_bonuses(
        final_base, final_lines, own, opponent, include_cheat_code=True
    )
    for slot in final_xi:
        if slot.is_rotaldo:
            continue
        slot.rating = final_adjusted[slot.player_id]
        slot.adjustments = final_notes[slot.player_id]

    return final_xi, log


def _real_goal_events(
    final_xi: list[FinalPlayer], opponent_xi: list[FinalPlayer]
) -> list[GoalEvent]:
    """Real goals scored by this XI, plus the own goals the opposing XI put in."""
    events: list[GoalEvent] = []
    for slot in final_xi:
        for _ in range(slot.real_goals):
            events.append(GoalEvent("real", slot.player_id, slot.name, slot.slot_line))
    for slot in opponent_xi:
        for _ in range(slot.own_goals):
            events.append(GoalEvent("own_goal", slot.player_id, slot.name, slot.slot_line))
    return events


def _goalkeeper_rating(final_xi: list[FinalPlayer]) -> float | None:
    for slot in final_xi:
        if slot.slot_line is Line.G:
            return slot.rating
    return None


def _cancel(
    events: list[GoalEvent],
    kinds: tuple[str, ...],
    label: str,
    count: int,
) -> int:
    """Cancel up to `count` goals, real ones first, by line priority (spec 3.5)."""
    order = {line: index for index, line in enumerate(VALISE_LINE_PRIORITY)}
    cancelled = 0
    for kind in kinds:
        candidates = [
            event for event in events
            if event.kind == kind and event.cancelled_by is None
        ]
        candidates.sort(key=lambda e: order.get(e.line, len(order)))
        for event in candidates:
            if cancelled >= count:
                return cancelled
            event.cancelled_by = label
            cancelled += 1
    return cancelled


def resolve_match(
    home: TeamSheet,
    away: TeamSheet,
    context: MatchContext | None = None,
    *,
    flags: EngineFlags = DEFAULT_FLAGS,
) -> MatchResult:
    """Resolve one fixture. `home` is the side that benefits from golden rule 1."""
    context = context or MatchContext()

    # ------------------------------------------------------------- 1. Miroirs
    home_bonuses, away_bonuses, mirror_log = resolve_mirrors(
        home.bonuses, away.bonuses, flags=flags
    )

    # ------------------------------------------------- 2. final XI, per side
    home_xi, home_log = _build_side(home, home_bonuses, away_bonuses, flags=flags)
    away_xi, away_log = _build_side(away, away_bonuses, home_bonuses, flags=flags)

    # ------------------------------------------- 4 & 5. real then virtual goals
    home_events = _real_goal_events(home_xi, away_xi)
    away_events = _real_goal_events(away_xi, home_xi)

    home_mpg, away_averages = mpg_goals(
        home_xi, away_xi, at_home=True, attacker_wins_ties=context.attacker_wins_ties
    )
    away_mpg, home_averages = mpg_goals(
        away_xi, home_xi, at_home=False, attacker_wins_ties=context.attacker_wins_ties
    )
    for xi, events in ((home_xi, home_events), (away_xi, away_events)):
        for slot in xi:
            if slot.mpg_goal:
                events.append(GoalEvent("mpg", slot.player_id, slot.name, slot.slot_line))

    # ------------------------------ 6. phantom players become conceded own goals
    home_rotaldo_csc = rotaldo_own_goals(home_xi)
    away_rotaldo_csc = rotaldo_own_goals(away_xi)

    # --------------------------------------- 7. arm the MPG save and the Valise
    results: dict[str, TeamResult] = {}
    for side, sheet, xi, events, opp_b, opp_xi, opp_csc, averages in (
        ("home", home, home_xi, home_events, away_bonuses, away_xi,
         away_rotaldo_csc, home_averages),
        ("away", away, away_xi, away_events, home_bonuses, home_xi,
         home_rotaldo_csc, away_averages),
    ):
        opp_keeper = _goalkeeper_rating(opp_xi)
        save_armed = opp_keeper is not None and opp_keeper >= MPG_SAVE_THRESHOLD

        # The save only ever takes a real goal, and never the whole total
        # (invariant 4.1.2). It is resolved before the Valise (spec 6.4).
        save_kinds: tuple[str, ...] = (
            ("own_goal", "real") if flags.mpg_save_can_cancel_own_goal else ("real",)
        )
        saved = _cancel(events, save_kinds, "save", 1) if save_armed else 0

        # The Valise takes a real goal if there is one, otherwise a virtual one. The own
        # goal conceded through Rotaldos sits outside this pool entirely (invariant 4.1.3).
        valise_kinds: tuple[str, ...] = (
            ("real", "own_goal", "mpg")
            if flags.valise_can_cancel_real_own_goal
            else ("real", "mpg")
        )
        valise = (
            _cancel(events, valise_kinds, "valise", flags.goals_cancelled_per_valise)
            if opp_b.valise
            else 0
        )

        # --------------------------------------------- 8. the score itself
        standing = [event for event in events if event.cancelled_by is None]
        real = sum(1 for e in standing if e.kind in ("real", "own_goal"))
        virtual = sum(1 for e in standing if e.kind == "mpg")
        for _ in range(opp_csc):
            events.append(GoalEvent("rotaldo_own_goal", None, "Rotaldo punishment", None))

        result = TeamResult(
            participant_id=sheet.participant_id,
            score=real + virtual + opp_csc,
            real_goals=real,
            mpg_goals=virtual,
            saved=saved,
            valise_cancelled=valise,
            rotaldo_own_goals_for=opp_csc,
            rotaldo_count=sum(1 for slot in xi if slot.is_rotaldo),
            final_xi=xi,
            goals=events,
            line_averages=averages,
            valise_compensation=VALISE_COMPENSATION if opp_b.valise else 0,
            log=mirror_log + (home_log if side == "home" else away_log),
        )
        if save_armed:
            result.log.append(
                f"MPG save armed by the opposing goalkeeper ({opp_keeper:g}): "
                f"{saved} goal(s) cancelled"
            )
        if opp_b.valise:
            result.log.append(f"Valise posed against this side: {valise} goal(s) cancelled")
        if opp_csc:
            result.log.append(f"{opp_csc} own goal(s) from the opponent's phantom players")
        results[side] = result

    return MatchResult(home=results["home"], away=results["away"], context=context)
