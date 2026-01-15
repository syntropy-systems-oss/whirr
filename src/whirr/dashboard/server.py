# Copyright (c) Syntropy Systems
"""whirr dashboard - FastAPI server with HTMX + Tailwind."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Protocol, cast

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import whirr
from whirr.config import get_db_path, require_whirr_dir
from whirr.db import (
    cancel_job,
    get_active_jobs,
    get_connection,
    get_runs,
    get_workers,
)
from whirr.run import read_meta, read_metrics

if TYPE_CHECKING:
    import sqlite3

# Setup paths
DASHBOARD_DIR = Path(__file__).parent
TEMPLATES_DIR = DASHBOARD_DIR / "templates"
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 60 * SECONDS_PER_MINUTE
SECONDS_PER_DAY = 24 * SECONDS_PER_HOUR

app = FastAPI(title="whirr dashboard", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_db() -> sqlite3.Connection:
    """Get a database connection."""
    whirr_dir = require_whirr_dir()
    db_path = get_db_path(whirr_dir)
    return get_connection(db_path)


def format_duration(seconds: float | None) -> str:
    """Format seconds to human readable duration."""
    if seconds is None:
        return "-"
    if seconds < SECONDS_PER_MINUTE:
        return f"{int(seconds)}s"
    if seconds < SECONDS_PER_HOUR:
        mins = int(seconds // SECONDS_PER_MINUTE)
        secs = int(seconds % SECONDS_PER_MINUTE)
        return f"{mins}m {secs}s"
    hours = int(seconds // SECONDS_PER_HOUR)
    mins = int((seconds % SECONDS_PER_HOUR) // SECONDS_PER_MINUTE)
    return f"{hours}h {mins}m"


def format_time_ago(timestamp: str | None) -> str:
    """Format timestamp as time ago."""
    if not timestamp:
        return "-"
    try:
        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - ts
        seconds = int(delta.total_seconds())

        if seconds < SECONDS_PER_MINUTE:
            return f"{seconds}s ago"
        if seconds < SECONDS_PER_HOUR:
            return f"{seconds // SECONDS_PER_MINUTE}m ago"
        if seconds < SECONDS_PER_DAY:
            return f"{seconds // SECONDS_PER_HOUR}h ago"
        return f"{seconds // SECONDS_PER_DAY}d ago"
    except ValueError:
        return "-"


def deserialize_json_field(value: object) -> object:
    """Deserialize a JSON field if it's a string."""
    if isinstance(value, str):
        try:
            return cast("object", json.loads(value))
        except (json.JSONDecodeError, TypeError):
            return value
    return value


class _TemplateEnv(Protocol):
    filters: dict[str, object]
    globals: dict[str, object]


# Add custom filters to Jinja2
templates_env = cast("_TemplateEnv", templates.env)
templates_env.filters["format_duration"] = format_duration
templates_env.filters["format_time_ago"] = format_time_ago
templates_env.filters["deserialize_json"] = deserialize_json_field

