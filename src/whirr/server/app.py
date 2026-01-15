# Copyright (c) Syntropy Systems
"""FastAPI application for the whirr server."""

# pyright: reportUnusedFunction=false
from __future__ import annotations

import logging
import mimetypes
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from time import sleep
from typing import TYPE_CHECKING, Annotated, Optional, cast

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import Response

from whirr.db import Database, PostgresDatabase, SQLiteDatabase
from whirr.models.api import (
    HealthResponse,
    HeartbeatResponse,
    JobCancelResponse,
    JobClaim,
    JobClaimResponse,
    JobComplete,
    JobCreate,
    JobCreateResponse,
    JobFail,
    JobHeartbeat,
    JobListResponse,
    JobResponse,
    MessageResponse,
    RunArtifactsResponse,
    RunListResponse,
    RunMetricsResponse,
    RunResponse,
    StatusResponse,
    WorkerListResponse,
    WorkerRegistration,
    WorkerResponse,
    WorkerUnregister,
)
from whirr.models.run import ArtifactRecord
from whirr.run import read_metrics

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from whirr.models.db import JobRecord, RunRecord, WorkerRecord

logger = logging.getLogger(__name__)


@dataclass
class _ServerState:
    db: Database | None = None
    lease_monitor_thread: Thread | None = None
    shutdown_flag: bool = False


_state = _ServerState()


def get_db() -> Database:
    """Get the database instance."""
    if _state.db is None:
        msg = "Database not initialized"
        raise RuntimeError(msg)
    return _state.db


def _job_to_response(job: JobRecord) -> JobResponse:
    return JobResponse(
        id=job.id,
        name=job.name,
        command_argv=job.command_argv,
        workdir=job.workdir,
        config=job.config.values if job.config else None,
        tags=job.tags,
        status=job.status,
        attempt=job.attempt,
        worker_id=job.worker_id,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        exit_code=job.exit_code,
        run_id=job.run_id,
    )


def _run_to_response(run: RunRecord) -> RunResponse:
    return RunResponse(
        id=run.id,
        job_id=run.job_id,
        name=run.name,
        config=run.config.values if run.config else None,
        tags=run.tags,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_seconds=run.duration_seconds,
        summary=run.summary.values if run.summary else None,
        hostname=run.hostname,
        run_dir=run.run_dir,
    )


def _worker_to_response(worker: WorkerRecord) -> WorkerResponse:
    return WorkerResponse(
        id=worker.id,
        hostname=worker.hostname,
        gpu_id=worker.gpu_id,
        status=worker.status,
        current_job_id=worker.current_job_id,
        heartbeat_at=worker.heartbeat_at,
    )


def _get_data_dir(app: FastAPI) -> Path:
    return cast("Path", app.state.data_dir)


