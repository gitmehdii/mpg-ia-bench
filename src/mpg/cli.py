"""Command line entry points: ingestion, mercato and game week resolution.

    python -m mpg.cli ingest [--championship 1] [--seasons 2024,2025,2026]
    python -m mpg.cli resolve-round <round_id>
    python -m mpg.cli resolve-week <league_id> <game_week> [--summary]
    python -m mpg.cli replay <fixture_id> [--summary]
    python -m mpg.cli standings <league_id>
    python -m mpg.cli demo
    python -m mpg.cli scenarios
"""

from __future__ import annotations

import argparse
import sys

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
