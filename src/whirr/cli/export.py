"""Export command - export runs to CSV/JSON."""

import csv
import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from whirr.config import get_db_path, require_whirr_dir
from whirr.db import get_connection, get_runs
from whirr.run import read_meta, read_metrics

console = Console()


def export(
    output: Path = typer.Argument(..., help="Output file path (.csv or .json)"),
    run_id: Optional[str] = typer.Option(None, "--run", "-r", help="Export specific run"),
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status"),
    tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Filter by tag"),
    include_metrics: bool = typer.Option(
        False, "--metrics", "-m", help="Include full metrics history (JSON only)"
    ),
    limit: int = typer.Option(100, "--limit", "-l", help="Max runs to export"),
):
    """Export runs to CSV or JSON format.

    Examples:
        whirr export runs.csv
        whirr export runs.json --metrics
        whirr export --run abc123 run.json
    """
    whirr_dir = require_whirr_dir()
    db_path = get_db_path(whirr_dir)
    conn = get_connection(db_path)

    # Determine format from extension
    suffix = output.suffix.lower()
    if suffix not in [".csv", ".json"]:
        console.print("[red]Output must be .csv or .json[/red]")
        raise typer.Exit(1)

    if include_metrics and suffix == ".csv":
        console.print("[yellow]Warning: --metrics only supported for JSON export[/yellow]")
        include_metrics = False

    try:
        # Fetch runs
        all_runs = get_runs(conn, status=status, tag=tag, limit=limit)

        # Filter by specific run ID if provided
        if run_id:
            all_runs = [r for r in all_runs if r["id"].startswith(run_id)]
            if not all_runs:
                console.print(f"[red]Run not found: {run_id}[/red]")
                raise typer.Exit(1)

        if not all_runs:
            console.print("[yellow]No runs to export[/yellow]")
            raise typer.Exit(0)

        # Build export data
        export_data = []
        for run in all_runs:
            run_data = {
                "id": run["id"],
                "name": run.get("name"),
                "status": run["status"],
                "started_at": run.get("started_at"),
                "finished_at": run.get("finished_at"),
                "duration_s": run.get("duration_s"),
                "exit_code": run.get("exit_code"),
                "tags": run.get("tags"),
                "run_dir": run.get("run_dir"),
            }

            # Parse config
            config = run.get("config")
            if isinstance(config, str):
                config = json.loads(config)
            run_data["config"] = config or {}

            # Get summary metrics from meta.json
            run_dir = Path(run["run_dir"]) if run.get("run_dir") else None
            summary = {}
            metrics_history = []

            if run_dir and run_dir.exists():
                meta = read_meta(run_dir)
                if meta and meta.get("summary"):
                    summary = meta["summary"]

                if include_metrics:
                    metrics_path = run_dir / "metrics.jsonl"
                    if metrics_path.exists():
                        metrics_history = read_metrics(metrics_path)

            run_data["summary"] = summary
            if include_metrics:
                run_data["metrics"] = metrics_history

            export_data.append(run_data)

        # Write output
        if suffix == ".json":
            with open(output, "w") as f:
                json.dump(export_data, f, indent=2, default=str)
        else:
            # CSV - flatten config and summary
            fieldnames = [
                "id",
                "name",
                "status",
                "started_at",
                "finished_at",
                "duration_s",
                "exit_code",
                "tags",
            ]

            # Collect all config and summary keys
            config_keys = set()
            summary_keys = set()
            for run_data in export_data:
                config_keys.update(run_data.get("config", {}).keys())
                summary_keys.update(run_data.get("summary", {}).keys())

            config_fields = [f"config.{k}" for k in sorted(config_keys)]
            summary_fields = [f"summary.{k}" for k in sorted(summary_keys)]
            fieldnames.extend(config_fields)
            fieldnames.extend(summary_fields)

            with open(output, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()

                for run_data in export_data:
                    row = {
                        "id": run_data["id"],
                        "name": run_data.get("name"),
                        "status": run_data["status"],
                        "started_at": run_data.get("started_at"),
                        "finished_at": run_data.get("finished_at"),
                        "duration_s": run_data.get("duration_s"),
                        "exit_code": run_data.get("exit_code"),
                        "tags": json.dumps(run_data.get("tags"))
                        if run_data.get("tags")
                        else None,
                    }

                    # Flatten config
                    for k, v in run_data.get("config", {}).items():
                        row[f"config.{k}"] = v

                    # Flatten summary
                    for k, v in run_data.get("summary", {}).items():
                        row[f"summary.{k}"] = v

                    writer.writerow(row)

        console.print(
            f"[green]Exported {len(export_data)} run(s) to {output}[/green]"
        )

    finally:
        conn.close()
