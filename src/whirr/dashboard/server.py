# Copyright (c) Syntropy Systems
"""whirr dashboard - FastAPI server with HTMX + Tailwind."""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Iterator, Optional, Protocol, cast

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import TypeAdapter, ValidationError

import whirr

if TYPE_CHECKING:
    import sqlite3

    from whirr.client import WhirrClient
    from whirr.models.api import JobResponse, RunResponse, WorkerResponse

# Setup paths
DASHBOARD_DIR = Path(__file__).parent
TEMPLATES_DIR = DASHBOARD_DIR / "templates"
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 60 * SECONDS_PER_MINUTE
SECONDS_PER_DAY = 24 * SECONDS_PER_HOUR

app = FastAPI(title="whirr dashboard", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
_JSON_VALUE_ADAPTER: TypeAdapter[object] = TypeAdapter(object)


def _is_remote_mode() -> bool:
    """Check if running in remote mode (connected to whirr server)."""
    return bool(os.environ.get("WHIRR_SERVER_URL"))


def _get_server_url() -> str:
    """Get the server URL from environment."""
    url = os.environ.get("WHIRR_SERVER_URL")
    if not url:
        msg = "WHIRR_SERVER_URL not set"
        raise RuntimeError(msg)
    return url


@contextmanager
def _get_client() -> Iterator[WhirrClient]:
    """Get an HTTP client for remote mode."""
    from whirr.client import WhirrClient

    client = WhirrClient(_get_server_url())
    try:
        yield client
    finally:
        client.close()


def _get_db() -> sqlite3.Connection:
    """Get a database connection for local mode."""
    from whirr.config import get_db_path, require_whirr_dir
    from whirr.db import get_connection

    whirr_dir = require_whirr_dir()
    db_path = get_db_path(whirr_dir)
    return get_connection(db_path)


# Data access functions that work in both modes
def get_active_jobs_data() -> list[JobResponse]:
    """Get active jobs from either local DB or remote server."""
    if _is_remote_mode():
        with _get_client() as client:
            return client.get_active_jobs()
    else:
        from whirr.db import get_active_jobs

        conn = _get_db()
        try:
            return get_active_jobs(conn)  # type: ignore[return-value]
        finally:
            conn.close()


def get_workers_data() -> list[WorkerResponse]:
    """Get workers from either local DB or remote server."""
    if _is_remote_mode():
        with _get_client() as client:
            return client.get_workers()
    else:
        from whirr.db import get_workers

        conn = _get_db()
        try:
            return get_workers(conn)  # type: ignore[return-value]
        finally:
            conn.close()


def get_runs_data(
    status: str | None = None, tag: str | None = None, limit: int = 50
) -> list[RunResponse]:
    """Get runs from either local DB or remote server."""
    if _is_remote_mode():
        with _get_client() as client:
            return client.get_runs(status=status, tag=tag, limit=limit)
    else:
        from whirr.db import get_runs

        conn = _get_db()
        try:
            return get_runs(conn, status=status, tag=tag, limit=limit)  # type: ignore[return-value]
        finally:
            conn.close()


def cancel_job_data(job_id: int) -> None:
    """Cancel a job via either local DB or remote server."""
    if _is_remote_mode():
        with _get_client() as client:
            client.cancel_job(job_id)
    else:
        from whirr.db import cancel_job

        conn = _get_db()
        try:
            cancel_job(conn, job_id)
        finally:
            conn.close()


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
            return _JSON_VALUE_ADAPTER.validate_json(value)
        except ValidationError:
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
    jobs = get_active_jobs_data()
    workers = get_workers_data()
    runs = get_runs_data(limit=5)

    # Calculate stats
    running_count = len([j for j in jobs if j.status == "running"])
    queued_count = len([j for j in jobs if j.status == "queued"])
    completed_runs = get_runs_data(status="completed", limit=100)
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


@app.get("/queue", response_class=HTMLResponse)
async def queue(request: Request) -> HTMLResponse:
    """Render the job queue view."""
    jobs = get_active_jobs_data()
    return templates.TemplateResponse(
        "queue.html",
        {
            "request": request,
            "jobs": jobs,
        },
    )


@app.get("/runs", response_class=HTMLResponse)
async def runs_list(
    request: Request,
    status: Optional[str] = None,
    tag: Optional[str] = None,
    limit: Annotated[int, Query(le=100)] = 20,
) -> HTMLResponse:
    """Render the runs history view."""
    runs = get_runs_data(status=status, tag=tag, limit=limit)
    return templates.TemplateResponse(
        "runs.html",
        {
            "request": request,
            "runs": runs,
            "filter_status": status,
            "filter_tag": tag,
        },
    )


@app.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_detail(request: Request, run_id: str) -> HTMLResponse:
    """Render the run detail view."""
    from whirr.run import read_meta, read_metrics

    if _is_remote_mode():
        with _get_client() as client:
            run = client.get_run(run_id)
            if not run:
                return templates.TemplateResponse(
                    "run_detail.html",
                    {
                        "request": request,
                        "run": None,
                        "error": f"Run {run_id} not found",
                    },
                )

            # Get metrics via API
            try:
                metrics_records = client.get_metrics(run_id)
                metrics = [
                    record.model_dump(by_alias=True, exclude_none=True)
                    for record in metrics_records
                ]
            except Exception:
                metrics = []

            # Read meta from filesystem if accessible
            run_dir = Path(run.run_dir) if run.run_dir else None
            meta = {}
            if run_dir and run_dir.exists():
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
    else:
        # Local mode - use database directly
        runs = get_runs_data(limit=1000)
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


@app.post("/jobs/{job_id}/cancel")
async def cancel_job_endpoint(job_id: int) -> RedirectResponse:
    """Cancel a job."""
    cancel_job_data(job_id)
    return RedirectResponse(url="/queue", status_code=303)


# HTMX Partials - for live updates

@app.get("/partials/workers", response_class=HTMLResponse)
async def workers_partial(request: Request) -> HTMLResponse:
    """Render the workers status partial for HTMX polling."""
    workers = get_workers_data()
    jobs = get_active_jobs_data()

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


@app.get("/partials/jobs", response_class=HTMLResponse)
async def jobs_partial(request: Request) -> HTMLResponse:
    """Render the jobs list partial for HTMX polling."""
    jobs = get_active_jobs_data()
    return templates.TemplateResponse(
        "partials/jobs.html",
        {
            "request": request,
            "jobs": jobs,
        },
    )


@app.get("/partials/stats", response_class=HTMLResponse)
async def stats_partial(request: Request) -> HTMLResponse:
    """Render the stats partial for HTMX polling."""
    jobs = get_active_jobs_data()
    workers = get_workers_data()
    completed_runs = get_runs_data(status="completed", limit=100)

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


@app.get("/partials/recent-runs", response_class=HTMLResponse)
async def recent_runs_partial(request: Request) -> HTMLResponse:
    """Render the recent runs partial for HTMX polling."""
    runs = get_runs_data(limit=5)
    return templates.TemplateResponse(
        "partials/recent_runs.html",
        {
            "request": request,
            "runs": runs,
        },
    )


def create_app() -> FastAPI:
    """Create and return the FastAPI app."""
    return app
