# Copyright (c) Syntropy Systems
"""whirr ablate run command."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import typer
from rich.console import Console
from rich.table import Table

from whirr.ablate import get_ablations_dir, load_session_by_name
from whirr.ablate.models import AblationRunResult
from whirr.config import get_db_path, require_whirr_dir
from whirr.db import create_job, get_connection
from whirr.models.ablation import AblationSession, ConfigValue, FileValue
from whirr.models.run import RunConfig

if TYPE_CHECKING:
    from whirr.models.base import JSONValue

console = Console()


class AblationJob(TypedDict):
    """Generated job payload for ablation runs."""

    command: list[str]
    name: str
    tags: list[str]
    config: dict[str, JSONValue]
    condition: str
    replicate: int
    seed: int
    cfg_path: str


def resolve_config_value(value: ConfigValue) -> JSONValue:
    """Resolve a config value, extracting text from FileValue."""
    if isinstance(value, FileValue):
        return value.text
    return value


def generate_config(
    session_id: str,
    condition: str,
    replicate: int,
    seed: int,
    baseline: dict[str, ConfigValue],
    delta: dict[str, ConfigValue] | None,
) -> dict[str, JSONValue]:
    """Generate a config dict for a specific condition/replicate."""
    config: dict[str, JSONValue] = {
        "__ablate__": {
            "session_id": session_id,
            "condition": condition,
            "replicate": replicate,
            "seed": seed,
        }
    }

    # Add baseline values
    for k, v in baseline.items():
        config[k] = resolve_config_value(v)

    # Override with delta values if present
    if delta:
        for k, v in delta.items():
            config[k] = resolve_config_value(v)

    return config


def substitute_templates(argv: list[str], seed: int, cfg_path: str) -> list[str]:
    """Replace {{seed}} and {{cfg_path}} in command argv."""
    result: list[str] = []
    for arg in argv:
        rendered = arg.replace("{{seed}}", str(seed))
        rendered = rendered.replace("{{cfg_path}}", cfg_path)
        result.append(rendered)
    return result


def run(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Session name"),
    replicates: int | None = typer.Option(
        None,
        "--replicates",
        "-r",
        help="Number of replicates per condition (default: session default)",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Preview jobs without submitting",
    ),
    server: str | None = typer.Option(
        None,
        "--server",
        "-s",
        envvar="WHIRR_SERVER_URL",
        help="Server URL for remote submission",
    ),
) -> None:
    """Run all conditions with paired seeds.

    Use -- to separate options from the command template:

        whirr ablate run study -- python eval.py --seed {{seed}} --cfg {{cfg_path}}

    Template variables:
        {{seed}} - Replicate seed (deterministic from session seed_base)
        {{cfg_path}} - Path to generated config JSON
    """
    # Get command from remaining args (after --)
    command_argv = list(ctx.args)

    if not command_argv:
        console.print("[red]Error:[/red] No command provided")
        console.print(
            "\nUsage: whirr ablate run SESSION [OPTIONS] -- COMMAND [ARGS]..."
        )
        example_prefix = "\nExample: whirr ablate run study -- python eval.py"
        example_suffix = "--seed {{seed}} --cfg {{cfg_path}}"
        console.print(f"{example_prefix} {example_suffix}")
        raise typer.Exit(1)

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

    if not session.deltas:
        message = " ".join(
            [
                "[red]Error:[/red] No deltas added.",
                f"Use 'whirr ablate add {name} key=value'",
            ]
        )
        console.print(message)
        raise typer.Exit(1)

    # Use provided replicates or session default
    num_replicates = replicates if replicates is not None else session.replicates

    # Config directory
    configs_dir = get_ablations_dir(whirr_dir) / session.session_id / "configs"
    configs_dir.mkdir(parents=True, exist_ok=True)

    # Generate jobs for all conditions
    conditions = session.get_condition_names()
    jobs_to_submit: list[AblationJob] = []

    for replicate_idx in range(num_replicates):
        seed = session.get_seed(replicate_idx)

        for condition in conditions:
            # Get delta if not baseline
            delta = session.deltas.get(condition) if condition != "baseline" else None

            # Generate config
            config = generate_config(
                session_id=session.session_id,
                condition=condition,
                replicate=replicate_idx,
                seed=seed,
                baseline=session.baseline,
                delta=delta,
            )

            # Write config file
            cfg_filename = f"{condition}-{replicate_idx}.json"
            cfg_path = configs_dir / cfg_filename

            if not dry_run:
                _ = cfg_path.write_text(
                    RunConfig.model_validate(config).model_dump_json(indent=2)
                )

            # Build command with template substitution
            job_command = substitute_templates(command_argv, seed, str(cfg_path))

            # Build tags
            tags = [
                f"ablate:{session.session_id}",
                f"condition:{condition}",
                f"replicate:{replicate_idx}",
            ]

            # Build job config for tracking
            job_config: dict[str, JSONValue] = {
                "ablation_session": session.name,
                "ablation_session_id": session.session_id,
                "condition": condition,
                "replicate": replicate_idx,
                "seed": seed,
            }

            job_name = f"{session.name}-{condition}-{replicate_idx}"

            jobs_to_submit.append(
                {
                    "command": job_command,
                    "name": job_name,
                    "tags": tags,
                    "config": job_config,
                    "condition": condition,
                    "replicate": replicate_idx,
                    "seed": seed,
                    "cfg_path": str(cfg_path),
                }
            )

    # Display preview table
    table = Table(title=f"Ablation: {session.name}")
    table.add_column("Condition")
    table.add_column("Replicates")
    table.add_column("Sample Command")

    for condition in conditions:
        sample_job = next(j for j in jobs_to_submit if j["condition"] == condition)
        cmd_str = " ".join(sample_job["command"][:6])
        if len(sample_job["command"]) > 6:
            cmd_str += " ..."
        table.add_row(condition, str(num_replicates), cmd_str)

    console.print(table)
    console.print(f"\n[bold]{len(jobs_to_submit)} jobs[/bold] will be submitted")
    console.print(f"  [dim]conditions:[/dim] {len(conditions)}")
    console.print(f"  [dim]replicates:[/dim] {num_replicates}")
    console.print(f"  [dim]seed_base:[/dim] {session.seed_base}")

    if dry_run:
        console.print("\n[yellow]Dry run - no jobs submitted[/yellow]")
        console.print(f"  Configs would be written to: {configs_dir}")
        return

    # Submit jobs
    workdir = str(Path.cwd())

    if server:
        _submit_remote(server, jobs_to_submit, workdir, session)
    else:
        _submit_local(whirr_dir, jobs_to_submit, workdir, session)


def _submit_local(
    whirr_dir: Path,
    jobs_to_submit: list[AblationJob],
    workdir: str,
    session: AblationSession,
) -> None:
    """Submit jobs to local queue."""
    db_path = get_db_path(whirr_dir)
    conn = get_connection(db_path)

    try:
        submitted_ids: list[int] = []
        for job in jobs_to_submit:
            job_id = create_job(
                conn,
                command_argv=job["command"],
                workdir=workdir,
                name=job["name"],
                tags=job["tags"],
                config=job["config"],
            )
            submitted_ids.append(job_id)

            # Record in session
            session.runs.append(
                AblationRunResult(
                    run_id=f"job-{job_id}",
                    job_id=job_id,
                    condition=job["condition"],
                    replicate=job["replicate"],
                    seed=job["seed"],
                    status="queued",
                )
            )

        session.save()

        console.print(f"\n[green]Submitted {len(submitted_ids)} jobs[/green]")
        console.print(f"  [dim]Job IDs:[/dim] {submitted_ids[0]}-{submitted_ids[-1]}")
        console.print("\nMonitor: [cyan]whirr status[/cyan]")
        console.print(f"Rank:    [cyan]whirr ablate rank {session.name}[/cyan]")

    finally:
        conn.close()


def _submit_remote(
    server_url: str,
    jobs_to_submit: list[AblationJob],
    workdir: str,
    session: AblationSession,
) -> None:
    """Submit jobs to remote server."""
    try:
        from whirr.client import WhirrClient, WhirrClientError
    except ImportError as e:
        error_message = "[red]Error:[/red] httpx is required for remote submission."
        install_message = "Install with: pip install whirr[server]"
        console.print(f"{error_message} {install_message}")
        raise typer.Exit(1) from e

    client = WhirrClient(server_url)
    try:
        submitted_ids: list[int] = []
        for job in jobs_to_submit:
            result = client.submit_job(
                command_argv=job["command"],
                workdir=workdir,
                name=job["name"],
                config=job["config"],
                tags=job["tags"],
            )
            job_id = result.job_id
            submitted_ids.append(job_id)

            session.runs.append(
                AblationRunResult(
                    run_id=result.run_id,
                    job_id=job_id,
                    condition=job["condition"],
                    replicate=job["replicate"],
                    seed=job["seed"],
                    status="queued",
                )
            )

        session.save()

        console.print(f"\n[green]Submitted {len(submitted_ids)} jobs[/green]")
        console.print(f"  [dim]Job IDs:[/dim] {submitted_ids[0]}-{submitted_ids[-1]}")
        console.print(f"\nRank: [cyan]whirr ablate rank {session.name}[/cyan]")

    except WhirrClientError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e
    finally:
        client.close()
