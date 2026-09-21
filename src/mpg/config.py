"""Application settings and the game's tunable constants."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

#: Money is handled in millions of euros throughout, which is the unit the MPG
#: quotations already use (an integer from 1 to 40). One unit is 1,000,000 euros.
EUROS_PER_UNIT = 1_000_000

#: Spec 3.2: the mercato budget, 500,000,000 euros.
MERCATO_BUDGET = 500

#: Spec 3.2 / 6.7: minimum squad, as goalkeepers, defenders, midfielders, forwards.
MERCATO_POSITION_QUOTA: dict[str, int] = {"G": 2, "D": 6, "M": 6, "A": 4}

MIN_SQUAD_SIZE = sum(MERCATO_POSITION_QUOTA.values())

#: Spec 3.2: 48 h for the first round, then 24 h per round.
FIRST_ROUND_HOURS = 48
NEXT_ROUND_HOURS = 24
MERCATO_MAX_DAYS = 7
MERCATO_BASE_ROUNDS = 5

#: Spec 3.5: per-season bonus quotas by league size.
BONUS_QUOTAS: dict[str, dict[int, int]] = {
    "valise":        {2: 0, 4: 1, 6: 1, 8: 1, 10: 1},
    "mcdo":          {2: 0, 4: 1, 6: 2, 8: 3, 10: 3},
    "suarez":        {2: 0, 4: 0, 6: 1, 8: 1, 10: 2},
    "zahia":         {2: 0, 4: 0, 6: 1, 8: 1, 10: 1},
    "mirror":        {2: 0, 4: 0, 6: 1, 8: 1, 10: 1},
    "formation_424": {2: 0, 4: 0, 6: 1, 8: 1, 10: 1},
    "cheat_code":    {2: 0, 4: 0, 6: 0, 8: 1, 10: 1},
    "tonton_pat":    {2: 0, 4: 0, 6: 0, 8: 1, 10: 1},
}

#: Spec 3.5: these two are unlimited and do not count against the one-per-game-week cap.
UNLIMITED_BONUSES = frozenset({"defense", "captain"})

LEAGUE_SIZES = (2, 4, 6, 8, 10)

#: Spec 3.7: points for a win, a draw and a loss.
POINTS_WIN, POINTS_DRAW, POINTS_LOSS = 3, 1, 0

CHAMPIONSHIPS: dict[int, str] = {
    1: "fr_l1",
    2: "uk_pl",
    3: "es_l1",
    4: "fr_l2",
    5: "it_sa",
    6: "eu_cl",
    7: "ag_ls",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MPG_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://mpg:mpg@localhost:5432/mpg"
    api_base: str = "https://api.mpg.football/api/data"
    #: Spec 8: the public API must be called with an identifiable User-Agent.
    user_agent: str = "mpg-ia-bench/0.1 (portfolio project; contact via repo)"
    default_championship: int = 1
    secret_key: str = "change-me-in-production"

    #: Spec 6.6: which random-bid algorithm to use for participants who never validated.
    random_bid_strategy: Literal["deficit_weighted"] = "deficit_weighted"
    #: Spec 3.2: close the mercato automatically once the 7 days are up.
    mercato_auto_close: bool = True
    #: Spec 3.8: recompute a resolved game week when a rating changes retroactively.
    postponed_recompute: bool = True

    #: Polite ingestion cadence (spec 1.6): seconds between two pool refreshes.
    ingest_interval_seconds: int = 3600
    ingest_matchday_interval_seconds: int = 900


@lru_cache
def get_settings() -> Settings:
    return Settings()
