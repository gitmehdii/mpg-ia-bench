"""Input and output types of the resolution engine.

Everything here is a plain dataclass: the engine takes objects in and hands a
result back, with no database session and no framework anywhere near it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from mpg.engine.lines import Line, line_of

# --------------------------------------------------------------------------- input


@dataclass(slots=True)
class PlayerEntry:
    """A squad player as submitted for one game week."""

    player_id: str
    ultra_position: int
    name: str = ""

    #: Did the player actually take the field in real life? Drives spec 3.4.
    played: bool = False
    #: Official MPG rating, 1 to 10 by steps of 0.5. None when the player did not play.
    rating: float | None = None
    #: Real goals credited to the player's own team.
    real_goals: int = 0
    #: Own goals, credited to the opposing team as real goals.
    own_goals: int = 0
    #: Kick-off already passed: the player can no longer come on in live mode (spec 3.4).
    match_started: bool = False
    #: Match brought forward or postponed: rating forced to 5, goals dropped (spec 3.8).
    neutralised: bool = False

    @property
    def line(self) -> Line:
        return line_of(self.ultra_position)

    def effective_rating(self) -> float | None:
        """Rating before any bonus, after the advanced/postponed match rule (spec 3.8)."""
        if self.neutralised:
            return 5.0
        return self.rating

    def effective_real_goals(self) -> int:
        return 0 if self.neutralised else self.real_goals

    def effective_own_goals(self) -> int:
        return 0 if self.neutralised else self.own_goals

    def has_effectively_played(self) -> bool:
        """A neutralised player is deemed present but cannot come on during the weekend."""
        if self.neutralised:
            return True
        return self.played and self.rating is not None


@dataclass(slots=True)
class TacticalSub:
    """"if starter X is rated strictly below `threshold`, substitute Y comes on"."""

    starter_id: str
    sub_id: str
    threshold: float


@dataclass(slots=True)
class LiveSub:
    """A change made during the weekend in live mode (spec 3.4)."""

    starter_id: str
    sub_id: str
    #: Order in which the manager made the change; Tonton Pat' keeps only the first two.
    order: int = 0


@dataclass(slots=True)
class Bonuses:
    """The bonuses a team posed for one game week (spec 3.5).

    The engine does not police the per-game-week or per-season quotas: that is the
    service layer's job (spec 3.5). The engine applies whatever it is handed.
    """

    defense: bool = False
    zahia: bool = False
    mcdo_target: str | None = None
    captain_target: str | None = None
    valise: bool = False
    suarez: bool = False
    mirror: bool = False
    cheat_code: bool = False
    tonton_pat: bool = False
    formation_424: bool = False

    #: Bonuses that count against the one-per-game-week limit.
    LIMITED = ("mcdo_target", "valise", "suarez", "zahia", "mirror", "cheat_code",
               "tonton_pat", "formation_424")

    def limited_posed(self) -> list[str]:
        """Names of the limited bonuses actually posed."""
        return [name for name in self.LIMITED if getattr(self, name)]


@dataclass(slots=True)
class TeamSheet:
    """One participant's submission for one game week."""

    participant_id: str
    starters: list[PlayerEntry]
    bench: list[PlayerEntry] = field(default_factory=list)
    formation: str | None = None
    bonuses: Bonuses = field(default_factory=Bonuses)
    tactical_subs: list[TacticalSub] = field(default_factory=list)
    live_mode: bool = False
    live_subs: list[LiveSub] = field(default_factory=list)

    def all_players(self) -> list[PlayerEntry]:
        return [*self.starters, *self.bench]


@dataclass(frozen=True, slots=True)
class MatchContext:
    """Everything about the fixture itself that changes the resolution."""

    game_week: int = 1
    #: The league plays return legs. Drives the tie-break of golden rule 1.
    return_legs: bool = True
    #: Play-off fixture: the attacker wins ties, like a league without return legs.
    playoff: bool = False

    @property
    def attacker_wins_ties(self) -> bool:
        """Golden rule 1's exception: no return legs, or a play-off."""
        return (not self.return_legs) or self.playoff


# --------------------------------------------------------------------------- output


@dataclass(slots=True)
class Duel:
    """One crossing of an opposing line, kept so a goal can be explained (spec 3.6).

    Purely a record of what the comparison already decided: adding it changes no
    value and no comparison.
    """

    line: Line
    #: Mean rating of the opposing line, None when that line is empty.
    opponent: float | None
    rating_before: float
    outcome: Literal["won", "won_on_tie", "lost_on_tie", "lost", "free"]
    #: Decrement applied after winning this duel: 1 point for the first, then 0.5.
    cost: float
    rating_after: float

    @property
    def passed(self) -> bool:
        return self.outcome in ("won", "won_on_tie", "free")


#: Why a player never ran the gauntlet at all (golden rules 2, 3 and 5).
SkipReason = Literal["goalkeeper", "already_scored", "below_floor"]


@dataclass(slots=True)
class FinalPlayer:
    """A slot of the final XI, once every rule has been applied."""

    slot_line: Line
    player_id: str
    name: str
    rating: float
    real_goals: int = 0
    own_goals: int = 0
    is_rotaldo: bool = False
    mpg_goal: bool = False
    #: Player this one came on for, and how.
    replaced: str | None = None
    replacement_kind: Literal["tactical", "mandatory", "live", None] = None
    #: Penalty applied for filling a slot of another line (spec 3.4).
    line_penalty: float = 0.0
    #: Bonuses and malus that touched this slot, for the match report.
    adjustments: list[str] = field(default_factory=list)
    #: The gauntlet this player ran, duel by duel. Empty when he never ran it.
    duels: list[Duel] = field(default_factory=list)
    #: Set when the player was not eligible to run it in the first place.
    mpg_skip_reason: SkipReason | None = None


@dataclass(slots=True)
class GoalEvent:
    """A goal in the final tally, kept so the report can explain the score."""

    kind: Literal["real", "mpg", "own_goal", "rotaldo_own_goal"]
    player_id: str | None
    name: str
    line: Line | None
    cancelled_by: Literal["save", "valise", None] = None


@dataclass(slots=True)
class TeamResult:
    """One side's outcome for the fixture."""

    participant_id: str
    score: int = 0
    real_goals: int = 0
    mpg_goals: int = 0
    #: Real goals cancelled by the opposing goalkeeper's MPG save.
    saved: int = 0
    #: Goals cancelled by the opponent's Valise.
    valise_cancelled: int = 0
    #: Own goals credited to this team because the opponent fielded phantom players.
    rotaldo_own_goals_for: int = 0
    rotaldo_count: int = 0
    final_xi: list[FinalPlayer] = field(default_factory=list)
    goals: list[GoalEvent] = field(default_factory=list)
    line_averages: dict[Line, float | None] = field(default_factory=dict)
    #: Compensation owed to this team because the opponent posed a Valise (spec 3.5).
    valise_compensation: int = 0
    log: list[str] = field(default_factory=list)

    def rating_of(self, player_id: str) -> float | None:
        for slot in self.final_xi:
            if slot.player_id == player_id:
                return slot.rating
        return None


@dataclass(slots=True)
class MatchResult:
    """The resolution of one league fixture."""

    home: TeamResult
    away: TeamResult
    context: MatchContext

    @property
    def scoreline(self) -> tuple[int, int]:
        return self.home.score, self.away.score
