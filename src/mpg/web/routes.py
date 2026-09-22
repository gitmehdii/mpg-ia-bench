"""The `/ui` screens.

Server-rendered, and reading the same data the JSON API exposes. The API stays the
source of truth: nothing here reaches past a rule the API enforces, and in particular
no screen shows a bid, a budget or a lineup the API would refuse to serve.
"""

from __future__ import annotations

import contextlib
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from mpg import presentation as fr
from mpg.api.deps import SessionDep
from mpg.api.security import create_token, hash_password, verify_password
from mpg.config import BONUS_QUOTAS, UNLIMITED_BONUSES
from mpg.db.models import (
    Bid,
    BonusUsage,
    League,
    LeagueMatch,
    LeagueStatus,
    Lineup,
    MercatoLog,
    Participant,
    Performance,
    Player,
    Roster,
    RoundValidation,
    User,
)
from mpg.engine.formations import BENCH_SIZE, FORMATIONS, STARTERS, is_legal, slots_of
from mpg.engine.lines import Line, line_of
from mpg.engine.substitutions import MAX_TACTICAL_SUBS
from mpg.mercato.rules import BidRejected, quota_deficit
from mpg.mercato.service import (
    cancel_bid,
    current_round,
    everyone_validated,
    free_players,
    open_mercato,
    owned_player_ids,
    place_bid,
    resolve_round,
    validate_round,
)
from mpg.report_view import rehydrate
from mpg.services.leagues import LeagueError, create_league, generate_fixtures, join_league
from mpg.services.lineups import BonusError, LineupError, pose_bonus, save_lineup
from mpg.services.matchday import resolve_game_week
from mpg.services.standings import as_table, standings
from mpg.web.deps import (
    UiAdmin,
    UiLeague,
    UiParticipant,
    UiUser,
    clear_session_cookie,
    redirect,
    set_session_cookie,
)
from mpg.web.templating import render

router = APIRouter(prefix="/ui", tags=["ui"])

FormStr = Annotated[str, Form()]


# ------------------------------------------------------------------------- session


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str | None = None) -> HTMLResponse:
    return render(request, "login.html", error=error, user=None)


@router.post("/login")
def login(request: Request, session: SessionDep, email: FormStr, password: FormStr):
    user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        return render(
            request, "login.html", user=None,
            error="Adresse e-mail ou mot de passe incorrect.",
        )
    response = redirect("/ui/")
    set_session_cookie(response, create_token(user.id))
    return response


@router.post("/register")
def register(
    request: Request,
    session: SessionDep,
    email: FormStr,
    display_name: FormStr,
    password: FormStr,
):
    if len(password) < 8:
        return render(
            request, "login.html", user=None,
            error="Le mot de passe doit faire au moins 8 caractères.",
        )
    if session.execute(select(User).where(User.email == email)).first():
        return render(
            request, "login.html", user=None,
            error="Cette adresse e-mail est déjà utilisée.",
        )
    user = User(
        email=email, display_name=display_name, password_hash=hash_password(password)
    )
    session.add(user)
    session.flush()
    response = redirect("/ui/")
    set_session_cookie(response, create_token(user.id))
    return response


@router.get("/logout")
def logout() -> RedirectResponse:
    response = redirect("/ui/login")
    clear_session_cookie(response)
    return response


# -------------------------------------------------------------------------- leagues


@router.get("/", response_class=HTMLResponse)
def home(request: Request, session: SessionDep, user: UiUser) -> HTMLResponse:
    rows = [
        {"participant": participant, "league": session.get(League, participant.league_id)}
        for participant in session.execute(
            select(Participant).where(Participant.user_id == user.id)
        ).scalars()
    ]
    return render(request, "leagues.html", user=user, rows=rows)


