"""whirr watch command - live updating status view."""

import time
from datetime import datetime, timezone

import typer
from rich.console import Console
from rich.live import Live
from rich.table import Table

from whirr.config import get_db_path, require_whirr_dir
from whirr.db import get_active_jobs, get_connection, get_workers

console = Console()


def format_duration(started_at: str) -> str:
    """Format duration from start time to now."""
    if not started_at:
        return "-"

    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - start
        total_seconds = int(delta.total_seconds())

        if total_seconds < 60:
            return f"{total_seconds}s"
        elif total_seconds < 3600:
            m, s = divmod(total_seconds, 60)
            return f"{m}m {s}s"
        else:
            h, rem = divmod(total_seconds, 3600)
            m, s = divmod(rem, 60)
            return f"{h}h {m}m"
    except Exception:
        return "-"


def format_time_ago(timestamp: str) -> str:
    """Format a timestamp as time ago."""
    if not timestamp:
        return "-"

    try:
        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - ts
        seconds = int(delta.total_seconds())

        if seconds < 60:
            return f"{seconds}s ago"
        elif seconds < 3600:
            return f"{seconds // 60}m ago"
        else:
            return f"{seconds // 3600}h ago"
    except Exception:
        return "-"


def build_jobs_table(jobs: list[dict]) -> Table:
    """Build the jobs status table."""
    table = Table(title="Jobs", show_header=True, header_style="bold")
    table.add_column("ID", style="dim", width=4)
    table.add_column("Name", width=20)
    table.add_column("Status", width=10)
    table.add_column("Runtime", width=10)
    table.add_column("Worker", width=15)

    if not jobs:
        table.add_row("-", "[dim]No active jobs[/dim]", "-", "-", "-")
        return table

    for job in jobs:
        status_style = {
            "queued": "yellow",
            "running": "blue",
        }.get(job["status"], "white")

        runtime = format_duration(job["started_at"]) if job["status"] == "running" else "-"
        worker = job.get("worker_id") or "-"

        table.add_row(
            str(job["id"]),
            job["name"] or f"[dim]{job['command_argv'][0]}[/dim]" if job.get("command_argv") else "-",
            f"[{status_style}]{job['status']}[/{status_style}]",
            runtime,
            worker[:15] if len(worker) > 15 else worker,
        )

    return table


def build_workers_table(workers: list[dict]) -> Table:
    """Build the workers status table."""
    table = Table(title="Workers", show_header=True, header_style="bold")
    table.add_column("ID", width=20)
    table.add_column("Status", width=10)
    table.add_column("Job", width=6)
    table.add_column("Last Seen", width=10)

    if not workers:
        table.add_row("[dim]No workers[/dim]", "-", "-", "-")
        return table

    for worker in workers:
        status_style = {
            "idle": "green",
            "busy": "blue",
            "offline": "dim",
        }.get(worker["status"], "white")

        job_id = str(worker["current_job_id"]) if worker.get("current_job_id") else "-"
        last_seen = format_time_ago(worker.get("last_heartbeat"))

        table.add_row(
            worker["id"],
            f"[{status_style}]{worker['status']}[/{status_style}]",
            job_id,
            last_seen,
        )

    return table


def build_display(jobs: list[dict], workers: list[dict]) -> Table:
    """Build the full display layout."""
    # Create a container table
    layout = Table.grid(padding=1)
    layout.add_row(build_jobs_table(jobs))
    layout.add_row(build_workers_table(workers))
    layout.add_row(f"[dim]Last updated: {datetime.now().strftime('%H:%M:%S')} (Ctrl+C to exit)[/dim]")
    return layout


def watch(
    interval: float = typer.Option(
        2.0,
        "--interval", "-i",
        help="Refresh interval in seconds",
    ),
) -> None:
    """
    Watch job and worker status with live updates.

    Like 'watch' but prettier. Press Ctrl+C to exit.
    """
    try:
        whirr_dir = require_whirr_dir()
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    db_path = get_db_path(whirr_dir)

    console.print("[dim]Starting watch mode...[/dim]")

    try:
        with Live(console=console, refresh_per_second=1, screen=True) as live:
            while True:
                conn = get_connection(db_path)
                try:
                    jobs = get_active_jobs(conn)
                    workers = get_workers(conn)
                finally:
                    conn.close()

                live.update(build_display(jobs, workers))
                time.sleep(interval)

    except KeyboardInterrupt:
        console.print("\n[dim]Watch stopped[/dim]")
