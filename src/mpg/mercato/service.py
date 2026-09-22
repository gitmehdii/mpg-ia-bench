"""The mercato as it runs against the database (spec 3.2, 4.4).

`resolve_round` is the delicate one: it is triggered both by the scheduler at the
deadline and by the last participant to validate, so the two must serialise. The
lock is taken on the league, not on the bids, because the resolution is a global
operation; idempotence rides on `resolved_at`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from random import Random

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mpg.config import (
    FIRST_ROUND_HOURS,
    MERCATO_BASE_ROUNDS,
    MERCATO_BUDGET,
    NEXT_ROUND_HOURS,
    get_settings,
)
from mpg.db.models import (
    Bid,
    League,
    LeagueStatus,
    MercatoLog,
    MercatoRound,
    Participant,
    Player,
    Roster,
    RoundValidation,
)
from mpg.engine.lines import Line, line_of
from mpg.mercato.allocation import allocate
from mpg.mercato.random_bids import generate_random_bids
from mpg.mercato.rules import BidRejected, quota_deficit, validate_bid
from mpg.mercato.types import BidInput, FreePlayer, LogEntry


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- helpers


def owned_player_ids(session: Session, league_id: int) -> set[str]:
    """Every player already owned by someone in the league (spec 3.2: one owner each)."""
    rows = session.execute(
        select(Roster.player_id)
        .join(Participant, Participant.id == Roster.participant_id)
        .where(Participant.league_id == league_id, Roster.sold_at.is_(None))
    ).scalars()
    return set(rows)


def squad_positions(session: Session, participant_id: int) -> list[int]:
    return list(
        session.execute(
            select(Player.ultra_position)
            .join(Roster, Roster.player_id == Player.id)
            .where(Roster.participant_id == participant_id, Roster.sold_at.is_(None))
        ).scalars()
    )


def free_players(session: Session, league: League, owned: set[str]) -> list[FreePlayer]:
    """Unowned players of the league's championship, with their current quotation."""
    rows = session.execute(
        select(Player.id, Player.ultra_position, Player.quotation, Player.last_name).where(
            Player.championship_id == league.championship_id,
            Player.quotation.is_not(None),
            Player.left_championship.is_(False),
        )
    ).all()
    return [
        FreePlayer(pid, line_of(ultra), quotation, name)
        for pid, ultra, quotation, name in rows
        if pid not in owned
    ]


def _log(session: Session, league_id: int, round_id: int | None, entry: LogEntry) -> None:
    session.add(
        MercatoLog(
            league_id=league_id,
            round_id=round_id,
            kind=entry.kind,
            player_id=entry.player_id,
            participant_id=entry.participant_id,
            detail=entry.detail or None,
            message=entry.message,
            created_at=_now(),
        )
    )


def open_participants(session: Session, league_id: int) -> list[Participant]:
    """Participants still taking part: budget left and mercato not closed by hand."""
    return list(
        session.execute(
            select(Participant)
            .where(
                Participant.league_id == league_id,
                Participant.mercato_closed.is_(False),
                Participant.budget > 0,
            )
            .order_by(Participant.id)
        ).scalars()
    )


# ----------------------------------------------------------------------- bid entry


def place_bid(
    session: Session, participant: Participant, round_: MercatoRound, player_id: str, amount: int
) -> Bid:
    """Record one closed bid, after the checks of spec 4.4.2."""
    if round_.resolved_at is not None:
        raise BidRejected("this round is already resolved", "round_resolved")

    player = session.get(Player, player_id)
    if player is None or player.quotation is None:
        raise BidRejected("unknown player", "unknown_player")

    owned = owned_player_ids(session, participant.league_id)
    owner = None
    if player_id in owned:
        owner = session.execute(
            select(Roster.participant_id)
            .join(Participant, Participant.id == Roster.participant_id)
            .where(Participant.league_id == participant.league_id, Roster.player_id == player_id)
        ).scalar_one_or_none()

    existing = session.execute(
        select(Bid).where(
            Bid.round_id == round_.id,
            Bid.participant_id == participant.id,
            Bid.player_id == player_id,
        )
    ).scalar_one_or_none()

    others_total = session.execute(
        select(func.coalesce(func.sum(Bid.amount), 0)).where(
            Bid.round_id == round_.id,
            Bid.participant_id == participant.id,
            Bid.player_id != player_id,
        )
    ).scalar_one()

    validate_bid(
        amount=amount,
        quotation=player.quotation,
        budget=participant.budget,
        other_bids_total=int(others_total),
        player_owned_by=owner,
        already_bid=existing is not None,
    )

    bid = Bid(
        round_id=round_.id,
        participant_id=participant.id,
        player_id=player_id,
        amount=amount,
        is_random=False,
        created_at=_now(),
    )
    session.add(bid)
    session.flush()
    return bid


