# Copyright (c) Syntropy Systems
"""whirr status command."""
from __future__ import annotations

import shlex
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import typer
from rich.console import Console
from rich.table import Table

from whirr.config import get_db_path, require_whirr_dir
from whirr.db import get_active_jobs, get_connection, get_job

if TYPE_CHECKING:
    from whirr.models.db import JobRecord

console = Console()


def format_duration(started_at: str | None, finished_at: str | None = None) -> str:
    """Format duration from started_at to now or finished_at."""
    if not started_at:
        return "-"

    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.now(timezone.utc)
        if finished_at:
            end = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
        delta = end - start
        total_seconds = int(delta.total_seconds())
    except (TypeError, ValueError):
        return "-"
    else:
        if total_seconds < 60:
            return f"{total_seconds}s"
        if total_seconds < 3600:
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            return f"{minutes}m {seconds}s"
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        return f"{hours}h {minutes}m"


def format_time_ago(timestamp: str | None) -> str:
    """Format a timestamp as time ago."""
    if not timestamp:
        return "-"

    try:
        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - ts
        total_seconds = int(delta.total_seconds())
    except (TypeError, ValueError):
        return "-"
    else:
        if total_seconds < 60:
            return "just now"
        if total_seconds < 3600:
            minutes = total_seconds // 60
            return f"{minutes}m ago"
        if total_seconds < 86400:
            hours = total_seconds // 3600
            return f"{hours}h ago"
        days = total_seconds // 86400
        return f"{days}d ago"


def status(
    job_id: int | None = typer.Argument(
        None,
        help="Job ID to show details for",
    ),
) -> None:
    """Show queue status.

    Without arguments, shows all active jobs (queued and running).
    With a job ID, shows detailed information about that job.
    """
    try:
        whirr_dir = require_whirr_dir()
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    db_path = get_db_path(whirr_dir)
    conn = get_connection(db_path)

    try:
        if job_id is not None:
            # Show single job details
            job = get_job(conn, job_id)
            if job is None:
                console.print(f"[red]Error:[/red] Job #{job_id} not found")
                raise typer.Exit(1)

            _show_job_details(job)
        else:
            # Show all active jobs
            jobs = get_active_jobs(conn)
            _show_job_table(jobs)
    finally:
        conn.close()


def _show_job_table(jobs: list[JobRecord]) -> None:
    """Display jobs in a table."""
    if not jobs:
        console.print("[dim]No active jobs[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="dim")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Runtime")
    table.add_column("Submitted")

    for job in jobs:
        status_style = {
            "queued": "yellow",
            "running": "blue",
            "completed": "green",
            "failed": "red",
            "cancelled": "dim",
        }.get(job.status, "white")

        table.add_row(
            str(job.id),
            job.name or f"job-{job.id}",
            f"[{status_style}]{job.status}[/{status_style}]",
            format_duration(job.started_at),
            format_time_ago(job.created_at),
        )

    console.print(table)


def _show_job_details(job: JobRecord) -> None:
    """Display detailed job information."""
    status_style = {
        "queued": "yellow",
        "running": "blue",
        "completed": "green",
        "failed": "red",
        "cancelled": "dim",
    }.get(job.status, "white")

    # Parse command_argv from JSON if needed
    command_argv = job.command_argv
    command_display = shlex.join(command_argv) if command_argv else "-"

    console.print(f"\n[bold]Job #{job.id}[/bold]")
    console.print(f"  [dim]name:[/dim] {job.name or '-'}")
    console.print(f"  [dim]status:[/dim] [{status_style}]{job.status}[/{status_style}]")
    console.print(f"  [dim]command:[/dim] {command_display}")
    if job.workdir:
        console.print(f"  [dim]workdir:[/dim] {job.workdir}")

    if job.tags:
        console.print(f"  [dim]tags:[/dim] {', '.join(job.tags)}")

    console.print()
    console.print(f"  [dim]created:[/dim] {format_time_ago(job.created_at)}")

    if job.started_at:
        console.print(f"  [dim]started:[/dim] {format_time_ago(job.started_at)}")
        runtime = format_duration(job.started_at, job.finished_at)
        console.print(f"  [dim]runtime:[/dim] {runtime}")

    if job.finished_at:
        console.print(f"  [dim]finished:[/dim] {format_time_ago(job.finished_at)}")

    if job.worker_id:
        console.print(f"  [dim]worker:[/dim] {job.worker_id}")

    if job.exit_code is not None:
        console.print(f"  [dim]exit_code:[/dim] {job.exit_code}")

    if job.error_message:
        console.print(f"  [dim]error:[/dim] {job.error_message}")

    if job.run_id:
        console.print(f"  [dim]run_id:[/dim] {job.run_id}")
