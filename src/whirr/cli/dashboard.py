"""Dashboard command - start the web UI."""

import typer
from rich.console import Console

console = Console()


def dashboard(
    port: int = typer.Option(8265, "--port", "-p", help="Port to run the dashboard on"),
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host to bind to"),
):
    """Start the whirr dashboard web UI."""
    try:
        import uvicorn
    except ImportError:
        console.print(
            "[red]Dashboard dependencies not installed.[/red]\n"
            "Install with: [cyan]pip install whirr[dashboard][/cyan]"
        )
        raise typer.Exit(1)

    from whirr.config import require_whirr_dir

    # Ensure we're in a whirr project
    try:
        require_whirr_dir()
    except SystemExit:
        console.print(
            "[red]Not in a whirr project.[/red]\n"
            "Run [cyan]whirr init[/cyan] first."
        )
        raise typer.Exit(1)

    console.print(f"[bold]whirr dashboard[/bold] starting at [cyan]http://{host}:{port}[/cyan]")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")

    uvicorn.run(
        "whirr.dashboard:app",
        host=host,
        port=port,
        log_level="warning",
    )