def cancel_bid(
    session: Session, participant: Participant, round_: MercatoRound, player_id: str
) -> None:
    if round_.resolved_at is not None:
        raise BidRejected("this round is already resolved", "round_resolved")
    bid = session.execute(
        select(Bid).where(
            Bid.round_id == round_.id,
            Bid.participant_id == participant.id,
            Bid.player_id == player_id,
        )
    ).scalar_one_or_none()
    if bid is not None:
        session.delete(bid)


def validate_round(session: Session, participant: Participant, round_: MercatoRound) -> None:
    """Mark a participant as done for this round (spec 3.2)."""
    if round_.resolved_at is not None:
        raise BidRejected("this round is already resolved", "round_resolved")
    already = session.execute(
        select(RoundValidation).where(
            RoundValidation.round_id == round_.id,
            RoundValidation.participant_id == participant.id,
        )
    ).scalar_one_or_none()
    if already is None:
        session.add(
            RoundValidation(
                round_id=round_.id, participant_id=participant.id, validated_at=_now()
            )
        )
        session.flush()


def everyone_validated(session: Session, round_: MercatoRound, league_id: int) -> bool:
    """Spec 3.2: if everyone still open has validated, move to the next round at once."""
    open_ids = {p.id for p in open_participants(session, league_id)}
    if not open_ids:
        return True
    validated = set(
        session.execute(
            select(RoundValidation.participant_id).where(RoundValidation.round_id == round_.id)
        ).scalars()
    )
    return open_ids <= validated


# ------------------------------------------------------------------- round lifecycle


def open_mercato(
    session: Session, league: League, opens_at: datetime | None = None
) -> MercatoRound:
    """Open the mercato and create its first round (48 h, then 24 h per round)."""
    opens_at = opens_at or _now()
    league.status = LeagueStatus.MERCATO
    league.mercato_opens_at = opens_at
    for participant in league.participants:
        participant.budget = MERCATO_BUDGET
    round_ = MercatoRound(
        league_id=league.id,
        number=1,
        opens_at=opens_at,
        deadline_at=opens_at + timedelta(hours=FIRST_ROUND_HOURS),
        seed=Random().randrange(1, 2**31 - 1),
    )
    session.add(round_)
    session.flush()
    return round_


def current_round(session: Session, league_id: int) -> MercatoRound | None:
    return session.execute(
        select(MercatoRound)
        .where(MercatoRound.league_id == league_id, MercatoRound.resolved_at.is_(None))
        .order_by(MercatoRound.number)
    ).scalars().first()


def resolve_round(session: Session, round_id: int) -> bool:
    """Resolve one round. Returns False when there was nothing to do.

    Idempotent: a cron that fires again on an already-resolved round exits at once.
    """
    round_ = session.get(MercatoRound, round_id)
    if round_ is None:
        return False

    # Pessimistic lock on the league, not on the bids: two concurrent resolutions
    # (the cron and a last validation arriving in the same millisecond) must serialise.
    league = session.execute(
        select(League).where(League.id == round_.league_id).with_for_update()
    ).scalar_one()

    session.refresh(round_)
    if round_.resolved_at is not None:
        return False                                  # already resolved, do nothing

    rng = Random(round_.seed)
    participants = open_participants(session, league.id)
    owned = owned_player_ids(session, league.id)

    # --- 1. random bids for whoever never validated in time
    validated = set(
        session.execute(
            select(RoundValidation.participant_id).where(RoundValidation.round_id == round_.id)
        ).scalars()
    )
    pool = free_players(session, league, owned)
    for participant in participants:
        if participant.id in validated:
            continue
        deficit = quota_deficit(squad_positions(session, participant.id))
        already = set(
            session.execute(
                select(Bid.player_id).where(
                    Bid.round_id == round_.id, Bid.participant_id == participant.id
                )
            ).scalars()
        )
        committed = int(
            session.execute(
                select(func.coalesce(func.sum(Bid.amount), 0)).where(
                    Bid.round_id == round_.id, Bid.participant_id == participant.id
                )
            ).scalar_one()
        )
        candidates = [p for p in pool if p.player_id not in already]
        generated = generate_random_bids(
            participant.id,
            participant.budget - committed,
            deficit,
            candidates,
            round_.number,
            rng,
        )
        for generated_bid in generated:
            session.add(
                Bid(
                    round_id=round_.id,
                    participant_id=participant.id,
                    player_id=generated_bid.player_id,
                    amount=generated_bid.amount,
                    is_random=True,
                    created_at=_now(),
                )
            )
        if generated:
            _log(
                session,
                league.id,
                round_.id,
                LogEntry(
                    kind="random_bids",
                    message=(
                        f"participant {participant.id} did not validate: "
                        f"{len(generated)} random bid(s) generated"
                    ),
                    participant_id=participant.id,
                    detail={"bids": [[b.player_id, b.amount] for b in generated]},
                ),
            )
    session.flush()

    # --- 2. award the players
    bids = list(
        session.execute(select(Bid).where(Bid.round_id == round_.id)).scalars()
    )
    budgets = {p.id: p.budget for p in participants}
    result = allocate(
        [BidInput(b.participant_id, b.player_id, b.amount, b.is_random) for b in bids],
        budgets,
        rng,
    )

    won: set[tuple[int, str]] = set()
    for allocation in result.allocations:
        session.add(
            Roster(
                participant_id=allocation.participant_id,
                player_id=allocation.player_id,
                bought_at=_now(),
                bought_price=allocation.price,
            )
        )
        won.add((allocation.participant_id, allocation.player_id))
        _log(
            session,
            league.id,
            round_.id,
            LogEntry(
                kind="award",
                message=(
                    f"{allocation.player_id} awarded to participant "
                    f"{allocation.participant_id} for {allocation.price}"
                ),
                player_id=allocation.player_id,
                participant_id=allocation.participant_id,
                detail={"price": allocation.price},
            ),
        )
    for bid in bids:
        bid.won = (bid.participant_id, bid.player_id) in won
    for participant in participants:
        # Budgets are only debited now: a losing bid never cost anything.
        participant.budget = result.budgets[participant.id]
    for entry in result.log:
        _log(session, league.id, round_.id, entry)

    # --- 3. close the round
    round_.resolved_at = _now()
    session.flush()

    if mercato_should_close(session, league, round_):
        close_mercato(session, league)
    else:
        create_next_round(session, league, round_)
    session.flush()
    return True


