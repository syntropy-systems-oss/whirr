# Copyright (c) Syntropy Systems
"""whirr ablate add command."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from whirr.ablate import load_session_by_name
from whirr.config import require_whirr_dir
from whirr.models.ablation import ConfigValue, FileValue

console = Console()


def parse_value(value: str, project_root: Path) -> ConfigValue:
    """Parse a value string, handling @file references.

    - @path/to/file -> FileValue with path and inlined text
    - 123 -> int
    - 1.5 -> float
    - other -> str
    """
    if value.startswith("@"):
        file_path = value[1:]
        path = Path(file_path)

        # Resolve relative to project root if not absolute
        full_path = project_root / path if not path.is_absolute() else path

        if not full_path.exists():
            msg = f"File not found: {full_path}"
            raise FileNotFoundError(msg)

        # Read content without stripping
        text = full_path.read_text()

        # Store path relative to project root
        try:
            rel_path = full_path.relative_to(project_root)
        except ValueError:
            # File is outside project root, use absolute path
            rel_path = full_path

        return FileValue(path=str(rel_path), text=text)

    # Try numeric parsing
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def add(
    name: str = typer.Argument(..., help="Session name"),
    deltas: list[str] = typer.Argument(..., help="Delta(s) in key=value format"),
    delta_name: Optional[str] = typer.Option(
        None,
        "--name",
        "-n",
        help="Name for this delta (defaults to first key name)",
    ),
) -> None:
    """Add a delta (parameter change) to an ablation session.

    Examples:
        whirr ablate add weird-behavior temperature=0
        whirr ablate add weird-behavior system=@prompts/v2.txt
        whirr ablate add weird-behavior lr=0.001 batch_size=64 --name "high-lr"

    """
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

    # Get project root (parent of .whirr)
    project_root = whirr_dir.parent

    # Parse key=value pairs
    changes: dict[str, ConfigValue] = {}
    for delta in deltas:
        if "=" not in delta:
            message = f"[red]Error:[/red] Invalid delta format: '{delta}'"
            console.print(f"{message} (expected key=value)")
            raise typer.Exit(1)

        key, value = delta.split("=", 1)
        try:
            changes[key] = parse_value(value, project_root)
        except FileNotFoundError as e:
            console.print(f"[red]Error:[/red] {e}")
            raise typer.Exit(1) from e

    # Determine delta name
    resolved_name = delta_name if delta_name is not None else next(iter(changes))

    if resolved_name in session.deltas:
        console.print(
            f"[yellow]Warning:[/yellow] Overwriting existing delta '{resolved_name}'"
        )

    session.deltas[resolved_name] = changes
    session.save()

    console.print(f"[green]Added delta:[/green] {resolved_name}")
    for k, v in changes.items():
        if isinstance(v, FileValue):
            console.print(f"  [dim]{k}=[/dim]@{v.path}")
        else:
            console.print(f"  [dim]{k}=[/dim]{v}")
