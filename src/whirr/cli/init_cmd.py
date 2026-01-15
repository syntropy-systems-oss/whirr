# Copyright (c) Syntropy Systems
"""whirr init command."""

from pathlib import Path

import typer
import yaml
from rich.console import Console

from whirr.db import init_db

console = Console()


def init(
    path: Path = typer.Argument(
        Path(),
        help="Directory to initialize (default: current directory)",
    ),
) -> None:
    """Initialize a new whirr project.

    Creates a .whirr directory with configuration and database.
    """
    target = path.resolve()
    whirr_dir = target / ".whirr"

    if whirr_dir.exists():
        console.print(f"[yellow]Already initialized:[/yellow] {whirr_dir}")
        return

    # Create directory structure
    whirr_dir.mkdir(parents=True)
    runs_dir = whirr_dir / "runs"
    runs_dir.mkdir()

    # Create default config
    config = {
        "heartbeat_interval": 30,
        "heartbeat_timeout": 120,
        "kill_grace_period": 10,
        "poll_interval": 5,
    }

    config_path = whirr_dir / "config.yaml"
    with config_path.open("w") as f:
        yaml.dump(config, f, default_flow_style=False)

    # Initialize database
    db_path = whirr_dir / "whirr.db"
    init_db(db_path)

    console.print(f"[green]Initialized whirr project:[/green] {whirr_dir}")
    console.print(f"  [dim]config:[/dim] {config_path}")
    console.print(f"  [dim]database:[/dim] {db_path}")
    console.print(f"  [dim]runs:[/dim] {runs_dir}")
