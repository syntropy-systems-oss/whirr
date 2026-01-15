# Copyright (c) Syntropy Systems
"""whirr cancel command."""
from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console

from whirr.config import get_db_path, require_whirr_dir
from whirr.db import cancel_all_queued, cancel_job, get_connection, get_job

console = Console()


def cancel(
    job_id: Optional[int] = typer.Argument(
        None,
        help="Job ID to cancel",
    ),
    all_queued: bool = typer.Option(
        False,
        "--all-queued",
        help="Cancel all queued jobs",
    ),
) -> None:
    """Cancel a job.

    For queued jobs: marks as cancelled immediately.
    For running jobs: signals the worker to terminate the job.
    """
    if job_id is None and not all_queued:
        console.print("[red]Error:[/red] Provide a job ID or use --all-queued")
        raise typer.Exit(1)

    try:
        whirr_dir = require_whirr_dir()
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    db_path = get_db_path(whirr_dir)
    conn = get_connection(db_path)

    try:
        if all_queued:
            count = cancel_all_queued(conn)
            if count > 0:
                console.print(f"[green]Cancelled {count} queued job(s)[/green]")
            else:
                console.print("[dim]No queued jobs to cancel[/dim]")
        else:
            if job_id is None:
                console.print("[red]Error:[/red] Job ID is required")
                raise typer.Exit(1)
            job = get_job(conn, job_id)
            if job is None:
                console.print(f"[red]Error:[/red] Job #{job_id} not found")
                raise typer.Exit(1)

            if job.status in ("completed", "failed", "cancelled"):
                console.print(
                    f"[yellow]Job #{job_id} is already {job.status}[/yellow]"
                )
                return

            old_status = cancel_job(conn, job_id)

            if old_status == "queued":
                console.print(f"[green]Cancelled job #{job_id}[/green]")
            elif old_status == "running":
                console.print(
                    f"[yellow]Cancellation requested for job #{job_id}[/yellow]"
                )
                console.print("[dim]Worker will terminate the job shortly[/dim]")
    finally:
        conn.close()
