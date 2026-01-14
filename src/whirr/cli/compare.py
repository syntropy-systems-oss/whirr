"""Compare command - compare runs side by side."""

from typing import List

import typer
from rich.console import Console
from rich.table import Table

from whirr.config import get_db_path, require_whirr_dir
from whirr.db import get_connection, get_runs
from whirr.run import read_meta

console = Console()


def compare(
    run_ids: List[str] = typer.Argument(..., help="Run IDs to compare (2 or more)"),
    metrics: bool = typer.Option(False, "--metrics", "-m", help="Show final metrics comparison"),
    config: bool = typer.Option(True, "--config/--no-config", help="Show config comparison"),
):
    """Compare multiple runs side by side.

    Example:
        whirr compare abc123 def456 ghi789
    """
    if len(run_ids) < 2:
        console.print("[red]Need at least 2 runs to compare[/red]")
        raise typer.Exit(1)

    whirr_dir = require_whirr_dir()
    db_path = get_db_path(whirr_dir)
    conn = get_connection(db_path)

    try:
        # Fetch all runs
        all_runs = get_runs(conn, limit=1000)
        runs_data = []

        for run_id in run_ids:
            # Find run by ID prefix
            matches = [r for r in all_runs if r["id"].startswith(run_id)]
            if not matches:
                console.print(f"[red]Run not found: {run_id}[/red]")
                raise typer.Exit(1)
            if len(matches) > 1:
                console.print(f"[yellow]Ambiguous ID '{run_id}', using first match[/yellow]")
            runs_data.append(matches[0])

        # Header info
        console.print(f"\n[bold]Comparing {len(runs_data)} runs[/bold]\n")

        # Basic info table
        info_table = Table(show_header=True, header_style="bold")
        info_table.add_column("", style="dim")
        for run in runs_data:
            info_table.add_column(run["name"] or run["id"][:8], style="cyan")

        info_table.add_row("ID", *[r["id"][:12] for r in runs_data])
        info_table.add_row("Status", *[r["status"] for r in runs_data])
        info_table.add_row(
            "Duration",
            *[f"{r['duration_s']:.1f}s" if r.get("duration_s") else "-" for r in runs_data],
        )

        console.print(info_table)

        # Config comparison
        if config:
            console.print("\n[bold]Config[/bold]")

            # Collect all config keys
            all_keys = set()
            configs = []
            for run in runs_data:
                cfg = run.get("config") or {}
                if isinstance(cfg, str):
                    import json

                    cfg = json.loads(cfg)
                configs.append(cfg)
                all_keys.update(cfg.keys())

            if all_keys:
                config_table = Table(show_header=True, header_style="bold")
                config_table.add_column("Key", style="dim")
                for run in runs_data:
                    config_table.add_column(run["name"] or run["id"][:8])

                for key in sorted(all_keys):
                    values = []
                    for cfg in configs:
                        val = cfg.get(key, "-")
                        values.append(str(val))

                    # Highlight differences
                    unique_values = set(v for v in values if v != "-")
                    if len(unique_values) > 1:
                        # Values differ - highlight
                        styled_values = [f"[yellow]{v}[/yellow]" for v in values]
                    else:
                        styled_values = values

                    config_table.add_row(key, *styled_values)

                console.print(config_table)
            else:
                console.print("[dim]No config to compare[/dim]")

        # Metrics comparison
        if metrics:
            console.print("\n[bold]Final Metrics[/bold]")

            # Collect summary metrics from meta.json
            all_metric_keys = set()
            summaries = []

            for run in runs_data:
                from pathlib import Path

                run_dir = Path(run["run_dir"]) if run.get("run_dir") else None
                summary = {}

                if run_dir and run_dir.exists():
                    meta = read_meta(run_dir)
                    if meta and meta.get("summary"):
                        summary = meta["summary"]

                summaries.append(summary)
                all_metric_keys.update(summary.keys())

            if all_metric_keys:
                metrics_table = Table(show_header=True, header_style="bold")
                metrics_table.add_column("Metric", style="dim")
                for run in runs_data:
                    metrics_table.add_column(run["name"] or run["id"][:8])

                for key in sorted(all_metric_keys):
                    values = []
                    numeric_values = []

                    for summary in summaries:
                        val = summary.get(key)
                        if val is not None:
                            if isinstance(val, float):
                                values.append(f"{val:.4f}")
                                numeric_values.append(val)
                            else:
                                values.append(str(val))
                                numeric_values.append(None)
                        else:
                            values.append("-")
                            numeric_values.append(None)

                    # Highlight best value (lowest for loss, highest for accuracy-like)
                    styled_values = values.copy()
                    valid_nums = [v for v in numeric_values if v is not None]

                    if len(valid_nums) >= 2:
                        # Assume lower is better for loss/error metrics, higher for others
                        is_loss = any(
                            term in key.lower() for term in ["loss", "error", "mse", "mae"]
                        )
                        best_val = min(valid_nums) if is_loss else max(valid_nums)

                        for i, num in enumerate(numeric_values):
                            if num == best_val:
                                styled_values[i] = f"[green]{values[i]}[/green]"

                    metrics_table.add_row(key, *styled_values)

                console.print(metrics_table)
            else:
                console.print("[dim]No summary metrics to compare[/dim]")

    finally:
        conn.close()
