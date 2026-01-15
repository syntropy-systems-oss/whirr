# Copyright (c) Syntropy Systems
"""whirr sweep command."""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from whirr.config import get_db_path, require_whirr_dir
from whirr.db import create_job, get_connection
from whirr.sweep import SweepConfig, generate_sweep_jobs

console = Console()


def sweep(
    config_file: Path = typer.Argument(
        ...,
        help="Path to sweep configuration YAML file",
        exists=True,
    ),
    prefix: str | None = typer.Option(
        None,
        "--prefix", "-p",
        help="Prefix for job names (overrides name in config)",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run", "-n",
        help="Preview jobs without submitting",
    ),
) -> None:
    r"""Generate and submit jobs from a sweep configuration.

    Example sweep.yaml:

    \b
        program: python train.py
        name: lr-sweep
        method: grid
        parameters:
          lr:
            values: [0.01, 0.001, 0.0001]
          batch_size:
            values: [16, 32]
    """
    try:
        whirr_dir = require_whirr_dir()
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    # Load sweep config
    try:
        sweep_config = SweepConfig.from_yaml(config_file)
    except (OSError, ValueError) as e:
        console.print(f"[red]Error loading config:[/red] {e}")
        raise typer.Exit(1) from e

    # Generate jobs
    try:
        jobs = generate_sweep_jobs(sweep_config, prefix)
    except ValueError as e:
        console.print(f"[red]Error generating jobs:[/red] {e}")
        raise typer.Exit(1) from e

    if not jobs:
        console.print("[yellow]No jobs generated from sweep config[/yellow]")
        return

    # Display jobs
    table = Table(title=f"Sweep: {prefix or sweep_config.name or 'unnamed'}")
    table.add_column("#", style="dim")
    table.add_column("Name")
    table.add_column("Command")
    table.add_column("Parameters")

    for i, job in enumerate(jobs):
        # Format parameters
        param_str = ", ".join(f"{k}={v}" for k, v in job.config.items())
        # Truncate command if too long
        cmd_str = " ".join(job.command)
        if len(cmd_str) > 50:
            cmd_str = cmd_str[:47] + "..."

        table.add_row(
            str(i),
            job.name,
            cmd_str,
            param_str,
        )

    console.print(table)
    console.print(f"\n[bold]{len(jobs)} jobs[/bold] will be submitted")

    if dry_run:
        console.print("\n[yellow]Dry run - no jobs submitted[/yellow]")
        return

    # Submit jobs
    db_path = get_db_path(whirr_dir)
    workdir = str(Path.cwd())

    conn = get_connection(db_path)
    try:
        submitted_ids: list[int] = []
        for job in jobs:
            job_id = create_job(
                conn,
                command_argv=job.command,
                workdir=workdir,
                name=job.name,
                tags=job.tags,
                config=job.config,
            )
            submitted_ids.append(job_id)

        console.print(f"\n[green]Submitted {len(submitted_ids)} jobs[/green]")
        console.print(f"  [dim]Job IDs:[/dim] {submitted_ids[0]}-{submitted_ids[-1]}")

    finally:
        conn.close()
