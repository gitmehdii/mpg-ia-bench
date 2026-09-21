"""Lineups and bonuses: validation and persistence (spec 3.3, 3.5).

The engine applies whatever bonuses it is handed; policing the quotas is this layer's
job, so that a replay of an old fixture is never silently changed by today's rules.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from mpg.config import BONUS_QUOTAS, UNLIMITED_BONUSES
from mpg.db.models import (
    BonusUsage,
    League,
    Lineup,
    LineupSlot,
    LiveSubRecord,
    Participant,
    Player,
    Roster,
    TacticalSubRule,
)
from mpg.engine.formations import BENCH_SIZE, STARTERS, is_legal, slots_of
from mpg.engine.lines import Line, line_of
from mpg.engine.substitutions import MAX_TACTICAL_SUBS


class LineupError(ValueError):
    pass


class BonusError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def owned_ids(session: Session, participant_id: int) -> set[str]:
    return set(
        session.execute(
            select(Roster.player_id).where(
                Roster.participant_id == participant_id, Roster.sold_at.is_(None)
            )
        ).scalars()
    )


def _lines(session: Session, player_ids: list[str]) -> dict[str, Line]:
    rows = session.execute(
        select(Player.id, Player.ultra_position).where(Player.id.in_(player_ids))
    ).all()
    return {pid: line_of(ultra) for pid, ultra in rows}


def validate_lineup(
    session: Session,
    participant: Participant,
    *,
    formation: str,
    starters: list[str],
    bench: list[str],
    tactical_subs: list[tuple[str, str, float]] | None = None,
    bonus_424: bool = False,
) -> None:
    """Spec 3.3: 11 starters and 7 substitutes, one of them a goalkeeper."""
    if len(starters) != STARTERS:
        raise LineupError(f"a lineup holds {STARTERS} starters, got {len(starters)}")
    if len(bench) != BENCH_SIZE:
        raise LineupError(f"a bench holds {BENCH_SIZE} substitutes, got {len(bench)}")

    everyone = [*starters, *bench]
    duplicates = [pid for pid, count in Counter(everyone).items() if count > 1]
    if duplicates:
        raise LineupError(f"a player cannot be listed twice: {', '.join(sorted(duplicates))}")

    owned = owned_ids(session, participant.id)
    foreign = [pid for pid in everyone if pid not in owned]
    if foreign:
        raise LineupError(f"these players are not in the squad: {', '.join(sorted(foreign))}")

    if not is_legal(formation, bonus_424=bonus_424):
        if formation == "4-2-4":
            raise LineupError("the 4-2-4 is only reachable through the 424 bonus")
        raise LineupError(f"unknown formation: {formation}")

    lines = _lines(session, everyone)
    wanted = Counter(slots_of(formation))
    fielded = Counter(lines[pid] for pid in starters)
    if fielded != wanted:
        raise LineupError(
            f"the XI does not match the {formation}: "
            f"expected {dict(wanted)}, got {dict(fielded)}"
        )

    # Spec 3.3: at least one goalkeeper among the substitutes, which is what lets a
    # goalkeeper who did not play be replaced at all (invariant 4.1.1).
    if not any(lines[pid] is Line.G for pid in bench):
        raise LineupError("the bench must hold at least one goalkeeper")

    for starter_id, sub_id, _threshold in tactical_subs or []:
        if starter_id not in starters:
            raise LineupError(f"{starter_id} is not a starter")
        if sub_id not in bench:
            raise LineupError(f"{sub_id} is not on the bench")
        if lines.get(starter_id) is Line.G or lines.get(sub_id) is Line.G:
            raise LineupError("a tactical substitution can never involve a goalkeeper")
    if len(tactical_subs or []) > MAX_TACTICAL_SUBS:
        raise LineupError(f"at most {MAX_TACTICAL_SUBS} tactical substitutions")
    subs_used = [sub_id for _, sub_id, _ in tactical_subs or []]
    if len(subs_used) != len(set(subs_used)):
        raise LineupError("one substitute cannot cover several starters")


def save_lineup(
    session: Session,
    participant: Participant,
    game_week: int,
    *,
    formation: str,
    starters: list[str],
    bench: list[str],
    tactical_subs: list[tuple[str, str, float]] | None = None,
    live_mode: bool = False,
) -> Lineup:
    bonuses = session.execute(
        select(BonusUsage).where(
            BonusUsage.participant_id == participant.id,
            BonusUsage.game_week_number == game_week,
        )
    ).scalars().all()
    bonus_424 = any(b.bonus_type == "formation_424" for b in bonuses)

    validate_lineup(
        session,
        participant,
        formation=formation,
        starters=starters,
        bench=bench,
        tactical_subs=tactical_subs,
        bonus_424=bonus_424,
    )

    lineup = session.execute(
        select(Lineup).where(
            Lineup.participant_id == participant.id, Lineup.game_week_number == game_week
        )
    ).scalar_one_or_none()
    if lineup is None:
        lineup = Lineup(
            participant_id=participant.id,
            game_week_number=game_week,
            formation=formation,
        )
        session.add(lineup)
        session.flush()
    else:
        for slot in list(lineup.slots):
            session.delete(slot)
        for rule in list(lineup.tactical_subs):
            session.delete(rule)
        session.flush()

    lineup.formation = formation
    lineup.live_mode = live_mode
    lineup.submitted_at = _now()
    lineup.inherited = False

    for order, player_id in enumerate(starters):
        session.add(
            LineupSlot(lineup_id=lineup.id, player_id=player_id, role="starter", order=order)
        )
    for order, player_id in enumerate(bench):
        # The bench order is the priority order for mandatory substitutions (spec 3.4).
        session.add(
            LineupSlot(lineup_id=lineup.id, player_id=player_id, role="bench", order=order)
        )
    for starter_id, sub_id, threshold in tactical_subs or []:
        session.add(
            TacticalSubRule(
                lineup_id=lineup.id, starter_id=starter_id, sub_id=sub_id, threshold=threshold
            )
        )
    session.flush()
    return lineup


def inherit_lineup(session: Session, participant: Participant, game_week: int) -> Lineup | None:
    """Spec 3.3: a forgotten lineup falls back on last week's XI.

    The starters carry over, and the bench only if the participant asked for it.
    """
    previous = session.execute(
        select(Lineup)
        .where(Lineup.participant_id == participant.id, Lineup.game_week_number < game_week)
        .order_by(Lineup.game_week_number.desc())
    ).scalars().first()
    if previous is None:
        return None

    lineup = Lineup(
        participant_id=participant.id,
        game_week_number=game_week,
        formation=previous.formation,
        live_mode=previous.live_mode,
        inherited=True,
    )
    session.add(lineup)
    session.flush()
    for slot in previous.slots:
        if slot.role == "bench" and not participant.keep_bench:
            continue
        session.add(
            LineupSlot(
                lineup_id=lineup.id, player_id=slot.player_id, role=slot.role, order=slot.order
            )
        )
    session.flush()
    return lineup


# ------------------------------------------------------------------------- bonuses


def bonus_usage_count(session: Session, participant_id: int, bonus_type: str) -> int:
    return len(
        session.execute(
            select(BonusUsage).where(
                BonusUsage.participant_id == participant_id,
                BonusUsage.bonus_type == bonus_type,
            )
        ).scalars().all()
    )


def pose_bonus(
    session: Session,
    participant: Participant,
    game_week: int,
    bonus_type: str,
    target_player_id: str | None = None,
) -> BonusUsage:
    """Spec 3.5: one bonus per game week, except Défense and Capitaine, plus the
    per-season quota that depends on the league's size."""
    league = session.get(League, participant.league_id)
    existing = session.execute(
        select(BonusUsage).where(
            BonusUsage.participant_id == participant.id,
            BonusUsage.game_week_number == game_week,
        )
    ).scalars().all()

    if any(b.bonus_type == bonus_type for b in existing):
        raise BonusError(f"{bonus_type} is already posed for game week {game_week}")

    if bonus_type not in UNLIMITED_BONUSES:
        quota = BONUS_QUOTAS.get(bonus_type, {}).get(league.size, 0)
        if quota == 0:
            raise BonusError(f"{bonus_type} is not available in a league of {league.size}")
        if bonus_usage_count(session, participant.id, bonus_type) >= quota:
            raise BonusError(f"{bonus_type} quota exhausted ({quota} for the season)")
        if any(b.bonus_type not in UNLIMITED_BONUSES for b in existing):
            raise BonusError("only one bonus per game week, Défense and Capitaine aside")

    if bonus_type in ("mcdo", "captain"):
        if target_player_id is None:
            raise BonusError(f"{bonus_type} needs a target player")
        if target_player_id not in owned_ids(session, participant.id):
            raise BonusError("the target is not in the squad")
        if bonus_type == "captain":
            lines = _lines(session, [target_player_id])
            if lines.get(target_player_id) is Line.G:
                raise BonusError("the captain can never be the goalkeeper")

    usage = BonusUsage(
        participant_id=participant.id,
        game_week_number=game_week,
        bonus_type=bonus_type,
        target_player_id=target_player_id,
    )
    session.add(usage)
    session.flush()
    return usage


def record_live_sub(
    session: Session, lineup: Lineup, starter_id: str, sub_id: str
) -> LiveSubRecord:
    """Spec 3.4: one change per starter, from the whole squad, before kick-off."""
    existing = session.execute(
        select(LiveSubRecord).where(LiveSubRecord.lineup_id == lineup.id)
    ).scalars().all()
    if any(record.starter_id == starter_id for record in existing):
        raise LineupError("this starter has already been changed once")

    record = LiveSubRecord(
        lineup_id=lineup.id,
        starter_id=starter_id,
        sub_id=sub_id,
        order=len(existing),
        made_at=_now(),
    )
    session.add(record)
    session.flush()
    return record
