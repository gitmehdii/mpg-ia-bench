"""A self-contained HTML report of a benchmark run.

One page, no server and no assets: the pooled table, what each agent bought at the
mercato, and every fixture with the two teams facing each other line by line. It is
the view that makes a run arguable rather than just scored.
"""

from __future__ import annotations

import html
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mpg import presentation as fr
from mpg.bench.aggregate import MultiRunResult
from mpg.db.models import BonusUsage, League, LeagueMatch, Participant, Player, Roster
from mpg.engine.lines import Line, line_of
from mpg.engine.models import FinalPlayer
from mpg.match_recap import team_average
from mpg.report_view import rehydrate

LINES = ((Line.G, "Gardien"), (Line.D, "Défense"), (Line.M, "Milieu"), (Line.A, "Attaque"))

CSS = (Path(__file__).resolve().parents[1] / "web" / "static" / "app.css").read_text()

EXTRA_CSS = """
.bench-row { display:grid; grid-template-columns:1fr auto 1fr; gap:10px; align-items:start; }
@media (max-width:760px){ .bench-row{ grid-template-columns:1fr; } .bench-row .mid{ order:-1; } }
.bench-row .mid { text-align:center; font-variant-numeric:tabular-nums; color:var(--ink-faint);
  font-size:12px; min-width:5.5em; padding-top:2px; }
.side.right { text-align:right; }
.side .p { display:flex; gap:6px; align-items:baseline; padding:3px 0;
  border-bottom:1px solid var(--line); }
.side.right .p { flex-direction:row-reverse; }
.side .p .n { flex:1 1 auto; min-width:0; overflow-wrap:anywhere; }
.side .p .r { font-variant-numeric:tabular-nums; font-weight:700; min-width:2.4em; }
.side .p.ghost { opacity:.7; font-style:italic; }
.mk { font-size:10px; font-weight:700; padding:1px 4px; border-radius:4px;
  border:1px solid transparent; white-space:nowrap; }
.mk.b { background:var(--accent-soft); color:var(--accent); }
.mk.v { background:var(--accent-soft); color:var(--accent); border-color:var(--accent); }
.mk.c { background:var(--warn-soft); color:var(--warn); }
.mk.s { background:var(--panel-2); color:var(--ink-soft); }
.mk.r { background:var(--danger-soft); color:var(--danger); }
.fx { display:flex; justify-content:space-between; align-items:baseline; gap:10px;
  flex-wrap:wrap; margin-bottom:6px; }
.fx .sc { font-size:26px; font-weight:800; font-variant-numeric:tabular-nums; }
.buys { column-count:2; column-gap:20px; }
@media (max-width:640px){ .buys{ column-count:1; } }
.buys div { break-inside:avoid; padding:2px 0; font-size:13.5px; }
"""


def _e(value: object) -> str:
    return html.escape(str(value))


def _markers(slot: FinalPlayer, captain: str | None) -> str:
    out = []
    if slot.real_goals:
        out.append(f'<span class="mk b">{"⚽" * slot.real_goals}</span>')
    if slot.mpg_goal:
        out.append('<span class="mk v">MPG</span>')
    if slot.own_goals:
        out.append('<span class="mk r">csc</span>')
    if slot.is_rotaldo:
        out.append('<span class="mk r">Rotaldo</span>')
    if slot.replacement_kind:
        label = {"tactical": "tactique", "mandatory": "entré", "live": "live"}[
            slot.replacement_kind
        ]
        out.append(f'<span class="mk s">{label}</span>')
    if captain and slot.player_id == captain:
        out.append('<span class="mk c">C</span>')
    return "".join(out)


def _player(slot: FinalPlayer, captain: str | None, *, right: bool) -> str:
    name = "Rotaldo" if slot.is_rotaldo else slot.name
    ghost = " ghost" if slot.is_rotaldo else ""
    title = _e(", ".join(fr.adjustments(slot))) if slot.adjustments else ""
    return (
        f'<div class="p{ghost}" title="{title}">'
        f'<span class="r">{fr.rating(slot.rating)}</span>'
        f'<span class="n">{_e(name)} {_markers(slot, captain)}</span>'
        f"</div>"
    )


