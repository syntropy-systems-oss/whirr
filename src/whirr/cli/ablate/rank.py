# Copyright (c) Syntropy Systems
"""whirr ablate rank command."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import typer
from rich.console import Console
from rich.table import Table

from whirr.ablate import load_session_by_name
from whirr.config import get_db_path, get_runs_dir, require_whirr_dir
from whirr.db import get_connection, get_run
from whirr.models.base import JSONValue
from whirr.run import read_meta, read_metrics

if TYPE_CHECKING:
    from whirr.models.db import RunRecord

console = Console()

SummaryValues = Mapping[str, JSONValue]


class DeltaEffect(TypedDict):
    """Summary of delta effects on the metric."""

    name: str
    mean: float
    effect: float
    n: int
    values: list[float]


def extract_metric(
    run_dir: Path | None, metric_name: str, summary: SummaryValues | None
) -> float | None:
    """Extract metric value from run.

    Priority:
    1. summary[metric_name] if present
    2. Last occurrence in metrics.jsonl
    """
    # Try summary first
    if summary and metric_name in summary:
        value = summary[metric_name]
        if isinstance(value, (int, float)):
            return float(value)

    # Fallback to metrics.jsonl
    if run_dir:
        metrics_path = run_dir / "metrics.jsonl"
        if metrics_path.exists():
            metrics = read_metrics(metrics_path)
            # Find last occurrence of metric
            for m in reversed(metrics):
                record = m.model_dump()
                if metric_name in record:
                    value = record[metric_name]
                    if isinstance(value, (int, float)):
                        return float(value)

    return None


def rank(
    name: str = typer.Argument(..., help="Session name"),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed per-replicate results",
    ),
) -> None:
    """Rank deltas by their effect on the target metric.

    Shows which delta has the strongest effect on the metric.
    Deltas are ranked by absolute effect (strongest first).

    Example:
        whirr ablate rank weird-behavior

    """
    try:
        whirr_dir = require_whirr_dir()
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    try:
        session = load_session_by_name(name, whirr_dir)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] Session '{name}' not found")
        raise typer.Exit(1) from e

    if not session.runs:
        console.print(
            f"[yellow]No runs recorded.[/yellow] Run 'whirr ablate run {name}' first."
        )
        raise typer.Exit(1)

    # Collect metrics per condition
    db_path = get_db_path(whirr_dir)
    runs_dir = get_runs_dir(whirr_dir)
    conn = get_connection(db_path)

    try:
        metrics_by_condition: defaultdict[str, list[float]] = defaultdict(list)
        pending_count = 0
        failed_count = 0
        no_metric_count = 0

        for run_result in session.runs:
            # Get run from database
            db_run: RunRecord | None = get_run(conn, run_result.run_id)

            run_dir: Path | None = None
            summary: SummaryValues | None = None

            if db_run is None:
                # Try to find by job_id pattern
                run_dir = runs_dir / f"job-{run_result.job_id}"
                if not run_dir.exists():
                    pending_count += 1
                    continue
                meta = read_meta(run_dir)
                if meta and meta.summary:
                    summary = meta.summary.values
            else:
                status = db_run.status
                if status == "running":
                    pending_count += 1
                    continue
                if status == "failed":
                    failed_count += 1
                    run_result.status = "failed"
                    continue

                run_result.status = status or "completed"

                # Get run directory and summary
                if db_run.run_dir:
                    run_dir = Path(db_run.run_dir)

                # Try to get summary from db first
                if db_run.summary:
                    summary = db_run.summary.values

                # Fallback to meta.json
                if summary is None and run_dir:
                    meta = read_meta(run_dir)
                    if meta and meta.summary:
                        summary = meta.summary.values

            # Extract target metric
            value = extract_metric(run_dir, session.metric, summary)

            if value is not None:
                metrics_by_condition[run_result.condition].append(value)
                run_result.metric_value = value
                run_result.outcome = None
            else:
                run_result.outcome = "no_metric"
                no_metric_count += 1

        session.save()  # Update with collected metrics

    finally:
        conn.close()

    # Calculate statistics
    if "baseline" not in metrics_by_condition or not metrics_by_condition["baseline"]:
        console.print("[red]Error:[/red] No baseline results found")
        if pending_count > 0:
            console.print(f"  {pending_count} jobs still pending")
        raise typer.Exit(1)

    baseline_values = metrics_by_condition["baseline"]
    baseline_mean = sum(baseline_values) / len(baseline_values)

    # Compute delta effects
    delta_effects: list[DeltaEffect] = []
    for delta_name in session.deltas:
        if delta_name not in metrics_by_condition:
            continue

        values = metrics_by_condition[delta_name]
        if not values:
            continue

        delta_mean = sum(values) / len(values)
        effect = delta_mean - baseline_mean

        delta_effects.append(
            {
                "name": delta_name,
                "mean": delta_mean,
                "effect": effect,
                "n": len(values),
                "values": values,
            }
        )

    if not delta_effects:
        console.print("[yellow]No delta results found yet.[/yellow]")
        if pending_count > 0:
            console.print(f"  {pending_count} jobs still pending")
        raise typer.Exit(1)

    # Sort by absolute effect (strongest first)
    delta_effects.sort(key=lambda item: abs(item["effect"]), reverse=True)

    # Display results
    console.print(f"\n[bold]Ablation Results: {session.name}[/bold]")
    console.print(f"  [dim]metric:[/dim] {session.metric}")
    console.print(
        f"  [dim]baseline mean:[/dim] {baseline_mean:.4f} (n={len(baseline_values)})"
    )

    if pending_count > 0:
        console.print(f"  [yellow]pending:[/yellow] {pending_count} jobs")
    if failed_count > 0:
        console.print(f"  [red]failed:[/red] {failed_count} jobs")
    if no_metric_count > 0:
        console.print(f"  [dim]no_metric:[/dim] {no_metric_count} runs")

    # Ranking table
    table = Table(title="Delta Ranking (strongest effect first)")
    table.add_column("Rank", style="dim")
    table.add_column("Delta")
    table.add_column("Mean", justify="right")
    table.add_column("Effect", justify="right")
    table.add_column("N", justify="right")

    for rank_idx, delta in enumerate(delta_effects, 1):
        effect_str = f"{delta['effect']:+.4f}"
        style = "green" if rank_idx == 1 else ""

        table.add_row(
            str(rank_idx),
            delta["name"],
            f"{delta['mean']:.4f}",
            effect_str if not style else f"[{style}]{effect_str}[/{style}]",
            str(delta["n"]),
        )

    console.print(table)

    # Winner announcement
    winner = delta_effects[0]
    console.print(f"\n[green]Strongest effect:[/green] {winner['name']}")
    console.print(f"  Effect: {winner['effect']:+.4f} (delta mean - baseline mean)")

    # Verbose output
    if verbose:
        console.print("\n[bold]Per-replicate values:[/bold]")
        console.print(f"  baseline: {baseline_values}")
        for delta in delta_effects:
            console.print(f"  {delta['name']}: {delta['values']}")
