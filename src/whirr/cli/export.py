# Copyright (c) Syntropy Systems
"""Export command - export runs to CSV/JSON."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING, cast

import typer
from pydantic import TypeAdapter
from rich.console import Console

from whirr.config import get_db_path, require_whirr_dir
from whirr.db import get_connection, get_runs
from whirr.models.base import JSONValue
from whirr.run import read_meta, read_metrics

if TYPE_CHECKING:
    from whirr.models.run import RunMetricRecord

console = Console()
_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, JSONValue])
_EXPORT_ADAPTER = TypeAdapter(list[dict[str, JSONValue]])


def _to_csv_value(value: JSONValue | None) -> str | float | list[str] | None:
    if value is None:
        return None
    if isinstance(value, float):
        return value
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, dict):
        return _JSON_OBJECT_ADAPTER.dump_json(value).decode("utf-8")
    return str(value)


def export(
    output: Path = typer.Argument(..., help="Output file path (.csv or .json)"),
    run_id: str | None = typer.Option(None, "--run", "-r", help="Export specific run"),
    status: str | None = typer.Option(None, "--status", "-s", help="Filter by status"),
    tag: str | None = typer.Option(None, "--tag", "-t", help="Filter by tag"),
    include_metrics: bool = typer.Option(
        False, "--metrics", "-m", help="Include full metrics history (JSON only)"
    ),
    limit: int = typer.Option(100, "--limit", "-l", help="Max runs to export"),
) -> None:
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
        console.print(
            "[yellow]Warning: --metrics only supported for JSON export[/yellow]"
        )
        include_metrics = False

    try:
        # Fetch runs
        all_runs = get_runs(conn, status=status, tag=tag, limit=limit)

        # Filter by specific run ID if provided
        if run_id:
            all_runs = [r for r in all_runs if r.id.startswith(run_id)]
            if not all_runs:
                console.print(f"[red]Run not found: {run_id}[/red]")
                raise typer.Exit(1)

        if not all_runs:
            console.print("[yellow]No runs to export[/yellow]")
            raise typer.Exit(0)

        # Build export data
        export_data: list[dict[str, JSONValue]] = []
        for run in all_runs:
            tags_value = cast("JSONValue", run.tags) if run.tags is not None else None
            run_data: dict[str, JSONValue] = {
                "id": run.id,
                "name": run.name,
                "status": run.status,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "duration_s": run.duration_seconds,
                "exit_code": None,
                "tags": tags_value,
                "run_dir": run.run_dir,
            }

            # Parse config
            run_data["config"] = run.config.values if run.config else {}

            # Get summary metrics from meta.json
            run_dir = Path(run.run_dir) if run.run_dir else None
            summary: dict[str, JSONValue] = {}
            metrics_history: list[RunMetricRecord] = []

            if run_dir and run_dir.exists():
                meta = read_meta(run_dir)
                if meta and meta.summary:
                    summary = meta.summary.values

                if include_metrics:
                    metrics_path = run_dir / "metrics.jsonl"
                    if metrics_path.exists():
                        metrics_history = read_metrics(metrics_path)

            run_data["summary"] = summary
            if include_metrics:
                metrics_payload = [
                    record.model_dump(by_alias=True, exclude_none=True)
                    for record in metrics_history
                ]
                run_data["metrics"] = cast("JSONValue", metrics_payload)

            export_data.append(run_data)

        # Write output
        if suffix == ".json":
            _ = output.write_bytes(_EXPORT_ADAPTER.dump_json(export_data, indent=2))
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
            config_keys: set[str] = set()
            summary_keys: set[str] = set()
            for run_data in export_data:
                config_data = cast("dict[str, JSONValue]", run_data.get("config", {}))
                config_keys.update(config_data.keys())
                summary_data = cast("dict[str, JSONValue]", run_data.get("summary", {}))
                summary_keys.update(summary_data.keys())

            config_fields = [f"config.{k}" for k in sorted(config_keys)]
            summary_fields = [f"summary.{k}" for k in sorted(summary_keys)]
            fieldnames.extend(config_fields)
            fieldnames.extend(summary_fields)

            with output.open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()

                for run_data in export_data:
                    row: dict[str, str | float | list[str] | None] = {
                        "id": _to_csv_value(run_data.get("id")),
                        "name": _to_csv_value(run_data.get("name")),
                        "status": _to_csv_value(run_data.get("status")),
                        "started_at": _to_csv_value(run_data.get("started_at")),
                        "finished_at": _to_csv_value(run_data.get("finished_at")),
                        "duration_s": _to_csv_value(run_data.get("duration_s")),
                        "exit_code": _to_csv_value(run_data.get("exit_code")),
                        "tags": _to_csv_value(run_data.get("tags")),
                    }

                    # Flatten config
                    config_data = cast(
                        "dict[str, JSONValue]",
                        run_data.get("config", {}),
                    )
                    for k, v in config_data.items():
                        row[f"config.{k}"] = _to_csv_value(v)

                    # Flatten summary
                    summary_data = cast(
                        "dict[str, JSONValue]",
                        run_data.get("summary", {}),
                    )
                    for k, v in summary_data.items():
                        row[f"summary.{k}"] = _to_csv_value(v)

                    writer.writerow(row)

        console.print(
            f"[green]Exported {len(export_data)} run(s) to {output}[/green]"
        )

    finally:
        conn.close()