def create_next_round(session: Session, league: League, previous: MercatoRound) -> MercatoRound:
    opens_at = _now()
    round_ = MercatoRound(
        league_id=league.id,
        number=previous.number + 1,
        opens_at=opens_at,
        deadline_at=opens_at + timedelta(hours=NEXT_ROUND_HOURS),
        seed=Random().randrange(1, 2**31 - 1),
    )
    session.add(round_)
    session.flush()
    return round_


def mercato_should_close(session: Session, league: League, round_: MercatoRound) -> bool:
    """Spec 4.4.5: every budget at zero, everyone closed by hand, or the 7 days are up."""
    settings = get_settings()
    participants = list(
        session.execute(
            select(Participant).where(Participant.league_id == league.id)
        ).scalars()
    )
    if all(p.budget <= 0 for p in participants):
        return True
    if all(p.mercato_closed for p in participants):
        return True
    if settings.mercato_auto_close and league.mercato_opens_at is not None:
        opened = league.mercato_opens_at
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=UTC)
        if _now() - opened >= timedelta(days=7):
            return True
    # Past the base rounds, only keep going while someone still has budget to spend
    # and has not closed their mercato.
    if round_.number >= MERCATO_BASE_ROUNDS:
        return not open_participants(session, league.id)
    return False


# ------------------------------------------------------------------------- draft


def draft_missing_players(session: Session, league: League) -> list[Roster]:
    """Spec 4.4.5: top every short squad up with the cheapest free players.

    A squad that cannot field a lineup is never an acceptable outcome of a mercato, so
    the players are handed over free of charge when the budget runs out.
    """
    added: list[Roster] = []
    participants = list(
        session.execute(
            select(Participant).where(Participant.league_id == league.id).order_by(Participant.id)
        ).scalars()
    )
    for participant in participants:
        owned = owned_player_ids(session, league.id)
        pool = free_players(session, league, owned)
        by_line: dict[Line, list[FreePlayer]] = {}
        for player in pool:
            by_line.setdefault(player.line, []).append(player)
        for players in by_line.values():
            # Cheapest first; the id keeps the order stable between two runs.
            players.sort(key=lambda p: (p.quotation, p.player_id))

        deficit = quota_deficit(squad_positions(session, participant.id))
        for line, missing in sorted(deficit.items()):
            for _ in range(missing):
                candidates = by_line.get(line) or []
                if not candidates:
                    _log(
                        session,
                        league.id,
                        None,
                        LogEntry(
                            kind="draft_exhausted",
                            message=f"no free {line} left to top up participant {participant.id}",
                            participant_id=participant.id,
                        ),
                    )
                    break
                player = candidates.pop(0)
                price = min(participant.budget, player.quotation)
                free_of_charge = price < player.quotation
                participant.budget -= price
                roster = Roster(
                    participant_id=participant.id,
                    player_id=player.player_id,
                    bought_at=_now(),
                    bought_price=price,
                    from_draft=True,
                )
                session.add(roster)
                session.flush()
                added.append(roster)
                _log(
                    session,
                    league.id,
                    None,
                    LogEntry(
                        kind="draft",
                        message=(
                            f"{player.player_id} ({line}, quotation {player.quotation}) handed to "
                            f"participant {participant.id} for {price}"
                            + (" (budget exhausted, free of charge)" if free_of_charge else "")
                        ),
                        player_id=player.player_id,
                        participant_id=participant.id,
                        detail={"price": price, "quotation": player.quotation,
                                "free_of_charge": free_of_charge},
                    ),
                )
    session.flush()
    return added


def close_mercato(session: Session, league: League) -> None:
    """Close the mercato: top up the short squads, then the league can start."""
    draft_missing_players(session, league)
    league.status = LeagueStatus.RUNNING
    league.mercato_closed_at = _now()
    for participant in league.participants:
        participant.mercato_closed = True
    session.flush()
