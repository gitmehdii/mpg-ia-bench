"""Printing a benchmark result."""

from __future__ import annotations

from mpg.bench.runner import BenchResult
from mpg.presentation import number


def render(result: BenchResult) -> str:
    lines = [
        "",
        f"  Résultat du benchmark — journée(s) {', '.join(map(str, result.game_weeks))}",
        "  " + "─" * 86,
        f"  {'#':>2}  {'agent':<22} {'pts':>4} {'bp':>4} {'bc':>4} {'réels':>6} {'MPG':>4} "
        f"{'budget':>7} {'fiab.':>6} {'temps':>8}",
    ]
    for report in result.agents:
        reliability = f"{report.reliability * 100:.0f}%" if report.calls else "—"
        seconds = f"{number(report.seconds, 1)}s" if report.seconds else "—"
        lines.append(
            f"  {report.rank or '?':>2}  {report.name[:22]:<22} {report.points:>4} "
            f"{report.goals_for:>4} {report.goals_against:>4} {report.real_goals:>6} "
            f"{report.mpg_goals:>4} {report.budget_spent:>6} M {reliability:>6} {seconds:>8}"
        )

    failing = [r for r in result.agents if r.failures]
    if failing:
        lines += ["", "  Décisions que le jeu a dû corriger :"]
        for report in failing:
            lines.append(f"    {report.name} — {report.failures}/{len(report.calls)}")
            for call in report.calls:
                if call.failure:
                    lines.append(f"      [{call.kind}] {call.failure[:110]}")

    notes = [
        (r.name, call) for r in result.agents for call in r.calls if call.raw
    ]
    if notes:
        lines += ["", "  Un mot de chaque agent :"]
        seen = set()
        for name, call in notes:
            if name in seen:
                continue
            seen.add(name)
            snippet = call.raw.replace("\n", " ")[:120]
            lines.append(f"    {name}: {snippet}")

    incomplete = [r for r in result.agents if not r.squad_complete]
    if incomplete:
        lines += ["", "  Effectifs complétés d'office au repêchage :"]
        lines += [f"    {r.name}" for r in incomplete]

    lines.append("")
    return "\n".join(lines)


def render_aggregate(result) -> str:
    """The pooled table. Means, and the spread that says how much was luck."""
    from mpg.presentation import number

    runs = result.aggregates[0].runs if result.aggregates else 0
    lines = [
        "",
        f"  Benchmark agrégé — {runs} run(s), journée(s) "
        f"{', '.join(map(str, result.game_weeks))}",
        "  " + "─" * 88,
        f"  {'#':>2}  {'agent':<22} {'pts moy':>8} {'écart':>7} {'1ers':>5} "
        f"{'diff':>6} {'MPG':>5} {'budget':>8} {'fiab.':>6} {'temps/run':>10}",
    ]
    for rank, entry in enumerate(result.aggregates, start=1):
        spread = number(entry.points_spread, 1) if entry.runs > 1 else "—"
        mpg = number(sum(entry.mpg_goals) / entry.runs, 1) if entry.runs else "0"
        budget = int(sum(entry.budget_spent) / entry.runs) if entry.runs else 0
        reliability = f"{entry.reliability * 100:.0f}%" if entry.calls else "—"
        seconds = f"{number(entry.mean_seconds, 0)}s" if entry.seconds else "—"
        lines.append(
            f"  {rank:>2}  {entry.name[:22]:<22} {number(entry.mean_points, 2):>8} "
            f"{spread:>7} {entry.wins:>5} {number(entry.mean_goal_difference, 1):>6} "
            f"{mpg:>5} {budget:>6} M {reliability:>6} {seconds:>10}"
        )

    if runs > 1:
        lines += [
            "",
            "  « écart » est l'écart-type des points entre runs : s'il approche l'écart",
            "  entre deux agents, le classement ne les sépare pas vraiment.",
        ]
    incomplete = [e for e in result.aggregates if e.incomplete_squads]
    if incomplete:
        lines += ["", "  Effectifs complétés d'office au repêchage :"]
        lines += [
            f"    {e.name} — {e.incomplete_squads}/{e.runs} run(s)" for e in incomplete
        ]
    lines.append("")
    return "\n".join(lines)
