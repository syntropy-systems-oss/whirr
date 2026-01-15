# Copyright (c) Syntropy Systems
"""Pydantic models for whirr API requests and responses."""

from __future__ import annotations

from pydantic import Field

from .base import JSONValue, WhirrBaseModel
from .run import ArtifactRecord, RunMetricRecord


class WorkerRegistration(WhirrBaseModel):
    """Request to register a worker."""

    worker_id: str
    hostname: str
    gpu_ids: list[int] = Field(default_factory=list)


class WorkerUnregister(WhirrBaseModel):
    """Request to unregister a worker."""

    worker_id: str


class WorkerResponse(WhirrBaseModel):
    """Worker information response."""

    id: str
    hostname: str | None = None
    gpu_id: int | None = None
    status: str
    current_job_id: int | None = None
    heartbeat_at: str | None = None


class WorkerListResponse(WhirrBaseModel):
    """Response containing worker records."""

    workers: list[WorkerResponse]


class JobCreate(WhirrBaseModel):
    """Request to create a new job."""

    command_argv: list[str]
    workdir: str
    name: str | None = None
    config: dict[str, JSONValue] | None = None
    tags: list[str] | None = None


class JobCreateResponse(WhirrBaseModel):
    """Response from creating a job."""

    job_id: int
    run_id: str
    run_dir: str
    message: str


class JobClaim(WhirrBaseModel):
    """Request to claim a job."""

    worker_id: str
    gpu_id: int | None = None
    lease_seconds: int = Field(default=60, ge=10, le=600)


class JobHeartbeat(WhirrBaseModel):
    """Request to renew job lease."""

    worker_id: str
    lease_seconds: int = Field(default=60, ge=10, le=600)


class JobComplete(WhirrBaseModel):
    """Request to mark job as completed."""

    worker_id: str
    exit_code: int
    run_id: str | None = None
    error_message: str | None = None


class JobFail(WhirrBaseModel):
    """Request to mark job as failed."""

    worker_id: str
    error_message: str


class JobResponse(WhirrBaseModel):
    """Job information response."""

    id: int
    name: str | None = None
    command_argv: list[str]
    workdir: str
    config: dict[str, JSONValue] | None = None
    tags: list[str] | None = None
    status: str
    attempt: int = 1
    worker_id: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    run_id: str | None = None


class JobClaimResponse(WhirrBaseModel):
    """Response from claiming a job."""

    job: JobResponse | None = None


class JobListResponse(WhirrBaseModel):
    """Response containing job records."""

    jobs: list[JobResponse]


class JobCancelResponse(WhirrBaseModel):
    """Response from cancelling a job."""

    message: str
    previous_status: str


class HeartbeatResponse(WhirrBaseModel):
    """Response from job heartbeat."""

    success: bool
    lease_expires_at: str | None = None
    cancel_requested: bool = False


class RunResponse(WhirrBaseModel):
    """Run information response."""

    id: str
    job_id: int | None = None
    name: str | None = None
    config: dict[str, JSONValue] | None = None
    tags: list[str] | None = None
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float | None = None
    summary: dict[str, JSONValue] | None = None
    hostname: str | None = None
    run_dir: str | None = None


class RunListResponse(WhirrBaseModel):
    """Response containing run records."""

    runs: list[RunResponse]


class RunMetricsResponse(WhirrBaseModel):
    """Response containing run metrics."""

    metrics: list[RunMetricRecord]
    count: int


class RunArtifactsResponse(WhirrBaseModel):
    """Response containing run artifacts."""

    artifacts: list[ArtifactRecord]
    count: int


class StatusResponse(WhirrBaseModel):
    """Server status response."""

    queued: int
    running: int
    completed: int
    failed: int
    workers_online: int
    workers_total: int


class MessageResponse(WhirrBaseModel):
    """Generic message response."""

    message: str
    success: bool = True


class ErrorResponse(WhirrBaseModel):
    """Error response."""

    detail: str
    error_code: str | None = None


class HealthResponse(WhirrBaseModel):
    """Health check response."""

    status: str


_ = RunMetricsResponse.model_rebuild(
    _types_namespace={"RunMetricRecord": RunMetricRecord},
)
_ = RunArtifactsResponse.model_rebuild(
    _types_namespace={"ArtifactRecord": ArtifactRecord},
)
