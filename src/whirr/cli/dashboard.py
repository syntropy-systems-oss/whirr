# Copyright (c) Syntropy Systems
"""Dashboard command - start the web UI."""

import typer
from rich.console import Console

console = Console()


def dashboard(
    port: int = typer.Option(8265, "--port", "-p", help="Port to run the dashboard on"),
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host to bind to"),
) -> None:
    """Start the whirr dashboard web UI."""
    try:
        import uvicorn
    except ImportError as e:
        error_message = "[red]Dashboard dependencies not installed.[/red]"
        install_message = "Install with: [cyan]pip install whirr[dashboard][/cyan]"
        console.print(f"{error_message}\n{install_message}")
        raise typer.Exit(1) from e

    from whirr.config import require_whirr_dir

    # Ensure we're in a whirr project
    try:
        _ = require_whirr_dir()
    except SystemExit as e:
        error_message = "[red]Not in a whirr project.[/red]"
        hint_message = "Run [cyan]whirr init[/cyan] first."
        console.print(f"{error_message}\n{hint_message}")
        raise typer.Exit(1) from e

    console.print(f"[bold]whirr dashboard[/bold] starting at [cyan]http://{host}:{port}[/cyan]")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")

    uvicorn.run(
        "whirr.dashboard:app",
        host=host,
        port=port,
        log_level="warning",
    )