def _fixture(session: Session, fixture: LeagueMatch, names: dict[int, str]) -> str:
    result = rehydrate(fixture.report)

    def bonuses(participant_id: int) -> tuple[list[str], str | None]:
        rows = session.execute(
            select(BonusUsage).where(
                BonusUsage.participant_id == participant_id,
                BonusUsage.game_week_number == fixture.game_week_number,
            )
        ).scalars().all()
        captain = next((r.target_player_id for r in rows if r.bonus_type == "captain"), None)
        return [fr.bonus_name(r.bonus_type) for r in rows], captain

    home_bonuses, home_captain = bonuses(fixture.home_participant_id)
    away_bonuses, away_captain = bonuses(fixture.away_participant_id)
    home_name = names.get(fixture.home_participant_id, "?")
    away_name = names.get(fixture.away_participant_id, "?")

    parts = [
        '<div class="card">',
        '<div class="fx">',
        f"<strong>{_e(home_name)}</strong>",
        f'<span class="sc">{result.home.score} – {result.away.score}</span>',
        f"<strong>{_e(away_name)}</strong>",
        "</div>",
        '<p class="small muted">'
        f"moyenne d’équipe {fr.average(team_average(result.home))} "
        f"contre {fr.average(team_average(result.away))}"
        + (f" · bonus {_e(', '.join(home_bonuses))}" if home_bonuses else "")
        + (f" / {_e(', '.join(away_bonuses))}" if away_bonuses else "")
        + "</p>",
    ]

    for line, label in LINES:
        home_slots = [s for s in result.home.final_xi if s.slot_line is line]
        away_slots = [s for s in result.away.final_xi if s.slot_line is line]
        if not home_slots and not away_slots:
            continue
        parts.append(f"<h3>{label}</h3>")
        parts.append('<div class="bench-row">')
        parts.append('<div class="side">')
        parts.extend(_player(slot, home_captain, right=False) for slot in home_slots)
        parts.append("</div>")
        parts.append(
            f'<div class="mid">{fr.average(result.home.line_averages.get(line))}'
            f"<br>│<br>{fr.average(result.away.line_averages.get(line))}</div>"
        )
        parts.append('<div class="side right">')
        parts.extend(_player(slot, away_captain, right=True) for slot in away_slots)
        parts.append("</div></div>")

    for team, name in ((result.home, home_name), (result.away, away_name)):
        scorers = [
            f"{s.name}{' (MPG)' if s.mpg_goal else ''}"
            for s in team.final_xi if s.real_goals or s.mpg_goal
        ]
        parts.append(
            f'<p class="small"><strong>{_e(name)} {team.score}</strong> — '
            f"{_e(' · '.join(fr.score_breakdown(team)))}"
            + (f"<br>buteurs : {_e(', '.join(scorers))}" if scorers else "")
            + "</p>"
        )
    parts.append("</div>")
    return "".join(parts)


def _mercato(session: Session, league: League, names: dict[int, str]) -> str:
    parts = ['<div class="card"><h2>Le mercato</h2>',
             '<p class="sub">Ce que chaque agent a acheté, et à quel prix.</p>']
    for participant in session.execute(
        select(Participant).where(Participant.league_id == league.id).order_by(Participant.id)
    ).scalars():
        rows = session.execute(
            select(Roster, Player)
            .join(Player, Player.id == Roster.player_id)
            .where(Roster.participant_id == participant.id)
            .order_by(Roster.bought_price.desc())
        ).all()
        bought = [(r, p) for r, p in rows if not r.from_draft]
        drafted = [(r, p) for r, p in rows if r.from_draft]
        spent = sum(r.bought_price for r, _ in rows)
        by_line: dict[Line, int] = {}
        for _r, player in rows:
            line = line_of(player.ultra_position)
            by_line[line] = by_line.get(line, 0) + 1

        parts.append(
            f"<h3>{_e(names.get(participant.id, '?'))}</h3>"
            f'<p class="small muted">{spent} M€ dépensés, {participant.budget} M€ restants · '
            + " · ".join(
                f"{count} {fr.LINE_LABEL[line]}"
                for line, count in sorted(by_line.items())
            )
            + f" · {len(drafted)} joueur(s) attribué(s) d’office au repêchage</p>"
        )
        parts.append('<div class="buys">')
        for roster, player in bought:
            parts.append(
                f"<div>{fr.LINE_INITIAL[line_of(player.ultra_position)]} "
                f"{_e(player.display_name)} — <strong>{roster.bought_price} M€</strong> "
                f'<span class="muted">(cote {player.quotation or 0})</span></div>'
            )
        for roster, player in drafted:
            parts.append(
                f'<div class="muted">{fr.LINE_INITIAL[line_of(player.ultra_position)]} '
                f"{_e(player.display_name)} — repêchage, {roster.bought_price} M€</div>"
            )
        parts.append("</div>")
    parts.append("</div>")
    return "".join(parts)


