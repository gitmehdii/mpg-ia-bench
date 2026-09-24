"""Command line entry points: ingestion, mercato and game week resolution.

    python -m mpg.cli ingest [--championship 1] [--seasons 2024,2025,2026]
    python -m mpg.cli resolve-round <round_id>
    python -m mpg.cli resolve-week <league_id> <game_week> [--summary]
    python -m mpg.cli replay <fixture_id> [--summary]
    python -m mpg.cli standings <league_id>
    python -m mpg.cli lineups <league_id> <game_week>
    python -m mpg.cli recap <league_id> <game_week>
    python -m mpg.cli demo
    python -m mpg.cli bench --models llama3.1:8b,qwen2.5:14b --game-weeks 5
    python -m mpg.cli scenarios
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mpg.config import get_settings
from mpg.db.models import League, LeagueMatch
from mpg.db.session import get_session_factory
from mpg.ingest.jobs import ingest_history, refresh_all
from mpg.mercato.service import resolve_round
from mpg.report_text import render_match_report
from mpg.services.matchday import resolve_game_week, resolve_league_match
from mpg.services.standings import as_table, standings


def cmd_ingest(args: argparse.Namespace) -> int:
    session = get_session_factory()()
    try:
        if args.seasons:
            from mpg.ingest.client import MpgClient

            seasons = [int(s) for s in args.seasons.split(",")]
            with MpgClient() as client:
                reports = ingest_history(session, client, args.championship, seasons)
            for season, report in zip(seasons, reports, strict=True):
                print(f"  {season}: {report.players} players, {report.performances} ratings")
        else:
            report = refresh_all(session, args.championship)
            print(
                f"  {report.players} players, {report.new_players} new, "
                f"{report.quotations} quotations, {report.performances} ratings, "
                f"{report.revised_performances} revised, "
                f"{report.flagged_fixtures} fixtures flagged for recompute"
            )
            for note in report.notes:
                print(f"  note: {note}")
        session.commit()
        return 0
    finally:
        session.close()


def cmd_resolve_round(args: argparse.Namespace) -> int:
    session = get_session_factory()()
    try:
        changed = resolve_round(session, args.round_id)
        session.commit()
        print("resolved" if changed else "nothing to do: already resolved")
        return 0
    finally:
        session.close()


def cmd_resolve_week(args: argparse.Namespace) -> int:
    session = get_session_factory()()
    try:
        league = session.get(League, args.league_id)
        if league is None:
            print("no such league", file=sys.stderr)
            return 1
        results = resolve_game_week(session, league, args.game_week)
        session.commit()
        for result in results:
            if args.summary:
                print(
                    f"  {result.home.participant_id} {result.home.score} - "
                    f"{result.away.score} {result.away.participant_id}"
                )
            else:
                print(render_match_report(result))
        return 0
    finally:
        session.close()


def cmd_replay(args: argparse.Namespace) -> int:
    """Recompute a fixture the ingestion flagged after a retroactive rating change."""
    session = get_session_factory()()
    try:
        fixture = session.get(LeagueMatch, args.fixture_id)
        if fixture is None:
            print("no such fixture", file=sys.stderr)
            return 1
        before = (fixture.home_score, fixture.away_score)
        result = resolve_league_match(session, fixture, force=True)
        session.commit()
        print(f"  {before[0]}-{before[1]} becomes {result.home.score}-{result.away.score}")
        if not args.summary:
            print()
            print(render_match_report(result))
        return 0
    finally:
        session.close()


def cmd_standings(args: argparse.Namespace) -> int:
    session = get_session_factory()()
    try:
        league = session.get(League, args.league_id)
        if league is None:
            print("no such league", file=sys.stderr)
            return 1
        print(f"{'#':>2}  {'team':24} {'P':>3} {'W':>3} {'D':>3} {'L':>3} "
              f"{'GF':>4} {'GA':>4} {'GD':>4} {'Pts':>4}")
        for row in as_table(standings(session, league)):
            print(
                f"{row['rank']:>2}  {row['team_name']:24} {row['played']:>3} {row['won']:>3} "
                f"{row['drawn']:>3} {row['lost']:>3} {row['goals_for']:>4} "
                f"{row['goals_against']:>4} {row['goal_difference']:>4} {row['points']:>4}"
            )
        return 0
    finally:
        session.close()


def cmd_recap(args: argparse.Namespace) -> int:
    """Print the side-by-side recap of a game week."""
    from mpg.services.recap import recap_game_week

    session = get_session_factory()()
    try:
        if session.get(League, args.league_id) is None:
            print("no such league", file=sys.stderr)
            return 1
        print(recap_game_week(session, args.league_id, args.game_week))
        return 0
    finally:
        session.close()


def cmd_lineups(args: argparse.Namespace) -> int:
    """Print every manager's team for a game week, next to what the players did."""
    from mpg.bench.lineups_text import render_lineups

    session = get_session_factory()()
    try:
        league = session.get(League, args.league_id)
        if league is None:
            print("no such league", file=sys.stderr)
            return 1
        print(render_lineups(session, args.league_id, args.game_week))
        return 0
    finally:
        session.close()


