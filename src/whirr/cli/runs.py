# Copyright (c) Syntropy Systems
"""whirr runs and show commands."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from whirr.config import get_db_path, require_whirr_dir
from whirr.db import get_connection, get_run, get_runs
from whirr.run import read_metrics

console = Console()


def format_duration(seconds: float | None) -> str:
    """Format duration in seconds to human readable."""
    if seconds is None:
        return "-"

    total = int(seconds)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        m, s = divmod(total, 60)
        return f"{m}m {s}s"
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m"


def runs(
    status: Optional[str] = typer.Option(
        None,
        "--status", "-s",
        help="Filter by status (running, completed, failed)",
    ),
    tag: Optional[str] = typer.Option(
        None,
        "--tag", "-t",
        help="Filter by tag",
    ),
    last: int = typer.Option(
        20,
        "--last", "-n",
        help="Number of runs to show",
    ),
) -> None:
    """List experiment runs.

    Shows both completed jobs and direct runs.
    """
    try:
        whirr_dir = require_whirr_dir()
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    db_path = get_db_path(whirr_dir)
    conn = get_connection(db_path)

    try:
        run_list = get_runs(conn, status=status, tag=tag, limit=last)
    finally:
        conn.close()

    if not run_list:
        console.print("[dim]No runs found[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="dim")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Duration")
    table.add_column("Summary")

    for run in run_list:
        status_style = {
            "running": "blue",
            "completed": "green",
            "failed": "red",
        }.get(run.status, "white")

        # Parse summary if present
        summary_str = ""
        if run.summary:
            try:
                summary = run.summary.values
                # Format key metrics
                parts: list[str] = []
                for k, v in list(summary.items())[:3]:
                    if isinstance(v, float):
                        parts.append(f"{k}={v:.4f}")
                    else:
                        parts.append(f"{k}={v}")
                summary_str = ", ".join(parts)
            except (TypeError, ValueError):
                summary_str = ""

        table.add_row(
            run.id[:8] if len(run.id) > 8 else run.id,
            run.name or "-",
            f"[{status_style}]{run.status}[/{status_style}]",
            format_duration(run.duration_seconds),
            summary_str or "-",
        )

    console.print(table)


def show(
    run_id: str = typer.Argument(
        ...,
        help="Run ID to show details for",
    ),
) -> None:
    """Show detailed information about a run.

    Displays configuration, metrics, and summary.
    """
    try:
        whirr_dir = require_whirr_dir()
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    db_path = get_db_path(whirr_dir)
    conn = get_connection(db_path)

    try:
        run = get_run(conn, run_id)

        # Try partial match if exact not found
        if run is None:
            all_runs = get_runs(conn, limit=1000)
            matches = [r for r in all_runs if r.id.startswith(run_id)]
            if len(matches) == 1:
                run = matches[0]
            elif len(matches) > 1:
                console.print(f"[yellow]Ambiguous ID '{run_id}', matches:[/yellow]")
                for r in matches[:5]:
                    console.print(f"  {r.id} ({r.name})")
                raise typer.Exit(1)
    finally:
        conn.close()

    if run is None:
        console.print(f"[red]Error:[/red] Run '{run_id}' not found")
        raise typer.Exit(1)

    status_style = {
        "running": "blue",
        "completed": "green",
        "failed": "red",
    }.get(run.status, "white")

    console.print(f"\n[bold]Run {run.id}[/bold]")
    console.print(f"  [dim]name:[/dim] {run.name or '-'}")
    console.print(f"  [dim]status:[/dim] [{status_style}]{run.status}[/{status_style}]")

    if run.job_id:
        console.print(f"  [dim]job_id:[/dim] #{run.job_id}")

    # Tags
    if run.tags:
        console.print(f"  [dim]tags:[/dim] {', '.join(run.tags)}")

    # Duration
    console.print(f"  [dim]duration:[/dim] {format_duration(run.duration_seconds)}")
    console.print(f"  [dim]started:[/dim] {run.started_at}")
    if run.finished_at:
        console.print(f"  [dim]finished:[/dim] {run.finished_at}")

    # Config
    if run.config:
        console.print("\n[bold]Config[/bold]")
        for k, v in run.config.items():
            console.print(f"  {k}: {v}")

    # Summary
    if run.summary:
        console.print("\n[bold]Summary[/bold]")
        for k, v in run.summary.items():
            if isinstance(v, float):
                console.print(f"  {k}: {v:.6f}")
            else:
                console.print(f"  {k}: {v}")

    # Metrics summary from files
    if run.run_dir:
        run_dir = Path(run.run_dir)
        metrics_path = run_dir / "metrics.jsonl"
        if metrics_path.exists():
            metrics = read_metrics(metrics_path)
            if metrics:
                console.print(f"\n[bold]Metrics[/bold] ({len(metrics)} logged)")

                # Show last few
                for m in metrics[-3:]:
                    record = m.model_dump(by_alias=True)
                    parts = [
                        f"{k}={v:.4f}" if isinstance(v, float) else f"{k}={v}"
                        for k, v in record.items()
                        if not k.startswith("_")
                    ]
                    console.print(f"  {', '.join(parts)}")

    console.print()
