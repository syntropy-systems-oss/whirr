"""FastAPI application for the whirr server."""

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from time import sleep
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from ..db import Database, PostgresDatabase, SQLiteDatabase, get_database
from .models import (
    ErrorResponse,
    HeartbeatResponse,
    JobClaim,
    JobClaimResponse,
    JobComplete,
    JobCreate,
    JobFail,
    JobHeartbeat,
    JobResponse,
    MessageResponse,
    RunResponse,
    StatusResponse,
    WorkerRegistration,
    WorkerResponse,
    WorkerUnregister,
)

# Global database instance
_db: Optional[Database] = None
_lease_monitor_thread: Optional[Thread] = None
_shutdown_flag = False


def get_db() -> Database:
    """Get the database instance."""
    if _db is None:
        raise RuntimeError("Database not initialized")
    return _db


def _lease_monitor_loop(db: Database, interval: int = 30) -> None:
    """Background thread to check for expired leases and requeue jobs."""
    global _shutdown_flag
    while not _shutdown_flag:
        try:
            expired = db.requeue_expired_jobs()
            if expired:
                print(f"[lease-monitor] Requeued {len(expired)} jobs with expired leases")
        except Exception as e:
            print(f"[lease-monitor] Error: {e}")
        sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for the FastAPI app."""
    global _lease_monitor_thread, _shutdown_flag

    # Start lease monitor thread
    _shutdown_flag = False
    if _db is not None:
        _lease_monitor_thread = Thread(target=_lease_monitor_loop, args=(_db,), daemon=True)
        _lease_monitor_thread.start()

    yield

    # Shutdown
    _shutdown_flag = True
    if _lease_monitor_thread is not None:
        _lease_monitor_thread.join(timeout=5)


def create_app(
    database_url: Optional[str] = None,
    db_path: Optional[Path] = None,
    data_dir: Optional[Path] = None,
) -> FastAPI:
    """
    Create the FastAPI application.

    Args:
        database_url: PostgreSQL connection URL (preferred for production)
        db_path: SQLite database path (for testing/local)
        data_dir: Directory for run data (shared filesystem)

    Returns:
        Configured FastAPI application
    """
    global _db

    # Initialize database
    if database_url:
        _db = PostgresDatabase(database_url)
    elif db_path:
        _db = SQLiteDatabase(db_path)
    else:
        # Try environment variable
        env_url = os.environ.get("WHIRR_DATABASE_URL")
        if env_url:
            _db = PostgresDatabase(env_url)
        else:
            raise ValueError("No database configuration provided")

    # Initialize schema
    _db.init_schema()

    # Store data_dir for reference
    app_data_dir = data_dir or Path(os.environ.get("WHIRR_DATA_DIR", "."))

    app = FastAPI(
        title="whirr server",
        description="Multi-machine experiment orchestration server",
        version="0.4.0",
        lifespan=lifespan,
    )

    # Store data_dir in app state
    app.state.data_dir = app_data_dir

    # --- Worker Endpoints ---

    @app.post("/api/v1/workers/register", response_model=MessageResponse)
    def register_worker(request: WorkerRegistration, db: Database = Depends(get_db)):
        """Register a worker with the server."""
        # Register each GPU as a separate worker if multiple GPUs
        if request.gpu_ids:
            for gpu_id in request.gpu_ids:
                worker_id = f"{request.worker_id}-gpu{gpu_id}"
                db.register_worker(
                    worker_id=worker_id,
                    pid=0,  # Not tracked for remote workers
                    hostname=request.hostname,
                    gpu_index=gpu_id,
                )
        else:
            # Single worker without GPU
            db.register_worker(
                worker_id=request.worker_id,
                pid=0,
                hostname=request.hostname,
                gpu_index=None,
            )

        return MessageResponse(message=f"Worker {request.worker_id} registered")

    @app.post("/api/v1/workers/unregister", response_model=MessageResponse)
    def unregister_worker(request: WorkerUnregister, db: Database = Depends(get_db)):
        """Unregister a worker from the server."""
        db.unregister_worker(request.worker_id)
        return MessageResponse(message=f"Worker {request.worker_id} unregistered")

    @app.get("/api/v1/workers", response_model=dict)
    def list_workers(db: Database = Depends(get_db)):
        """Get all registered workers."""
        workers = db.get_workers()
        return {"workers": workers}

    # --- Job Endpoints ---

    @app.post("/api/v1/jobs", response_model=dict)
    def create_job(request: JobCreate, db: Database = Depends(get_db)):
        """Submit a new job to the queue."""
        job_id = db.create_job(
            command_argv=request.command_argv,
            workdir=request.workdir,
            name=request.name,
            config=request.config,
            tags=request.tags,
        )
        return {"job_id": job_id, "message": f"Job {job_id} created"}

    @app.post("/api/v1/jobs/claim", response_model=JobClaimResponse)
    def claim_job(request: JobClaim, db: Database = Depends(get_db)):
        """Claim the next available job."""
        job = db.claim_job(
            worker_id=request.worker_id,
            lease_seconds=request.lease_seconds,
        )

        if job is None:
            return JobClaimResponse(job=None)

        return JobClaimResponse(
            job=JobResponse(
                id=job["id"],
                name=job.get("name"),
                command_argv=job["command_argv"],
                workdir=job["workdir"],
                config=job.get("config"),
                tags=job.get("tags"),
                status="running",
                attempt=job.get("attempt", 1),
                worker_id=request.worker_id,
            )
        )

    @app.get("/api/v1/jobs/{job_id}", response_model=JobResponse)
    def get_job(job_id: int, db: Database = Depends(get_db)):
        """Get job details."""
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        return JobResponse(
            id=job["id"],
            name=job.get("name"),
            command_argv=job.get("command_argv", []),
            workdir=job.get("workdir", ""),
            config=job.get("config"),
            tags=job.get("tags"),
            status=job.get("status", "unknown"),
            attempt=job.get("attempt", 1),
            worker_id=job.get("worker_id"),
            created_at=job.get("created_at"),
            started_at=job.get("started_at"),
            finished_at=job.get("finished_at"),
            exit_code=job.get("exit_code"),
            run_id=job.get("run_id"),
        )

    @app.get("/api/v1/jobs", response_model=dict)
    def list_jobs(
        status: Optional[str] = Query(None, description="Filter: active, queued, running, completed, failed"),
        limit: int = Query(50, ge=1, le=500),
        db: Database = Depends(get_db),
    ):
        """List jobs with optional filtering."""
        if status == "active":
            jobs = db.get_active_jobs()
        else:
            # For now, just return active jobs
            # TODO: Add more filtering options
            jobs = db.get_active_jobs()

        return {"jobs": jobs[:limit]}

    @app.post("/api/v1/jobs/{job_id}/heartbeat", response_model=HeartbeatResponse)
    def job_heartbeat(job_id: int, request: JobHeartbeat, db: Database = Depends(get_db)):
        """Renew the lease for a job."""
        success = db.renew_lease(
            job_id=job_id,
            worker_id=request.worker_id,
            lease_seconds=request.lease_seconds,
        )

        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"Job {job_id} not found or not owned by worker {request.worker_id}",
            )

        # Check if cancellation was requested
        cancel_requested = db.update_job_heartbeat(job_id)

        return HeartbeatResponse(
            success=True,
            cancel_requested=cancel_requested is not None,
        )

    @app.post("/api/v1/jobs/{job_id}/complete", response_model=MessageResponse)
    def complete_job(job_id: int, request: JobComplete, db: Database = Depends(get_db)):
        """Mark a job as completed."""
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        if job.get("worker_id") != request.worker_id:
            raise HTTPException(
                status_code=403,
                detail=f"Job {job_id} is not owned by worker {request.worker_id}",
            )

        db.complete_job(
            job_id=job_id,
            exit_code=request.exit_code,
            run_id=request.run_id,
            error_message=request.error_message,
        )

        status = "completed" if request.exit_code == 0 else "failed"
        return MessageResponse(message=f"Job {job_id} marked as {status}")

    @app.post("/api/v1/jobs/{job_id}/fail", response_model=MessageResponse)
    def fail_job(job_id: int, request: JobFail, db: Database = Depends(get_db)):
        """Mark a job as failed."""
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        if job.get("worker_id") != request.worker_id:
            raise HTTPException(
                status_code=403,
                detail=f"Job {job_id} is not owned by worker {request.worker_id}",
            )

        db.complete_job(
            job_id=job_id,
            exit_code=1,
            error_message=request.error_message,
        )

        return MessageResponse(message=f"Job {job_id} marked as failed")

    @app.post("/api/v1/jobs/{job_id}/cancel", response_model=dict)
    def cancel_job(job_id: int, db: Database = Depends(get_db)):
        """Cancel a job."""
        try:
            old_status = db.cancel_job(job_id)
            return {"message": f"Job {job_id} cancelled", "previous_status": old_status}
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # --- Run Endpoints ---

    @app.get("/api/v1/runs", response_model=dict)
    def list_runs(
        status: Optional[str] = Query(None),
        tag: Optional[str] = Query(None),
        limit: int = Query(50, ge=1, le=500),
        db: Database = Depends(get_db),
    ):
        """List runs with optional filtering."""
        runs = db.get_runs(status=status, tag=tag, limit=limit)
        return {"runs": runs}

    @app.get("/api/v1/runs/{run_id}", response_model=RunResponse)
    def get_run(run_id: str, db: Database = Depends(get_db)):
        """Get run details."""
        run = db.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

        return RunResponse(
            id=run["id"],
            job_id=run.get("job_id"),
            name=run.get("name"),
            config=run.get("config"),
            tags=run.get("tags"),
            status=run.get("status", "unknown"),
            started_at=run.get("started_at"),
            finished_at=run.get("finished_at"),
            duration_seconds=run.get("duration_seconds"),
            summary=run.get("summary"),
            hostname=run.get("hostname"),
            run_dir=run.get("run_dir"),
        )

    # --- Status Endpoints ---

    @app.get("/api/v1/status", response_model=StatusResponse)
    def get_status(db: Database = Depends(get_db)):
        """Get server status and statistics."""
        active_jobs = db.get_active_jobs()
        workers = db.get_workers()

        queued = sum(1 for j in active_jobs if j.get("status") == "queued")
        running = sum(1 for j in active_jobs if j.get("status") == "running")

        # Get recent completed/failed (last 100)
        recent_runs = db.get_runs(limit=100)
        completed = sum(1 for r in recent_runs if r.get("status") == "completed")
        failed = sum(1 for r in recent_runs if r.get("status") == "failed")

        workers_online = sum(1 for w in workers if w.get("status") in ("idle", "busy"))
        workers_total = len(workers)

        return StatusResponse(
            queued=queued,
            running=running,
            completed=completed,
            failed=failed,
            workers_online=workers_online,
            workers_total=workers_total,
        )

    @app.get("/health")
    def health_check():
        """Health check endpoint."""
        return {"status": "healthy"}

    return app