def _lease_monitor_loop(db: Database, interval: int = 30) -> None:
    """Background thread to check for expired leases and requeue jobs."""
    while not _state.shutdown_flag:
        try:
            _ = db.requeue_expired_jobs()
        except Exception as exc:
            logger.exception("Lease monitor error", exc_info=exc)
        sleep(interval)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Lifecycle manager for the FastAPI app."""
    # Start lease monitor thread
    _state.shutdown_flag = False
    if _state.db is not None:
        _state.lease_monitor_thread = Thread(
            target=_lease_monitor_loop,
            args=(_state.db,),
            daemon=True,
        )
        _state.lease_monitor_thread.start()

    yield

    # Shutdown
    _state.shutdown_flag = True
    if _state.lease_monitor_thread is not None:
        _state.lease_monitor_thread.join(timeout=5)


def create_app(  # noqa: C901, PLR0915
    database_url: str | None = None,
    db_path: Path | None = None,
    data_dir: Path | None = None,
) -> FastAPI:
    """Create the FastAPI application.

    Args:
        database_url: PostgreSQL connection URL (preferred for production)
        db_path: SQLite database path (for testing/local)
        data_dir: Directory for run data (shared filesystem)

    Returns:
        Configured FastAPI application

    """
    # Initialize database
    if database_url:
        _state.db = PostgresDatabase(database_url)
    elif db_path:
        _state.db = SQLiteDatabase(db_path)
    else:
        # Try environment variable
        env_url = os.environ.get("WHIRR_DATABASE_URL")
        if env_url:
            _state.db = PostgresDatabase(env_url)
        else:
            msg = "No database configuration provided"
            raise ValueError(msg)

    # Initialize schema
    db = _state.db
    db.init_schema()

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
    def register_worker(
        request: WorkerRegistration,
        db: Annotated[Database, Depends(get_db)],
    ) -> MessageResponse:
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
    def unregister_worker(
        request: WorkerUnregister,
        db: Annotated[Database, Depends(get_db)],
    ) -> MessageResponse:
        """Unregister a worker from the server."""
        db.unregister_worker(request.worker_id)
        return MessageResponse(message=f"Worker {request.worker_id} unregistered")

    @app.get("/api/v1/workers", response_model=WorkerListResponse)
    def list_workers(
        db: Annotated[Database, Depends(get_db)],
    ) -> WorkerListResponse:
        """Get all registered workers."""
        workers = db.get_workers()
        return WorkerListResponse(workers=[_worker_to_response(w) for w in workers])

    # --- Job Endpoints ---

    @app.post("/api/v1/jobs", response_model=JobCreateResponse)
    def create_job(
        request: JobCreate,
        db: Annotated[Database, Depends(get_db)],
    ) -> JobCreateResponse:
        """Submit a new job to the queue.

        Returns job_id, run_id (reserved immediately), and run_dir path.
        """
        job_id = db.create_job(
            command_argv=request.command_argv,
            workdir=request.workdir,
            name=request.name,
            config=request.config,
            tags=request.tags,
        )
        # Reserve run_id immediately so callers know where results will be
        run_id = f"job-{job_id}"
        data_dir = _get_data_dir(app)
        run_dir = str(data_dir / "runs" / run_id)
        return JobCreateResponse(
            job_id=job_id,
            run_id=run_id,
            run_dir=run_dir,
            message=f"Job {job_id} created",
        )

    @app.post("/api/v1/jobs/claim", response_model=JobClaimResponse)
    def claim_job(
        request: JobClaim,
        db: Annotated[Database, Depends(get_db)],
    ) -> JobClaimResponse:
        """Claim the next available job."""
        job = db.claim_job(
            worker_id=request.worker_id,
            lease_seconds=request.lease_seconds,
        )

        if job is None:
            return JobClaimResponse(job=None)

        return JobClaimResponse(job=_job_to_response(job))

    @app.get("/api/v1/jobs/{job_id}", response_model=JobResponse)
    def get_job(
        job_id: int,
        db: Annotated[Database, Depends(get_db)],
    ) -> JobResponse:
        """Get job details."""
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        return _job_to_response(job)

    @app.get("/api/v1/jobs", response_model=JobListResponse)
    def list_jobs(
        db: Annotated[Database, Depends(get_db)],
        status: Annotated[
            Optional[str],
            Query(
                description="Filter: active, queued, running, completed, failed",
            ),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> JobListResponse:
        """List jobs with optional filtering."""
        jobs = db.get_active_jobs()
        if status:
            jobs = [job for job in jobs if job.status == status]

        return JobListResponse(jobs=[_job_to_response(job) for job in jobs[:limit]])

    @app.post("/api/v1/jobs/{job_id}/heartbeat", response_model=HeartbeatResponse)
    def job_heartbeat(
        job_id: int,
        request: JobHeartbeat,
        db: Annotated[Database, Depends(get_db)],
    ) -> HeartbeatResponse:
        """Renew the lease for a job."""
        success = db.renew_lease(
            job_id=job_id,
            worker_id=request.worker_id,
            lease_seconds=request.lease_seconds,
        )

        if not success:
            detail = (
                f"Job {job_id} not found or not owned by worker {request.worker_id}"
            )
            raise HTTPException(
                status_code=404,
                detail=detail,
            )

        # Check if cancellation was requested
        cancel_requested = db.update_job_heartbeat(job_id)

        return HeartbeatResponse(
            success=True,
            cancel_requested=cancel_requested is not None,
        )

    @app.post("/api/v1/jobs/{job_id}/complete", response_model=MessageResponse)
    def complete_job(
        job_id: int,
        request: JobComplete,
        db: Annotated[Database, Depends(get_db)],
    ) -> MessageResponse:
        """Mark a job as completed."""
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        if job.worker_id != request.worker_id:
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
    def fail_job(
        job_id: int,
        request: JobFail,
        db: Annotated[Database, Depends(get_db)],
    ) -> MessageResponse:
        """Mark a job as failed."""
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        if job.worker_id != request.worker_id:
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

    @app.post("/api/v1/jobs/{job_id}/cancel", response_model=JobCancelResponse)
    def cancel_job(
        job_id: int,
        db: Annotated[Database, Depends(get_db)],
    ) -> JobCancelResponse:
        """Cancel a job."""
        try:
            old_status = db.cancel_job(job_id)
            return JobCancelResponse(
                message=f"Job {job_id} cancelled",
                previous_status=old_status,
            )
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    # --- Run Endpoints ---

    @app.get("/api/v1/runs", response_model=RunListResponse)
    def list_runs(
        db: Annotated[Database, Depends(get_db)],
        status: Annotated[Optional[str], Query()] = None,
        tag: Annotated[Optional[str], Query()] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> RunListResponse:
        """List runs with optional filtering."""
        runs = db.get_runs(status=status, tag=tag, limit=limit)
        return RunListResponse(runs=[_run_to_response(run) for run in runs])

    @app.get("/api/v1/runs/{run_id}", response_model=RunResponse)
    def get_run(
        run_id: str,
        db: Annotated[Database, Depends(get_db)],
    ) -> RunResponse:
        """Get run details."""
        run = db.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

        return _run_to_response(run)

    @app.get("/api/v1/runs/{run_id}/metrics", response_model=RunMetricsResponse)
    def get_run_metrics(
        run_id: str,
        db: Annotated[Database, Depends(get_db)],
    ) -> RunMetricsResponse:
        """Get metrics for a run.

        Returns the contents of the run's metrics.jsonl file as a list of records.
        """
        # Verify run exists
        run = db.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

        # Find metrics file
        run_dir = run.run_dir
        if run_dir:
            metrics_path = Path(run_dir) / "metrics.jsonl"
        else:
            # Fallback to data_dir/runs/{run_id}/metrics.jsonl
            data_dir = _get_data_dir(app)
            metrics_path = data_dir / "runs" / run_id / "metrics.jsonl"

        if not metrics_path.exists():
            return RunMetricsResponse(metrics=[], count=0)

        try:
            metrics = read_metrics(metrics_path)
        except OSError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error reading metrics file: {e}",
            ) from e

        return RunMetricsResponse(metrics=metrics, count=len(metrics))

    @app.get("/api/v1/runs/{run_id}/artifacts", response_model=RunArtifactsResponse)
    def list_run_artifacts(
        run_id: str,
        db: Annotated[Database, Depends(get_db)],
    ) -> RunArtifactsResponse:
        """List all artifacts for a run.

        Returns files in the run directory with name, size, and modified time.
        """
        # Verify run exists
        run = db.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

        # Find run directory
        run_dir = run.run_dir
        if run_dir:
            run_path = Path(run_dir)
        else:
            data_dir = _get_data_dir(app)
            run_path = data_dir / "runs" / run_id

        if not run_path.exists():
            return RunArtifactsResponse(artifacts=[], count=0)

        # List all files recursively
        artifacts: list[ArtifactRecord] = []
        try:
            for file_path in run_path.rglob("*"):
                if file_path.is_file():
                    rel_path = file_path.relative_to(run_path)
                    stat = file_path.stat()
                    artifacts.append(
                        ArtifactRecord(
                            path=str(rel_path),
                            size=stat.st_size,
                            modified=datetime.fromtimestamp(
                                stat.st_mtime, tz=timezone.utc
                            ).isoformat(),
                        )
                    )
        except OSError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error listing artifacts: {e}",
            ) from e

        # Sort by path for consistent ordering
        artifacts.sort(key=lambda x: x.path)
        return RunArtifactsResponse(artifacts=artifacts, count=len(artifacts))

    @app.get("/api/v1/runs/{run_id}/artifacts/{artifact_path:path}")
    def get_run_artifact(
        run_id: str,
        artifact_path: str,
        db: Annotated[Database, Depends(get_db)],
    ) -> Response:
        """Download an artifact file from a run.

        Returns the raw file content with appropriate content-type.
        """
        # Verify run exists
        run = db.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

        # Find run directory
        run_dir = run.run_dir
        if run_dir:
            run_path = Path(run_dir)
        else:
            data_dir = _get_data_dir(app)
            run_path = data_dir / "runs" / run_id

        # Resolve the artifact path safely
        file_path = (run_path / artifact_path).resolve()

        # Security: ensure the path is within the run directory
        try:
            _ = file_path.relative_to(run_path.resolve())
        except ValueError as e:
            raise HTTPException(
                status_code=403,
                detail="Access denied: path traversal not allowed",
            ) from e

        if not file_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Artifact not found: {artifact_path}",
            )

        if not file_path.is_file():
            raise HTTPException(
                status_code=400,
                detail=f"Not a file: {artifact_path}",
            )

        # Read file content
        try:
            content = file_path.read_bytes()
        except OSError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error reading artifact: {e}",
            ) from e

        # Guess content type
        content_type, _ = mimetypes.guess_type(str(file_path))
        if content_type is None:
            content_type = "application/octet-stream"

        return Response(
            content=content,
            media_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{file_path.name}"',
            },
        )

    # --- Status Endpoints ---

    @app.get("/api/v1/status", response_model=StatusResponse)
    def get_status(db: Annotated[Database, Depends(get_db)]) -> StatusResponse:
        """Get server status and statistics."""
        active_jobs = db.get_active_jobs()
        workers = db.get_workers()

        queued = sum(1 for j in active_jobs if j.status == "queued")
        running = sum(1 for j in active_jobs if j.status == "running")

        # Get recent completed/failed (last 100)
        recent_runs = db.get_runs(limit=100)
        completed = sum(1 for r in recent_runs if r.status == "completed")
        failed = sum(1 for r in recent_runs if r.status == "failed")

        workers_online = sum(1 for w in workers if w.status in ("idle", "busy"))
        workers_total = len(workers)

        return StatusResponse(
            queued=queued,
            running=running,
            completed=completed,
            failed=failed,
            workers_online=workers_online,
            workers_total=workers_total,
        )

    @app.get("/health", response_model=HealthResponse)
    def health_check() -> HealthResponse:
        """Health check endpoint."""
        return HealthResponse(status="healthy")

    return app
