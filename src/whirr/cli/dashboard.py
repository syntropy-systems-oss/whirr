# Copyright (c) Syntropy Systems
"""Dashboard command - start the web UI."""

import os
from typing import Optional

import typer
from rich.console import Console

console = Console()


def dashboard(
    port: int = typer.Option(8265, "--port", "-p", help="Port to run the dashboard on"),
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="Host to bind to"),
    server: Optional[str] = typer.Option(
        None, "--server", "-s", help="whirr server URL (e.g., http://localhost:8080)"
    ),
) -> None:
    """Start the whirr dashboard web UI."""
    try:
        import uvicorn
    except ImportError as e:
        error_message = "[red]Dashboard dependencies not installed.[/red]"
        install_message = "Install with: [cyan]pip install whirr[dashboard][/cyan]"
        console.print(f"{error_message}\n{install_message}")
        raise typer.Exit(1) from e

    # If server URL provided, use remote mode
    if server:
        os.environ["WHIRR_SERVER_URL"] = server
        console.print(f"[bold]whirr dashboard[/bold] (remote mode)")
        console.print(f"  Server: [cyan]{server}[/cyan]")
    else:
        from whirr.config import require_whirr_dir

        # Ensure we're in a whirr project for local mode
        try:
            _ = require_whirr_dir()
        except SystemExit as e:
            error_message = "[red]Not in a whirr project.[/red]"
            hint_message = (
                "Run [cyan]whirr init[/cyan] first, or use "
                "[cyan]--server URL[/cyan] for remote mode."
            )
            console.print(f"{error_message}\n{hint_message}")
            raise typer.Exit(1) from e
        console.print(f"[bold]whirr dashboard[/bold] (local mode)")

    console.print(f"  Dashboard: [cyan]http://{host}:{port}[/cyan]")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")

    uvicorn.run(
        "whirr.dashboard:app",
        host=host,
        port=port,
        log_level="warning",
    )
