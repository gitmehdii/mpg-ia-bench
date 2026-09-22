"""FastAPI application. The API exposes everything; `/docs` is the v1 interface."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from mpg.api.routers import auth, leagues, matches, mercato, teams
from mpg.web import routes as ui_routes
from mpg.web.deps import LoginRequired
from mpg.web.templating import STATIC_DIR

app = FastAPI(
    title="mpg-ia-bench",
    version="0.1.0",
    description=(
        "A fantasy football engine reproducing the MPG mechanics: closed-bid mercato, "
        "weekly lineups, bonuses, and game week resolution with real and virtual goals."
    ),
)

app.include_router(auth.router)
app.include_router(leagues.router)
app.include_router(mercato.router)
app.include_router(teams.router)
app.include_router(matches.router)
app.include_router(ui_routes.router)

app.mount("/ui/static", StaticFiles(directory=str(STATIC_DIR)), name="ui_static")


@app.exception_handler(LoginRequired)
def _login_required(request: Request, exc: LoginRequired) -> RedirectResponse:
    """A browser gets sent to the login page; the JSON API still answers 401."""
    return RedirectResponse("/ui/login", status_code=303)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/ui/")


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}
