# Copyright (c) Syntropy Systems
"""whirr retry command."""

import typer
from rich.console import Console

from whirr.config import get_db_path, require_whirr_dir
from whirr.db import get_connection, get_job, retry_job

console = Console()


def retry(
    job_id: int = typer.Argument(
        ...,
        help="Job ID to retry",
    ),
) -> None:
    """Retry a failed or cancelled job.

    Creates a new job with the same command, incrementing the attempt counter.
    """
    try:
        whirr_dir = require_whirr_dir()
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    db_path = get_db_path(whirr_dir)
    conn = get_connection(db_path)

    try:
        # Get original job for display
        original = get_job(conn, job_id)
        if original is None:
            console.print(f"[red]Error:[/red] Job #{job_id} not found")
            raise typer.Exit(1)

        if original.status not in ("failed", "cancelled"):
            console.print("[red]Error:[/red] Can only retry failed or cancelled jobs")
            console.print(f"  Job #{job_id} has status: {original.status}")
            raise typer.Exit(1)

        # Create retry job
        new_job_id = retry_job(conn, job_id)

        console.print(f"[green]Created retry job #{new_job_id}[/green]")
        console.print(f"  [dim]original:[/dim] #{job_id}")
        console.print(f"  [dim]attempt:[/dim] {original.attempt + 1}")
        console.print(f"  [dim]command:[/dim] {' '.join(original.command_argv)}")

    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    finally:
        conn.close()
