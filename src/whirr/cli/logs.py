# Copyright (c) Syntropy Systems
"""whirr logs command."""

import time
from pathlib import Path
from typing import cast

import typer
from rich.console import Console

from whirr.config import get_db_path, get_runs_dir, require_whirr_dir
from whirr.db import get_connection, get_job, get_run_by_job_id
from whirr.models.db import JobRecord

console = Console()


def logs(
    job_id: int = typer.Argument(
        default=cast("int", cast("object", ...)),
        help="Job ID to show logs for",
    ),
    follow: bool = typer.Option(
        False,
        "--follow", "-f",
        help="Follow log output (like tail -f)",
    ),
) -> None:
    """Show logs for a job.

    Displays the output.log from the job's run directory.
    Use --follow to watch for new output.
    """
    try:
        whirr_dir = require_whirr_dir()
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    db_path = get_db_path(whirr_dir)
    runs_dir = get_runs_dir(whirr_dir)

    conn = get_connection(db_path)
    try:
        job = get_job(conn, job_id)
        if job is None:
            console.print(f"[red]Error:[/red] Job #{job_id} not found")
            raise typer.Exit(1)

        # Find run directory
        run = get_run_by_job_id(conn, job_id)
    finally:
        conn.close()

    # Determine log path
    if run and run.run_dir:
        log_path = Path(run.run_dir) / "output.log"
    else:
        # Fallback to default location
        log_path = runs_dir / f"job-{job_id}" / "output.log"

    if follow:
        _follow_logs(log_path, job)
    else:
        _show_logs(log_path, job)


def _show_logs(log_path: Path, job: JobRecord) -> None:
    """Show current log contents."""
    if not log_path.exists():
        if job.status == "queued":
            console.print("[dim]Job is queued, no logs yet[/dim]")
        elif job.status == "running":
            console.print("[dim]Job is running, log file not created yet[/dim]")
        else:
            console.print("[dim]No logs available[/dim]")
        return

    with log_path.open() as f:
        content = f.read()

    if content:
        console.print(content, end="")
    else:
        console.print("[dim]Log file is empty[/dim]")


def _follow_logs(log_path: Path, _job: JobRecord) -> None:
    """Follow log output like tail -f."""
    # Wait for file to exist
    wait_count = 0
    while not log_path.exists():
        if wait_count == 0:
            console.print("[dim]Waiting for log file...[/dim]")
        wait_count += 1
        time.sleep(0.5)

        # Check if job finished without producing logs
        if wait_count > 20:  # 10 seconds
            console.print(
                "[yellow]Log file not appearing, job may have failed to start[/yellow]"
            )
            return

    # Tail the file
    console.print(f"[dim]Following {log_path}...[/dim]\n")

    with log_path.open() as f:
        # First, print existing content
        content = f.read()
        if content:
            console.print(content, end="")

        # Then follow new content
        try:
            while True:
                line = f.readline()
                if line:
                    console.print(line, end="")
                else:
                    time.sleep(0.1)
        except KeyboardInterrupt:
            console.print("\n[dim]Stopped following logs[/dim]")
