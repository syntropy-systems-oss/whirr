"""Pydantic models for the whirr server API."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# --- Worker Models ---


class WorkerRegistration(BaseModel):
    """Request to register a worker."""

    worker_id: str = Field(..., description="Unique worker identifier")
    hostname: str = Field(..., description="Worker hostname")
    gpu_ids: list[int] = Field(default_factory=list, description="Available GPU indices")


class WorkerUnregister(BaseModel):
    """Request to unregister a worker."""

    worker_id: str = Field(..., description="Worker identifier to unregister")


class WorkerResponse(BaseModel):
    """Worker information response."""

    id: str
    hostname: str
    gpu_id: Optional[int] = None
    status: str
    current_job_id: Optional[int] = None
    heartbeat_at: Optional[datetime] = None


# --- Job Models ---


class JobCreate(BaseModel):
    """Request to create a new job."""

    command_argv: list[str] = Field(..., description="Command to run as list of arguments")
    workdir: str = Field(..., description="Working directory for the command")
    name: Optional[str] = Field(None, description="Optional job name")
    config: Optional[dict[str, Any]] = Field(None, description="Optional configuration")
    tags: Optional[list[str]] = Field(None, description="Optional tags")


class JobClaim(BaseModel):
    """Request to claim a job."""

    worker_id: str = Field(..., description="Worker claiming the job")
    gpu_id: Optional[int] = Field(None, description="GPU index to use")
    lease_seconds: int = Field(60, description="Lease duration in seconds", ge=10, le=600)


class JobHeartbeat(BaseModel):
    """Request to renew job lease."""

    worker_id: str = Field(..., description="Worker holding the job")
    lease_seconds: int = Field(60, description="New lease duration in seconds", ge=10, le=600)


class JobComplete(BaseModel):
    """Request to mark job as completed."""

    worker_id: str = Field(..., description="Worker that completed the job")
    exit_code: int = Field(..., description="Process exit code (0 = success)")
    run_id: Optional[str] = Field(None, description="Associated run ID")
    error_message: Optional[str] = Field(None, description="Error message if failed")


class JobFail(BaseModel):
    """Request to mark job as failed."""

    worker_id: str = Field(..., description="Worker reporting failure")
    error_message: str = Field(..., description="Error description")


class JobResponse(BaseModel):
    """Job information response."""

    id: int
    name: Optional[str] = None
    command_argv: list[str]
    workdir: str
    config: Optional[dict[str, Any]] = None
    tags: Optional[list[str]] = None
    status: str
    attempt: int = 1
    worker_id: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    exit_code: Optional[int] = None
    run_id: Optional[str] = None


class JobClaimResponse(BaseModel):
    """Response from claiming a job."""

    job: Optional[JobResponse] = Field(None, description="Claimed job or null if none available")


class HeartbeatResponse(BaseModel):
    """Response from job heartbeat."""

    success: bool
    lease_expires_at: Optional[datetime] = None
    cancel_requested: bool = False


# --- Run Models ---


class RunResponse(BaseModel):
    """Run information response."""

    id: str
    job_id: Optional[int] = None
    name: Optional[str] = None
    config: Optional[dict[str, Any]] = None
    tags: Optional[list[str]] = None
    status: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    summary: Optional[dict[str, Any]] = None
    hostname: Optional[str] = None
    run_dir: Optional[str] = None


# --- Status Models ---


class StatusResponse(BaseModel):
    """Server status response."""

    queued: int = Field(..., description="Number of queued jobs")
    running: int = Field(..., description="Number of running jobs")
    completed: int = Field(..., description="Number of completed jobs (recent)")
    failed: int = Field(..., description="Number of failed jobs (recent)")
    workers_online: int = Field(..., description="Number of online workers")
    workers_total: int = Field(..., description="Total registered workers")


# --- Generic Response Models ---


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str
    success: bool = True


class ErrorResponse(BaseModel):
    """Error response."""

    detail: str
    error_code: Optional[str] = None