def render_html(session: Session, result: MultiRunResult, *, title: str = "Benchmark") -> str:
    """The whole thing: the pooled table, then each run's mercato and fixtures."""
    runs = result.aggregates[0].runs if result.aggregates else 0
    body = [
        f"<h1>{_e(title)}</h1>",
        f'<p class="sub">{runs} run(s) · journées '
        f"{_e(', '.join(map(str, result.game_weeks)))}</p>",
        '<div class="card"><h2>Classement agrégé</h2><div class="scroll"><table>',
        "<thead><tr><th class='num'>#</th><th>agent</th><th class='num'>pts moy.</th>"
        "<th class='num'>écart</th><th class='num'>1ers</th><th class='num'>diff</th>"
        "<th class='num'>MPG</th><th class='num'>budget</th><th class='num'>fiab.</th>"
        "<th class='num'>temps/run</th></tr></thead><tbody>",
    ]
    for rank, entry in enumerate(result.aggregates, start=1):
        spread = fr.number(entry.points_spread, 1) if entry.runs > 1 else "—"
        body.append(
            f"<tr><td class='num'>{rank}</td><td class='wide'>{_e(entry.name)}</td>"
            f"<td class='num'>{fr.number(entry.mean_points, 2)}</td>"
            f"<td class='num muted'>{spread}</td>"
            f"<td class='num'>{entry.wins}</td>"
            f"<td class='num'>{fr.number(entry.mean_goal_difference, 1)}</td>"
            f"<td class='num muted'>"
            f"{fr.number(sum(entry.mpg_goals) / max(1, entry.runs), 1)}</td>"
            f"<td class='num muted'>{int(sum(entry.budget_spent) / max(1, entry.runs))} M</td>"
            f"<td class='num'>{entry.reliability * 100:.0f}%</td>"
            f"<td class='num muted'>{fr.number(entry.mean_seconds, 0)}s</td></tr>"
        )
    body.append("</tbody></table></div>")
    if runs > 1:
        body.append(
            '<p class="small muted">« écart » est l’écart-type des points entre runs. '
            "S’il approche l’écart entre deux agents, le classement ne les sépare pas.</p>"
        )
    body.append("</div>")

    for index, run in enumerate(result.runs, start=1):
        league = session.get(League, run.league_id)
        names = {
            row.id: row.team_name
            for row in session.execute(
                select(Participant).where(Participant.league_id == league.id)
            ).scalars()
        }
        body.append(f"<h2>Run {index} — {_e(league.name)}</h2>")
        body.append(_mercato(session, league, names))
        for game_week in run.game_weeks:
            fixtures = session.execute(
                select(LeagueMatch).where(
                    LeagueMatch.league_id == league.id,
                    LeagueMatch.game_week_number == game_week,
                    LeagueMatch.report.is_not(None),
                ).order_by(LeagueMatch.id)
            ).scalars().all()
            if not fixtures:
                continue
            body.append(f"<h3>Journée {game_week}</h3>")
            body.extend(_fixture(session, fixture, names) for fixture in fixtures)

    return (
        "<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{_e(title)}</title><style>{CSS}{EXTRA_CSS}</style></head>"
        f"<body><main class='wrap'>{''.join(body)}</main></body></html>"
    )
