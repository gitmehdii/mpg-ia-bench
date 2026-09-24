"""Running a benchmark several times and pooling the results.

One run of one league is a single observation: who finished first says very little,
because a fixture list of three game weeks turns on a handful of ratings. Several runs
with the seating shuffled and the sampling varied give a mean worth reading, and a
spread that says how much of the table was luck.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from mpg.bench.agents import Agent
from mpg.bench.runner import BenchResult, run_bench
from mpg.config import MERCATO_BASE_ROUNDS


@dataclass(slots=True)
class Aggregate:
    """One agent's record across every run."""

    name: str
    points: list[int] = field(default_factory=list)
    ranks: list[int] = field(default_factory=list)
    goals_for: list[int] = field(default_factory=list)
    goals_against: list[int] = field(default_factory=list)
    real_goals: list[int] = field(default_factory=list)
    mpg_goals: list[int] = field(default_factory=list)
    budget_spent: list[int] = field(default_factory=list)
    calls: int = 0
    failures: int = 0
    seconds: float = 0.0
    tokens: int = 0
    incomplete_squads: int = 0

    @property
    def runs(self) -> int:
        return len(self.points)

    @property
    def mean_points(self) -> float:
        return statistics.fmean(self.points) if self.points else 0.0

    @property
    def points_spread(self) -> float:
        """Standard deviation of the points. A large one means the table was noise."""
        return statistics.stdev(self.points) if len(self.points) > 1 else 0.0

    @property
    def mean_rank(self) -> float:
        return statistics.fmean(self.ranks) if self.ranks else 0.0

    @property
    def mean_goal_difference(self) -> float:
        if not self.goals_for:
            return 0.0
        return statistics.fmean(
            [f - a for f, a in zip(self.goals_for, self.goals_against, strict=True)]
        )

    @property
    def wins(self) -> int:
        return sum(1 for rank in self.ranks if rank == 1)

    @property
    def reliability(self) -> float:
        return 1.0 - (self.failures / self.calls) if self.calls else 1.0

    @property
    def mean_seconds(self) -> float:
        return self.seconds / self.runs if self.runs else 0.0


@dataclass(slots=True)
class MultiRunResult:
    aggregates: list[Aggregate]
    runs: list[BenchResult] = field(default_factory=list)
    game_weeks: list[int] = field(default_factory=list)


def collect(results: list[BenchResult]) -> list[Aggregate]:
    """Pool a list of runs by agent name."""
    pooled: dict[str, Aggregate] = {}
    for result in results:
        for report in result.agents:
            entry = pooled.setdefault(report.name, Aggregate(name=report.name))
            entry.points.append(report.points)
            entry.ranks.append(report.rank or len(result.agents))
            entry.goals_for.append(report.goals_for)
            entry.goals_against.append(report.goals_against)
            entry.real_goals.append(report.real_goals)
            entry.mpg_goals.append(report.mpg_goals)
            entry.budget_spent.append(report.budget_spent)
            entry.calls += len(report.calls)
            entry.failures += report.failures
            entry.seconds += report.seconds
            entry.tokens += report.tokens
            if not report.squad_complete:
                entry.incomplete_squads += 1
    return sorted(
        pooled.values(), key=lambda a: (-a.mean_points, a.mean_rank, a.name)
    )


def run_many(
    make_session: Callable[[], Session] | None,
    make_agents: Callable[[int], list[Agent]],
    *,
    game_weeks: list[int],
    runs: int = 3,
    session: Session | None = None,
    championship_id: int = 1,
    mercato_rounds: int = MERCATO_BASE_ROUNDS,
    name: str = "Benchmark",
    restrict_pool: bool = True,
    on_run: Callable[[int, BenchResult], None] | None = None,
) -> MultiRunResult:
    """Play the same benchmark `runs` times.

    `make_agents` is called per run so a client can vary its sampling seed: with a
    temperature of zero and an identical prompt a model answers identically, and runs
    that cannot differ are not replicates. The seating is shuffled too, which changes
    the fixture list and who receives -- and the home side wins tied duels.
    """

    @contextmanager
    def scoped() -> Iterator[Session]:
        if session is not None:
            yield session
            return
        if make_session is None:
            raise ValueError("either a session or a session factory is required")
        opened = make_session()
        try:
            yield opened
            opened.commit()
        finally:
            opened.close()

    results: list[BenchResult] = []
    for index in range(runs):
        agents = make_agents(index)
        # Rotating the seating changes the pairings and home advantage between runs.
        rotation = index % max(1, len(agents))
        agents = agents[rotation:] + agents[:rotation]
        with scoped() as active:
            result = run_bench(
                active, agents, game_weeks=game_weeks,
                championship_id=championship_id, mercato_rounds=mercato_rounds,
                name=f"{name} #{index + 1}", restrict_pool=restrict_pool,
            )
            active.flush()
        results.append(result)
        if on_run is not None:
            on_run(index, result)

    return MultiRunResult(
        aggregates=collect(results), runs=results, game_weeks=game_weeks
    )
