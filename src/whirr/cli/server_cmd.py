"""CLI command for running the whirr server."""

import os
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

console = Console()


def server(
    port: int = typer.Option(8080, "--port", "-p", help="Port to listen on"),
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host to bind to"),
    database_url: Optional[str] = typer.Option(
        None,
        "--database-url",
        envvar="WHIRR_DATABASE_URL",
        help="PostgreSQL connection URL",
    ),
    data_dir: Optional[Path] = typer.Option(
        None,
        "--data-dir",
        "-d",
        envvar="WHIRR_DATA_DIR",
        help="Data directory for runs (shared filesystem)",
    ),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload for development"),
):
    """
    Start the whirr server for multi-machine orchestration.

    The server provides an HTTP API for workers to claim jobs, send heartbeats,
    and report completion. Use PostgreSQL for production deployments.

    Examples:

        # Start server with PostgreSQL
        whirr server --database-url postgresql://user:pass@localhost/whirr

        # Start server with SQLite (for testing)
        whirr server --data-dir /data/whirr

        # Bind to all interfaces (for remote access)
        whirr server --host 0.0.0.0 --port 8080
    """
    try:
        import uvicorn
    except ImportError:
        console.print("[red]Error:[/red] uvicorn is required for server mode.")
        console.print("Install with: pip install whirr[server]")
        raise typer.Exit(1)

    # Validate configuration
    if not database_url and not data_dir:
        # Check for environment variables
        if not os.environ.get("WHIRR_DATABASE_URL"):
            console.print("[red]Error:[/red] No database configuration provided.")
            console.print()
            console.print("Provide one of:")
            console.print("  --database-url postgresql://user:pass@localhost/whirr")
            console.print("  --data-dir /path/to/whirr/data (uses SQLite)")
            console.print("  WHIRR_DATABASE_URL environment variable")
            raise typer.Exit(1)

    # Set environment variables for the app
    if database_url:
        os.environ["WHIRR_DATABASE_URL"] = database_url
    if data_dir:
        os.environ["WHIRR_DATA_DIR"] = str(data_dir)

    console.print("[bold]whirr server[/bold]")
    console.print(f"  Host: {host}")
    console.print(f"  Port: {port}")

    if database_url:
        # Mask password in URL for display
        display_url = database_url
        if "@" in display_url and "://" in display_url:
            parts = display_url.split("@")
            prefix = parts[0].split("://")[0] + "://***:***@"
            display_url = prefix + parts[1]
        console.print(f"  Database: {display_url}")
    else:
        console.print(f"  Database: SQLite (in {data_dir or 'data_dir'})")

    if data_dir:
        console.print(f"  Data Dir: {data_dir}")

    console.print()

    # Import and create app here to handle import errors gracefully
    try:
        from ..server.app import create_app

        # Determine database configuration
        db_path = None
        if not database_url:
            # Use SQLite with data_dir
            actual_data_dir = data_dir or Path(".")
            actual_data_dir.mkdir(parents=True, exist_ok=True)
            db_path = actual_data_dir / "whirr.db"

        app = create_app(
            database_url=database_url,
            db_path=db_path,
            data_dir=data_dir,
        )

        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="info",
        )

    except ImportError as e:
        console.print(f"[red]Error:[/red] Missing dependency: {e}")
        console.print("Install server dependencies with: pip install whirr[server]")
        raise typer.Exit(1)
