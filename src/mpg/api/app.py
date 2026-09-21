"""FastAPI application. The API exposes everything; `/docs` is the v1 interface."""

from __future__ import annotations

from fastapi import FastAPI

from mpg.api.routers import auth, leagues, matches, mercato, teams

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


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}