@router.post("/leagues")
def create(
    request: Request,
    session: SessionDep,
    user: UiUser,
    name: FormStr,
    team_name: FormStr,
    size: Annotated[int, Form()] = 4,
    return_legs: Annotated[int, Form()] = 1,
):
    try:
        league = create_league(
            session, name=name, championship_id=1, size=size, creator=user,
            team_name=team_name, return_legs=bool(return_legs),
        )
    except LeagueError as error:
        return render(request, "leagues.html", user=user, rows=[], error=str(error))
    return redirect(f"/ui/leagues/{league.id}")


@router.post("/leagues/join")
def join(
    request: Request, session: SessionDep, user: UiUser, code: FormStr, team_name: FormStr
):
    league = session.execute(
        select(League).where(League.code == code.strip().upper())
    ).scalar_one_or_none()
    if league is None:
        rows = [
            {"participant": p, "league": session.get(League, p.league_id)}
            for p in session.execute(
                select(Participant).where(Participant.user_id == user.id)
            ).scalars()
        ]
        return render(
            request, "leagues.html", user=user, rows=rows,
            error="Aucune ligue ne porte ce code.",
        )
    try:
        join_league(session, league, user, team_name)
    except LeagueError as error:
        rows = [
            {"participant": p, "league": session.get(League, p.league_id)}
            for p in session.execute(
                select(Participant).where(Participant.user_id == user.id)
            ).scalars()
        ]
        return render(request, "leagues.html", user=user, rows=rows, error=str(error))
    return redirect(f"/ui/leagues/{league.id}")


@router.get("/leagues/{league_id}", response_class=HTMLResponse)
def dashboard(
    request: Request,
    session: SessionDep,
    user: UiUser,
    league: UiLeague,
    participant: UiParticipant,
) -> HTMLResponse:
    """Standings and fixture list. The goal difference is split between real and
    virtual goals, because the split is one of the tie-breaks."""
    table = as_table(standings(session, league))
    names = {
        row.id: row.team_name
        for row in session.execute(
            select(Participant).where(Participant.league_id == league.id)
        ).scalars()
    }
    fixtures = session.execute(
        select(LeagueMatch).where(LeagueMatch.league_id == league.id)
        .order_by(LeagueMatch.game_week_number, LeagueMatch.id)
    ).scalars().all()

    # The real/virtual split lives in the stored reports, so it costs no recompute.
    split: dict[int, dict[str, int]] = {pid: {"real": 0, "mpg": 0} for pid in names}
    for fixture in fixtures:
        if not fixture.report:
            continue
        for side, pid in (
            ("home", fixture.home_participant_id),
            ("away", fixture.away_participant_id),
        ):
            body = fixture.report.get(side) or {}
            if pid in split:
                split[pid]["real"] += body.get("real_goals", 0)
                split[pid]["mpg"] += body.get("mpg_goals", 0)

    weeks: dict[int, list[LeagueMatch]] = {}
    for fixture in fixtures:
        weeks.setdefault(fixture.game_week_number, []).append(fixture)

    return render(
        request, "league.html", user=user, league=league, participant=participant,
        table=table, names=names, weeks=sorted(weeks.items()), split=split,
    )


@router.post("/leagues/{league_id}/fixtures")
def build_fixtures(session: SessionDep, league: UiLeague, admin: UiAdmin):
    # Already generated is not an error here: the button is idempotent on purpose.
    with contextlib.suppress(LeagueError):
        generate_fixtures(session, league)
    return redirect(f"/ui/leagues/{league.id}")


