"""Turn a stored match report back into engine objects.

A fixture's report is written once, when it is resolved, and read back for display.
Rehydrating it means the web screens and the terminal go through exactly the same
wording helpers rather than each interpreting raw JSON their own way -- and it keeps
the promise that reading a report never recomputes it.
"""

from __future__ import annotations

from mpg.engine.lines import Line
from mpg.engine.models import (
    Duel,
    FinalPlayer,
    GoalEvent,
    MatchContext,
    MatchResult,
    TeamResult,
)


def _line(value: str | None) -> Line | None:
    if value is None:
        return None
    try:
        return Line(value)
    except ValueError:
        return None


def _duel(raw: dict) -> Duel:
    return Duel(
        line=_line(raw["line"]) or Line.G,
        opponent=raw.get("opponent"),
        rating_before=raw["rating_before"],
        outcome=raw["outcome"],
        cost=raw.get("cost", 0.0),
        rating_after=raw["rating_after"],
    )


def _slot(raw: dict) -> FinalPlayer:
    return FinalPlayer(
        slot_line=_line(raw["slot_line"]) or Line.G,
        player_id=raw["player_id"],
        name=raw["name"],
        rating=raw["rating"],
        real_goals=raw.get("real_goals", 0),
        own_goals=raw.get("own_goals", 0),
        is_rotaldo=raw.get("is_rotaldo", False),
        mpg_goal=raw.get("mpg_goal", False),
        replaced=raw.get("replaced"),
        replacement_kind=raw.get("replacement_kind"),
        line_penalty=raw.get("line_penalty", 0.0),
        adjustments=list(raw.get("adjustments") or []),
        duels=[_duel(duel) for duel in raw.get("duels") or []],
        mpg_skip_reason=raw.get("mpg_skip_reason"),
    )


def _goal(raw: dict) -> GoalEvent:
    return GoalEvent(
        kind=raw["kind"],
        player_id=raw.get("player_id"),
        name=raw.get("name", ""),
        line=_line(raw.get("line")),
        cancelled_by=raw.get("cancelled_by"),
    )


def _team(raw: dict) -> TeamResult:
    return TeamResult(
        participant_id=raw["participant_id"],
        score=raw.get("score", 0),
        real_goals=raw.get("real_goals", 0),
        mpg_goals=raw.get("mpg_goals", 0),
        saved=raw.get("saved", 0),
        valise_cancelled=raw.get("valise_cancelled", 0),
        rotaldo_own_goals_for=raw.get("rotaldo_own_goals_for", 0),
        rotaldo_count=raw.get("rotaldo_count", 0),
        final_xi=[_slot(slot) for slot in raw.get("final_xi") or []],
        goals=[_goal(goal) for goal in raw.get("goals") or []],
        line_averages={
            line: value
            for key, value in (raw.get("line_averages") or {}).items()
            if (line := _line(key)) is not None
        },
        valise_compensation=raw.get("valise_compensation", 0),
        log=list(raw.get("log") or []),
    )


def rehydrate(report: dict) -> MatchResult:
    """Rebuild a `MatchResult` from a stored report."""
    context = report.get("context") or {}
    return MatchResult(
        home=_team(report["home"]),
        away=_team(report["away"]),
        context=MatchContext(
            game_week=context.get("game_week", 1),
            return_legs=context.get("return_legs", True),
            playoff=context.get("playoff", False),
        ),
    )