def cmd_bench(args: argparse.Namespace) -> int:
    """Play a league with model-driven agents and print how they did."""
    from sqlalchemy import create_engine

    from mpg.bench.agents import HeuristicAgent, LlmAgent
    from mpg.bench.ollama import OllamaClient, available_models
    from mpg.bench.report import render
    from mpg.bench.runner import run_bench
    from mpg.db.models import Base

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if models:
        known = available_models(args.host)
        if not known:
            print(f"aucun serveur Ollama sur {args.host}", file=sys.stderr)
            return 1
        missing = [m for m in models if m not in known]
        if missing:
            print(f"modèles absents : {', '.join(missing)}", file=sys.stderr)
            print(f"disponibles : {', '.join(known)}", file=sys.stderr)
            return 1

    def make_agents(run_index: int) -> list:
        """A fresh set per run, each client sampling with its own seed.

        At temperature zero and an identical prompt a model answers identically, so
        runs that cannot differ are not replicates.
        """
        built = [
            LlmAgent(
                name=model,
                complete=OllamaClient(
                    model, host=args.host, timeout=args.timeout, think=args.think,
                    temperature=args.temperature, seed=42 + run_index,
                ),
            )
            for model in models
        ]
        if args.baseline or len(built) % 2 or not built:
            built.append(HeuristicAgent(name="heuristique"))
        # A league needs an even number of participants, and with no model at all the
        # run is still worth having: it exercises the whole pipeline with no server.
        if len(built) % 2:
            built.append(HeuristicAgent(name="heuristique-2", premium=0.30))
        return built

    agents = make_agents(0)

    engine = create_engine(get_settings().database_url, future=True)
    Base.metadata.create_all(engine)
    session = get_session_factory()()
    try:
        weeks = [int(w) for w in args.game_weeks.split(",") if w.strip()]
        if args.runs > 1:
            from mpg.bench.aggregate import run_many
            from mpg.bench.report import render_aggregate

            def announce(index: int, run: object) -> None:
                print(f"  run {index + 1}/{args.runs} terminé", flush=True)

            multi = run_many(
                None, make_agents, game_weeks=weeks, runs=args.runs,
                session=session, mercato_rounds=args.rounds, name=args.name,
                on_run=announce, restrict_pool=not args.whole_pool,
            )
            session.commit()
            print(render_aggregate(multi))
            for run in multi.runs:
                print(render(run))
            result = multi.runs[-1]
        else:
            from mpg.bench.aggregate import MultiRunResult, collect

            result = run_bench(
                session, agents, game_weeks=weeks, mercato_rounds=args.rounds,
                name=args.name, restrict_pool=not args.whole_pool,
            )
            session.commit()
            print(render(result))
            multi = MultiRunResult(
                aggregates=collect([result]), runs=[result], game_weeks=weeks
            )

        if args.html:
            from mpg.bench.html_report import render_html

            Path(args.html).write_text(
                render_html(session, multi, title=args.name), encoding="utf-8"
            )
            print(f"  Rapport HTML : {args.html}")
    finally:
        session.close()
    session = get_session_factory()()
    try:
        from mpg.services.recap import recap_game_week

        for week in weeks:
            print(recap_game_week(session, result.league_id, week))
            print()
        if args.show_lineups:
            from mpg.bench.lineups_text import render_lineups

            print(render_lineups(session, result.league_id, weeks[-1]))
    finally:
        session.close()
    print(f"  Compos détaillées : mpg lineups {result.league_id} {weeks[-1]}")
    print()
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Seed a browsable league and print how to reach it."""
    from sqlalchemy import create_engine

    from mpg.db.models import Base
    from mpg.demo_data import seed

    engine = create_engine(get_settings().database_url, future=True)
    Base.metadata.create_all(engine)

    session = get_session_factory()()
    try:
        info = seed(session)
        session.commit()
    finally:
        session.close()

    base = args.base_url.rstrip("/")
    print()
    print("  Base de démonstration prête.")
    print(f"    {base}/ui/login")
    print()
    print(f"    alice@example.com  /  {info['password']}   (Les Parigots, admin)")
    print(f"    bob@example.com    /  {info['password']}")
    print()
    for row in info["leagues"]:
        if row["fixture_id"]:
            home, away = row["score"]
            print(f"    {row['name']}  —  {home} - {away}")
            print(f"      {base}/ui/leagues/{row['league_id']}/matches/{row['fixture_id']}")
        else:
            print(f"    {row['name']}")
            print(f"      {base}/ui/leagues/{row['league_id']}/mercato")
    print()
    return 0


def cmd_scenarios(args: argparse.Namespace) -> int:
    """Run the five acceptance scenarios of the spec and print their scorelines."""
    from mpg.demo import run_scenarios

    run_scenarios(full_report=getattr(args, "report", False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mpg", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="refresh the pool from the public MPG API")
    ingest.add_argument("--championship", type=int, default=get_settings().default_championship)
    ingest.add_argument("--seasons", help="comma-separated list, e.g. 2024,2025,2026")
    ingest.set_defaults(func=cmd_ingest)

    round_ = sub.add_parser("resolve-round", help="resolve one mercato round")
    round_.add_argument("round_id", type=int)
    round_.set_defaults(func=cmd_resolve_round)

    week = sub.add_parser("resolve-week", help="resolve a league game week")
    week.add_argument("league_id", type=int)
    week.add_argument("game_week", type=int)
    week.add_argument(
        "--summary", action="store_true", help="scorelines only, without the full report"
    )
    week.set_defaults(func=cmd_resolve_week)

    replay = sub.add_parser("replay", help="recompute a fixture flagged for recompute")
    replay.add_argument("fixture_id", type=int)
    replay.add_argument(
        "--summary", action="store_true", help="the new score only, without the report"
    )
    replay.set_defaults(func=cmd_replay)

    table = sub.add_parser("standings", help="print a league table")
    table.add_argument("league_id", type=int)
    table.set_defaults(func=cmd_standings)

    recap = sub.add_parser("recap", help="print the side-by-side recap of a game week")
    recap.add_argument("league_id", type=int)
    recap.add_argument("game_week", type=int)
    recap.set_defaults(func=cmd_recap)

    lineups = sub.add_parser("lineups", help="print every team of a game week")
    lineups.add_argument("league_id", type=int)
    lineups.add_argument("game_week", type=int)
    lineups.set_defaults(func=cmd_lineups)

    bench = sub.add_parser("bench", help="play a league with model-driven agents")
    bench.add_argument("--models", default="",
                       help="comma-separated Ollama models, e.g. llama3.1:8b,qwen2.5:14b")
    bench.add_argument("--game-weeks", default="5", help="comma-separated game weeks")
    bench.add_argument("--rounds", type=int, default=5, help="mercato rounds")
    bench.add_argument("--host", default="http://localhost:11434")
    bench.add_argument("--timeout", type=float, default=300.0)
    bench.add_argument("--baseline", action="store_true",
                       help="add the heuristic agent as a control")
    bench.add_argument("--name", default="Benchmark")
    bench.add_argument("--runs", type=int, default=1,
                       help="play the benchmark this many times and pool the results")
    bench.add_argument("--temperature", type=float, default=0.0)
    bench.add_argument("--think", action="store_true",
                       help="let reasoning models think; they return an empty object "
                            "in JSON mode when they do")
    bench.add_argument("--whole-pool", action="store_true",
                       help="do not restrict the pool to players the data covers; "
                            "most of a squad then reads as absent")
    bench.add_argument("--html", default="",
                       help="write a self-contained HTML report to this path")
    bench.add_argument("--show-lineups", action="store_true",
                       help="print every team at the end of the run")
    bench.set_defaults(func=cmd_bench)

    demo = sub.add_parser("demo", help="seed a browsable demo league")
    demo.add_argument("--base-url", default="http://localhost:8000")
    demo.set_defaults(func=cmd_demo)

    scenarios = sub.add_parser("scenarios", help="run the five acceptance scenarios")
    scenarios.add_argument(
        "--report", action="store_true", help="print the full report of each scenario"
    )
    scenarios.set_defaults(func=cmd_scenarios)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