@router.get("/leagues/{league_id}/matches/{fixture_id}", response_class=HTMLResponse)
def report(
    request: Request,
    session: SessionDep,
    user: UiUser,
    league: UiLeague,
    participant: UiParticipant,
    fixture_id: int,
) -> HTMLResponse:
    """The match report: the final XI, every rating with what moved it, the
    substitutions, and each MPG goal explained duel by duel."""
    fixture = session.get(LeagueMatch, fixture_id)
    if fixture is None or fixture.league_id != league.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ce match n’existe pas.")
    if not fixture.report:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Cette journée n’a pas encore été résolue."
        )

    result = rehydrate(fixture.report)
    names = {
        row.id: row.team_name
        for row in session.execute(
            select(Participant).where(Participant.league_id == league.id)
        ).scalars()
    }

    def keeper(team) -> float | None:
        return next((s.rating for s in team.final_xi if s.slot_line is Line.G), None)

    def bonuses_of(participant_id: int) -> list[str]:
        rows = session.execute(
            select(BonusUsage).where(
                BonusUsage.participant_id == participant_id,
                BonusUsage.game_week_number == fixture.game_week_number,
            )
        ).scalars().all()
        labels = []
        for row in rows:
            label = fr.bonus_name(row.bonus_type)
            if row.target_player_id:
                label += f" sur {row.target_player_id}"
            labels.append(label)
        return labels

    return render(
        request, "report.html", user=user, league=league, participant=participant,
        result=result,
        home_name=names.get(fixture.home_participant_id, "Domicile"),
        away_name=names.get(fixture.away_participant_id, "Extérieur"),
        home_bonuses=bonuses_of(fixture.home_participant_id),
        away_bonuses=bonuses_of(fixture.away_participant_id),
        home_keeper=keeper(result.home), away_keeper=keeper(result.away),
    )


@router.post("/leagues/{league_id}/resolve")
def resolve_week(
    session: SessionDep, league: UiLeague, admin: UiAdmin, game_week: Annotated[int, Form()]
):
    with contextlib.suppress(ValueError):
        resolve_game_week(session, league, game_week)
    return redirect(f"/ui/leagues/{league.id}")


# -------------------------------------------------------------------------- mercato


@router.post("/leagues/{league_id}/mercato/open")
def open_mercato_screen(session: SessionDep, league: UiLeague, admin: UiAdmin):
    if league.status is LeagueStatus.CREATED:
        open_mercato(session, league)
        generate_fixtures(session, league)
    return redirect(f"/ui/leagues/{league.id}/mercato")


