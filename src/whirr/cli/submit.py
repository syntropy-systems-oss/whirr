# Copyright (c) Syntropy Systems
"""whirr submit command."""
from __future__ import annotations

from pathlib import Path
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
    server: Optional[str] = typer.Option(
        None,
        "--server", "-s",
        envvar="WHIRR_SERVER_URL",
        help="Server URL for remote submission",
    ),
) -> None:
    """Submit a job to the queue.

    Use -- to separate whirr options from the command:

        whirr submit --name baseline -- python train.py --lr 0.01

    For remote submission to a whirr server:

        whirr submit --server http://head-node:8080 -- python train.py
    """
    # Get command from remaining args (after --)
    command_argv = list(ctx.args)

    if not command_argv:
        console.print("[red]Error:[/red] No command provided")
        console.print("\nUsage: whirr submit [OPTIONS] -- COMMAND...")
        console.print(
            "\nExample: whirr submit --name baseline -- python train.py --lr 0.01"
        )
        raise typer.Exit(1)

    # Parse tags
    tag_list = None
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    # Store absolute workdir at submission time
    workdir = str(Path.cwd())

    if server:
        _submit_remote(server, command_argv, workdir, name, tag_list)
    else:
        _submit_local(command_argv, workdir, name, tag_list)


def _submit_local(
    command_argv: list[str],
    workdir: str,
    name: str | None,
    tag_list: list[str] | None,
) -> None:
    """Submit job to local queue."""
    try:
        whirr_dir = require_whirr_dir()
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

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

    _print_success(job_id, command_argv, workdir, name, tag_list)


def _submit_remote(
    server_url: str,
    command_argv: list[str],
    workdir: str,
    name: str | None,
    tag_list: list[str] | None,
) -> None:
    """Submit job to remote server."""
    try:
        from whirr.client import WhirrClient, WhirrClientError
    except ImportError as e:
        console.print("[red]Error:[/red] httpx is required for remote submission.")
        console.print("Install with: pip install whirr[server]")
        raise typer.Exit(1) from e

    client = WhirrClient(server_url)
    try:
        result = client.submit_job(
            command_argv=command_argv,
            workdir=workdir,
            name=name,
            tags=tag_list,
        )
        job_id = result.job_id
    except WhirrClientError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    finally:
        client.close()

    _print_success(job_id, command_argv, workdir, name, tag_list, remote=True)


def _print_success(
    job_id: int,
    command_argv: list[str],
    workdir: str,
    name: str | None,
    tag_list: list[str] | None,
    remote: bool = False,
) -> None:
    """Print success message after job submission."""
    import shlex
    command_display = shlex.join(command_argv)

    console.print(f"[green]Submitted job #{job_id}[/green]")
    if remote:
        console.print("  [dim]mode:[/dim] remote")
    if name:
        console.print(f"  [dim]name:[/dim] {name}")
    console.print(f"  [dim]command:[/dim] {command_display}")
    console.print(f"  [dim]workdir:[/dim] {workdir}")
    if tag_list:
        console.print(f"  [dim]tags:[/dim] {', '.join(tag_list)}")
