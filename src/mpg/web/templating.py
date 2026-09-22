"""Jinja set-up. Rendered server-side, no build step and no front-end framework."""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from mpg import presentation as fr
from mpg.engine.lines import Line

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

#: Lines in the order a team sheet reads, with their French headings.
LINE_LABELS: list[tuple[Line, str]] = [
    (Line.G, "Gardien"),
    (Line.D, "Défense"),
    (Line.M, "Milieu"),
    (Line.A, "Attaque"),
]

# Everything a template needs to word things is reached through `fr`, so the web
# screens and the terminal report say the same things.
templates.env.globals.update(
    fr=fr,
    line_labels=LINE_LABELS,
    duel_verdict=fr.duel_verdict,
    status_label=fr.status_label,
)


def render(request: Request, template: str, /, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, template, context)