@router.get("/leagues/{league_id}/mercato", response_class=HTMLResponse)
def mercato_screen(
    request: Request,
    session: SessionDep,
    user: UiUser,
    league: UiLeague,
    participant: UiParticipant,
    position: str | None = None,
    club: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    """The free pool, my own bids, my own budget.

    Nothing here says anything about the other participants' bids -- not a count, not a
    maximum, not a "contested" marker. Everything shown is scoped to the caller, and
    the free pool looks identical whether a player has drawn three bids or none.
    """
    round_ = current_round(session, league.id)
    owned = owned_player_ids(session, league.id)
    pool = free_players(session, league, owned)

    if position:
        pool = [p for p in pool if p.line == position]
    rows = {
        row.id: row
        for row in session.execute(
            select(Player).where(Player.id.in_([p.player_id for p in pool[:400]]))
        ).scalars()
    }
    if club:
        pool = [p for p in pool if rows.get(p.player_id) and rows[p.player_id].club_id == club]
    pool = sorted(pool, key=lambda p: (-p.quotation, p.player_id))[:150]

    my_bids = []
    validated = False
    if round_ is not None:
        my_bids = session.execute(
            select(Bid).where(
                Bid.round_id == round_.id, Bid.participant_id == participant.id
            )
        ).scalars().all()
        validated = session.execute(
            select(RoundValidation).where(
                RoundValidation.round_id == round_.id,
                RoundValidation.participant_id == participant.id,
            )
        ).first() is not None

    squad = session.execute(
        select(Player).join(Roster, Roster.player_id == Player.id)
        .where(Roster.participant_id == participant.id, Roster.sold_at.is_(None))
    ).scalars().all()
    deficit = quota_deficit([p.ultra_position for p in squad])

    # Once a round is resolved, who got what is public -- and so are the draws.
    awards = session.execute(
        select(MercatoLog).where(
            MercatoLog.league_id == league.id, MercatoLog.kind.in_(("award", "draw", "draft"))
        ).order_by(MercatoLog.id.desc()).limit(60)
    ).scalars().all()
    names = {
        row.id: row.team_name
        for row in session.execute(
            select(Participant).where(Participant.league_id == league.id)
        ).scalars()
    }

    return render(
        request, "mercato.html", user=user, league=league, participant=participant,
        round=round_, pool=pool, players=rows, my_bids=my_bids, squad=squad,
        deficit=deficit, awards=awards, names=names, error=error,
        position=position or "", validated=validated,
        clubs=sorted({r.club_id for r in rows.values() if r.club_id}),
    )


@router.post("/leagues/{league_id}/mercato/bids")
def submit_bid_screen(
    session: SessionDep,
    league: UiLeague,
    participant: UiParticipant,
    player_id: FormStr,
    amount: Annotated[int, Form()],
):
    round_ = current_round(session, league.id)
    if round_ is None:
        return redirect(f"/ui/leagues/{league.id}/mercato")
    try:
        place_bid(session, participant, round_, player_id, amount)
    except BidRejected as error:
        return redirect(f"/ui/leagues/{league.id}/mercato?error={quote(str(error))}")
    return redirect(f"/ui/leagues/{league.id}/mercato")


@router.post("/leagues/{league_id}/mercato/bids/{player_id}/delete")
def withdraw_bid_screen(
    session: SessionDep, league: UiLeague, participant: UiParticipant, player_id: str
):
    round_ = current_round(session, league.id)
    if round_ is not None:
        with contextlib.suppress(BidRejected):
            cancel_bid(session, participant, round_, player_id)
    return redirect(f"/ui/leagues/{league.id}/mercato")


@router.post("/leagues/{league_id}/mercato/validate")
def validate_screen(session: SessionDep, league: UiLeague, participant: UiParticipant):
    round_ = current_round(session, league.id)
    if round_ is not None:
        with contextlib.suppress(BidRejected):
            validate_round(session, participant, round_)
        if everyone_validated(session, round_, league.id):
            resolve_round(session, round_.id)
    return redirect(f"/ui/leagues/{league.id}/mercato")


@router.post("/leagues/{league_id}/mercato/close")
def close_screen(session: SessionDep, league: UiLeague, participant: UiParticipant):
    participant.mercato_closed = True
    session.flush()
    round_ = current_round(session, league.id)
    if round_ is not None and everyone_validated(session, round_, league.id):
        resolve_round(session, round_.id)
    return redirect(f"/ui/leagues/{league.id}/mercato")


# ----------------------------------------------------------------------------- team


@router.get("/leagues/{league_id}/team", response_class=HTMLResponse)
def team_screen(
    request: Request,
    session: SessionDep,
    user: UiUser,
    league: UiLeague,
    participant: UiParticipant,
    game_week: int | None = None,
    formation: str | None = None,
    error: str | None = None,
    message: str | None = None,
) -> HTMLResponse:
    """The squad, the lineup for a game week, the bonuses and their remaining quota."""
    week = game_week or (league.first_game_week or 1)

    squad = session.execute(
        select(Player).join(Roster, Roster.player_id == Player.id)
        .where(Roster.participant_id == participant.id, Roster.sold_at.is_(None))
    ).scalars().all()
    by_line: dict[Line, list[Player]] = {}
    for player in squad:
        by_line.setdefault(line_of(player.ultra_position), []).append(player)
    for players in by_line.values():
        players.sort(key=lambda p: (-(p.quotation or 0), p.last_name))

    lineup = session.execute(
        select(Lineup).where(
            Lineup.participant_id == participant.id, Lineup.game_week_number == week
        )
    ).scalar_one_or_none()
    starters, bench = [], []
    if lineup:
        slots = sorted(lineup.slots, key=lambda s: s.order)
        index = {p.id: p for p in squad}
        starters = [
            index[s.player_id] for s in slots
            if s.role == "starter" and s.player_id in index
        ]
        bench = [
            index[s.player_id] for s in slots if s.role == "bench" and s.player_id in index
        ]

    # Who actually played that game week, so the screen can flag the absentees.
    played = {
        row.player_id: row
        for row in session.execute(
            select(Performance).where(
                Performance.player_id.in_([p.id for p in squad]),
                Performance.game_week_number == week,
            )
        ).scalars()
    }

    used = session.execute(
        select(BonusUsage).where(BonusUsage.participant_id == participant.id)
    ).scalars().all()
    quotas = []
    for key, by_size in BONUS_QUOTAS.items():
        total = by_size.get(league.size, 0)
        if not total:
            continue
        spent = sum(1 for row in used if row.bonus_type == key)
        quotas.append({"name": fr.bonus_name(key), "spent": spent, "total": total})
    unlimited = [fr.bonus_name(key) for key in sorted(UNLIMITED_BONUSES)]
    this_week = [row for row in used if row.game_week_number == week]

    chosen = formation or (lineup.formation if lineup else "4-4-2")
    has_424 = any(row.bonus_type == "formation_424" for row in this_week)
    if not is_legal(chosen, bonus_424=has_424):
        chosen = "4-4-2"
    picked = {
        "starters": [p.id for p in starters],
        "bench": [p.id for p in bench],
    }

    return render(
        request, "team.html", user=user, league=league, participant=participant,
        week=week, by_line=by_line, lineup=lineup, starters=starters, bench=bench,
        played=played, quotas=quotas, unlimited=unlimited, this_week=this_week,
        error=error, message=message, squad=squad, picked=picked,
        formation=chosen, slots=slots_of(chosen),
        formations=[f for f in FORMATIONS if is_legal(f, bonus_424=has_424)],
        line_order=[(Line.G, "Gardiens"), (Line.D, "Défenseurs"),
                    (Line.M, "Milieux"), (Line.A, "Attaquants")],
    )


@router.post("/leagues/{league_id}/team/lineups/{game_week}")
async def save_lineup_screen(
    request: Request,
    session: SessionDep,
    league: UiLeague,
    participant: UiParticipant,
    game_week: int,
):
    """Save the XI. The slots arrive as `starter_0..10` and `bench_0..6`, one select
    per slot, which keeps the form usable without any client-side code."""
    form = await request.form()
    formation = str(form.get("formation", "4-4-2"))
    starters = [str(form.get(f"starter_{index}", "")) for index in range(STARTERS)]
    bench = [str(form.get(f"bench_{index}", "")) for index in range(BENCH_SIZE)]
    tactical: list[tuple[str, str, float]] = []
    for index in range(MAX_TACTICAL_SUBS):
        starter = str(form.get(f"tac_starter_{index}", ""))
        sub = str(form.get(f"tac_sub_{index}", ""))
        threshold = str(form.get(f"tac_threshold_{index}", ""))
        if starter and sub and threshold:
            tactical.append((starter, sub, float(threshold)))

    target = f"/ui/leagues/{league.id}/team?game_week={game_week}"
    try:
        save_lineup(
            session, participant, game_week,
            formation=formation, starters=starters, bench=bench,
            tactical_subs=tactical, live_mode=league.live_mode,
        )
    except LineupError as error:
        return redirect(f"{target}&error={quote(str(error))}")
    return redirect(f"{target}&message={quote('Composition enregistrée.')}")


@router.post("/leagues/{league_id}/team/bonuses/{game_week}")
def pose_bonus_screen(
    session: SessionDep,
    league: UiLeague,
    participant: UiParticipant,
    game_week: int,
    bonus_type: FormStr,
    target_player_id: Annotated[str, Form()] = "",
):
    target = f"/ui/leagues/{league.id}/team?game_week={game_week}"
    try:
        pose_bonus(session, participant, game_week, bonus_type, target_player_id or None)
    except BonusError as error:
        return redirect(f"{target}&error={quote(str(error))}")
    return redirect(f"{target}&message={quote('Bonus posé.')}")