# Add global template variables
templates_env.globals["version"] = whirr.__version__


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Render the main dashboard view."""
    conn = get_db()
    try:
        jobs = get_active_jobs(conn)
        workers = get_workers(conn)
        runs = get_runs(conn, limit=5)

        # Calculate stats
        running_count = len([j for j in jobs if j.status == "running"])
        queued_count = len([j for j in jobs if j.status == "queued"])
        completed_runs = get_runs(conn, status="completed", limit=100)
        workers_online = len([w for w in workers if w.status != "offline"])

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "jobs": jobs,
                "workers": workers,
                "runs": runs,
                "stats": {
                    "running": running_count,
                    "queued": queued_count,
                    "completed": len(completed_runs),
                    "workers_online": workers_online,
                    "workers_total": len(workers),
                },
            },
        )
    finally:
        conn.close()


@app.get("/queue", response_class=HTMLResponse)
async def queue(request: Request) -> HTMLResponse:
    """Render the job queue view."""
    conn = get_db()
    try:
        jobs = get_active_jobs(conn)
        return templates.TemplateResponse(
            "queue.html",
            {
                "request": request,
                "jobs": jobs,
            },
        )
    finally:
        conn.close()


@app.get("/runs", response_class=HTMLResponse)
async def runs_list(
    request: Request,
    status: str | None = None,
    tag: str | None = None,
    limit: Annotated[int, Query(le=100)] = 20,
) -> HTMLResponse:
    """Render the runs history view."""
    conn = get_db()
    try:
        runs = get_runs(conn, status=status, tag=tag, limit=limit)
        return templates.TemplateResponse(
            "runs.html",
            {
                "request": request,
                "runs": runs,
                "filter_status": status,
                "filter_tag": tag,
            },
        )
    finally:
        conn.close()


@app.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_detail(request: Request, run_id: str) -> HTMLResponse:
    """Render the run detail view."""
    conn = get_db()
    try:
        # Get run from database
        runs = get_runs(conn, limit=1000)
        run = next((r for r in runs if r.id == run_id), None)

        if not run:
            return templates.TemplateResponse(
                "run_detail.html",
                {
                    "request": request,
                    "run": None,
                    "error": f"Run {run_id} not found",
                },
            )

        # Read metrics from filesystem
        run_dir = Path(run.run_dir) if run.run_dir else None
        metrics = []
        meta = {}

        if run_dir and run_dir.exists():
            metrics_path = run_dir / "metrics.jsonl"
            if metrics_path.exists():
                metrics_records = read_metrics(metrics_path)
                metrics = [
                    record.model_dump(by_alias=True, exclude_none=True)
                    for record in metrics_records
                ]

            meta = read_meta(run_dir)

        return templates.TemplateResponse(
            "run_detail.html",
            {
                "request": request,
                "run": run,
                "metrics": metrics,
                "meta": meta,
            },
        )
    finally:
        conn.close()


@app.post("/jobs/{job_id}/cancel")
async def cancel_job_endpoint(job_id: int) -> RedirectResponse:
    """Cancel a job."""
    conn = get_db()
    try:
        _ = cancel_job(conn, job_id)
        return RedirectResponse(url="/queue", status_code=303)
    finally:
        conn.close()


# HTMX Partials - for live updates

@app.get("/partials/workers", response_class=HTMLResponse)
async def workers_partial(request: Request) -> HTMLResponse:
    """Render the workers status partial for HTMX polling."""
    conn = get_db()
    try:
        workers = get_workers(conn)
        jobs = get_active_jobs(conn)

        # Map job_id to job for worker display
        job_map = {j.id: j for j in jobs}

        return templates.TemplateResponse(
            "partials/workers.html",
            {
                "request": request,
                "workers": workers,
                "job_map": job_map,
            },
        )
    finally:
        conn.close()


@app.get("/partials/jobs", response_class=HTMLResponse)
async def jobs_partial(request: Request) -> HTMLResponse:
    """Render the jobs list partial for HTMX polling."""
    conn = get_db()
    try:
        jobs = get_active_jobs(conn)
        return templates.TemplateResponse(
            "partials/jobs.html",
            {
                "request": request,
                "jobs": jobs,
            },
        )
    finally:
        conn.close()


@app.get("/partials/stats", response_class=HTMLResponse)
async def stats_partial(request: Request) -> HTMLResponse:
    """Render the stats partial for HTMX polling."""
    conn = get_db()
    try:
        jobs = get_active_jobs(conn)
        workers = get_workers(conn)
        completed_runs = get_runs(conn, status="completed", limit=100)

        running_count = len([j for j in jobs if j.status == "running"])
        queued_count = len([j for j in jobs if j.status == "queued"])
        workers_online = len([w for w in workers if w.status != "offline"])

        return templates.TemplateResponse(
            "partials/stats.html",
            {
                "request": request,
                "stats": {
                    "running": running_count,
                    "queued": queued_count,
                    "completed": len(completed_runs),
                    "workers_online": workers_online,
                    "workers_total": len(workers),
                },
            },
        )
    finally:
        conn.close()


@app.get("/partials/recent-runs", response_class=HTMLResponse)
async def recent_runs_partial(request: Request) -> HTMLResponse:
    """Render the recent runs partial for HTMX polling."""
    conn = get_db()
    try:
        runs = get_runs(conn, limit=5)
        return templates.TemplateResponse(
            "partials/recent_runs.html",
            {
                "request": request,
                "runs": runs,
            },
        )
    finally:
        conn.close()


def create_app() -> FastAPI:
    """Create and return the FastAPI app."""
    return app
