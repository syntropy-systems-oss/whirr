"""whirr dashboard - FastAPI server with HTMX + Tailwind."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import whirr
from whirr.config import get_db_path, require_whirr_dir
from whirr.db import (
    get_active_jobs,
    get_connection,
    get_job,
    get_runs,
    get_workers,
    cancel_job,
)
from whirr.run import read_metrics, read_meta

# Setup paths
DASHBOARD_DIR = Path(__file__).parent
TEMPLATES_DIR = DASHBOARD_DIR / "templates"

app = FastAPI(title="whirr dashboard", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_db():
    """Get database connection."""
    whirr_dir = require_whirr_dir()
    db_path = get_db_path(whirr_dir)
    return get_connection(db_path)


def format_duration(seconds: Optional[float]) -> str:
    """Format seconds to human readable duration."""
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins}m {secs}s"
    else:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h {mins}m"


def format_time_ago(timestamp: Optional[str]) -> str:
    """Format timestamp as time ago."""
    if not timestamp:
        return "-"
    try:
        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - ts
        seconds = int(delta.total_seconds())

        if seconds < 60:
            return f"{seconds}s ago"
        elif seconds < 3600:
            return f"{seconds // 60}m ago"
        elif seconds < 86400:
            return f"{seconds // 3600}h ago"
        else:
            return f"{seconds // 86400}d ago"
    except Exception:
        return "-"


def deserialize_json_field(value):
    """Deserialize a JSON field if it's a string."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    return value


# Add custom filters to Jinja2
templates.env.filters["format_duration"] = format_duration
templates.env.filters["format_time_ago"] = format_time_ago
templates.env.filters["deserialize_json"] = deserialize_json_field

# Add global template variables
templates.env.globals["version"] = whirr.__version__


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard view."""
    conn = get_db()
    try:
        jobs = get_active_jobs(conn)
        workers = get_workers(conn)
        runs = get_runs(conn, limit=5)

        # Calculate stats
        running_count = len([j for j in jobs if j["status"] == "running"])
        queued_count = len([j for j in jobs if j["status"] == "queued"])
        completed_runs = get_runs(conn, status="completed", limit=100)
        workers_online = len([w for w in workers if w["status"] != "offline"])

        return templates.TemplateResponse("index.html", {
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
            }
        })
    finally:
        conn.close()


@app.get("/queue", response_class=HTMLResponse)
async def queue(request: Request):
    """Job queue view."""
    conn = get_db()
    try:
        jobs = get_active_jobs(conn)
        return templates.TemplateResponse("queue.html", {
            "request": request,
            "jobs": jobs,
        })
    finally:
        conn.close()


@app.get("/runs", response_class=HTMLResponse)
async def runs_list(
    request: Request,
    status: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = Query(default=20, le=100),
):
    """Runs history view."""
    conn = get_db()
    try:
        runs = get_runs(conn, status=status, tag=tag, limit=limit)
        return templates.TemplateResponse("runs.html", {
            "request": request,
            "runs": runs,
            "filter_status": status,
            "filter_tag": tag,
        })
    finally:
        conn.close()


@app.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_detail(request: Request, run_id: str):
    """Run detail view."""
    conn = get_db()
    try:
        # Get run from database
        runs = get_runs(conn, limit=1000)
        run = next((r for r in runs if r["id"] == run_id), None)

        if not run:
            return templates.TemplateResponse("run_detail.html", {
                "request": request,
                "run": None,
                "error": f"Run {run_id} not found",
            })

        # Read metrics from filesystem
        run_dir = Path(run["run_dir"]) if run.get("run_dir") else None
        metrics = []
        meta = {}

        if run_dir and run_dir.exists():
            metrics_path = run_dir / "metrics.jsonl"
            if metrics_path.exists():
                metrics = read_metrics(metrics_path)

            meta = read_meta(run_dir) or {}

        return templates.TemplateResponse("run_detail.html", {
            "request": request,
            "run": run,
            "metrics": metrics,
            "meta": meta,
        })
    finally:
        conn.close()


@app.post("/jobs/{job_id}/cancel")
async def cancel_job_endpoint(job_id: int):
    """Cancel a job."""
    conn = get_db()
    try:
        cancel_job(conn, job_id)
        return RedirectResponse(url="/queue", status_code=303)
    finally:
        conn.close()


# HTMX Partials - for live updates

@app.get("/partials/workers", response_class=HTMLResponse)
async def workers_partial(request: Request):
    """Workers status partial for HTMX polling."""
    conn = get_db()
    try:
        workers = get_workers(conn)
        jobs = get_active_jobs(conn)

        # Map job_id to job for worker display
        job_map = {j["id"]: j for j in jobs}

        return templates.TemplateResponse("partials/workers.html", {
            "request": request,
            "workers": workers,
            "job_map": job_map,
        })
    finally:
        conn.close()


@app.get("/partials/jobs", response_class=HTMLResponse)
async def jobs_partial(request: Request):
    """Jobs list partial for HTMX polling."""
    conn = get_db()
    try:
        jobs = get_active_jobs(conn)
        return templates.TemplateResponse("partials/jobs.html", {
            "request": request,
            "jobs": jobs,
        })
    finally:
        conn.close()


@app.get("/partials/stats", response_class=HTMLResponse)
async def stats_partial(request: Request):
    """Stats partial for HTMX polling."""
    conn = get_db()
    try:
        jobs = get_active_jobs(conn)
        workers = get_workers(conn)
        completed_runs = get_runs(conn, status="completed", limit=100)

        running_count = len([j for j in jobs if j["status"] == "running"])
        queued_count = len([j for j in jobs if j["status"] == "queued"])
        workers_online = len([w for w in workers if w["status"] != "offline"])

        return templates.TemplateResponse("partials/stats.html", {
            "request": request,
            "stats": {
                "running": running_count,
                "queued": queued_count,
                "completed": len(completed_runs),
                "workers_online": workers_online,
                "workers_total": len(workers),
            }
        })
    finally:
        conn.close()


@app.get("/partials/recent-runs", response_class=HTMLResponse)
async def recent_runs_partial(request: Request):
    """Recent runs partial for HTMX polling."""
    conn = get_db()
    try:
        runs = get_runs(conn, limit=5)
        return templates.TemplateResponse("partials/recent_runs.html", {
            "request": request,
            "runs": runs,
        })
    finally:
        conn.close()


def create_app():
    """Create and return the FastAPI app."""
    return app
