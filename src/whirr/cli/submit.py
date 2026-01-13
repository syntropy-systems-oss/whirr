"""whirr submit command."""

import os
from typing import Optional

import typer
from rich.console import Console

from whirr.config import get_db_path, require_whirr_dir
from whirr.db import create_job, get_connection

console = Console()


def submit(
    ctx: typer.Context,
    name: Optional[str] = typer.Option(
        None,
        "--name", "-n",
        help="Name for the job",
    ),
    tags: Optional[str] = typer.Option(
        None,
        "--tags", "-t",
        help="Comma-separated tags",
    ),
) -> None:
    """
    Submit a job to the queue.

    Use -- to separate whirr options from the command:

        whirr submit --name baseline -- python train.py --lr 0.01
    """
    # Get command from remaining args (after --)
    command_argv = list(ctx.args)

    if not command_argv:
        console.print("[red]Error:[/red] No command provided")
        console.print("\nUsage: whirr submit [OPTIONS] -- COMMAND...")
        console.print("\nExample: whirr submit --name baseline -- python train.py --lr 0.01")
        raise typer.Exit(1)

    # Parse tags
    tag_list = None
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    try:
        whirr_dir = require_whirr_dir()
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    # Store absolute workdir at submission time
    workdir = os.getcwd()

    db_path = get_db_path(whirr_dir)
    conn = get_connection(db_path)

    try:
        job_id = create_job(
            conn,
            command_argv=command_argv,
            workdir=workdir,
            name=name,
            tags=tag_list,
        )
    finally:
        conn.close()

    # Format command for display
    import shlex
    command_display = shlex.join(command_argv)

    console.print(f"[green]Submitted job #{job_id}[/green]")
    if name:
        console.print(f"  [dim]name:[/dim] {name}")
    console.print(f"  [dim]command:[/dim] {command_display}")
    console.print(f"  [dim]workdir:[/dim] {workdir}")
    if tag_list:
        console.print(f"  [dim]tags:[/dim] {', '.join(tag_list)}")
