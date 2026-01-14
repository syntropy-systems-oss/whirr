"""whirr ablate init command."""

import typer
from rich.console import Console

from whirr.ablate import create_session, session_exists
from whirr.config import require_whirr_dir

console = Console()


def init(
    name: str = typer.Argument(..., help="Name for the ablation session"),
    metric: str = typer.Option(
        ...,
        "--metric",
        "-m",
        help="Metric to track (e.g., 'win', 'loss', 'accuracy')",
    ),
) -> None:
    """
    Initialize a new ablation study session.

    Example:
        whirr ablate init weird-behavior --metric win
    """
    try:
        whirr_dir = require_whirr_dir()
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if session_exists(name, whirr_dir):
        console.print(f"[red]Error:[/red] Session '{name}' already exists")
        raise typer.Exit(1)

    try:
        session = create_session(name, metric, whirr_dir)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    console.print(f"[green]Created ablation session:[/green] {name}")
    console.print(f"  [dim]session_id:[/dim] {session.session_id}")
    console.print(f"  [dim]metric:[/dim] {metric}")
    console.print(f"  [dim]seed_base:[/dim] {session.seed_base}")
    console.print("\nNext steps:")
    console.print(f"  1. Add deltas: [cyan]whirr ablate add {name} temperature=0[/cyan]")
    console.print(
        f"  2. Run study:  [cyan]whirr ablate run {name} -- python eval.py --seed {{{{seed}}}} --cfg {{{{cfg_path}}}}[/cyan]"
    )
