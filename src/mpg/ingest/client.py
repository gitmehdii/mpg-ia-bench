"""Client for the public MPG API (spec 1.1, 1.2).

Only the unauthenticated base is used, and every call carries an identifiable
User-Agent at a polite cadence (spec 8).
"""

from __future__ import annotations

from typing import Any

import httpx

from mpg.config import get_settings

#: Seasons the pool endpoint serves (spec 1.2).
AVAILABLE_SEASONS = range(2020, 2027)


class MpgClient:
    """Thin read-only client. Everything it returns is raw decoded JSON."""

    def __init__(self, base_url: str | None = None, timeout: float = 30.0) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.api_base).rstrip("/")
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
            follow_redirects=True,
        )

    def __enter__(self) -> MpgClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, **params: Any) -> dict:
        response = self._client.get(f"{self.base_url}/{path.lstrip('/')}", params=params or None)
        response.raise_for_status()
        return response.json()

    # ---------------------------------------------------------------- endpoints

    def active_championships(self) -> dict:
        return self._get("championships/active")

    def clubs(self) -> dict:
        """630 KB and rarely changes: cache it rather than refetching (spec 1.6)."""
        return self._get("championship-clubs")

    def players_pool(self, championship_id: int, season: int | None = None) -> dict:
        if season is None:
            return self._get(f"championship-players-pool/{championship_id}")
        return self._get(f"championship-players-pool/{championship_id}", season=season)

    def player_stats_summary(self, player_id: str) -> dict:
        return self._get(f"championship-player-stats-summary/{player_id}")
