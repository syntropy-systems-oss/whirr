# Copyright (c) Syntropy Systems
"""HTTP client for workers to communicate with the whirr server."""
from __future__ import annotations

import socket
import time
from typing import TYPE_CHECKING, Protocol, TypeVar, cast, overload

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

from pydantic import BaseModel, ValidationError
from typing_extensions import Self

from whirr.models.api import (
    ErrorResponse,
    HeartbeatResponse,
    JobCancelResponse,
    JobClaimResponse,
    JobCreateResponse,
    JobListResponse,
    JobResponse,
    MessageResponse,
    RunArtifactsResponse,
    RunListResponse,
    RunMetricsResponse,
    RunResponse,
    StatusResponse,
    WorkerListResponse,
    WorkerResponse,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import TracebackType

    from whirr.models.base import JSONValue
    from whirr.models.run import ArtifactRecord, RunMetricRecord

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)

class WhirrClientError(Exception):
    """Error from whirr server communication."""



class _HttpxResponse(Protocol):
    content: bytes

    def raise_for_status(self) -> _HttpxResponse:
        ...

    def json(self) -> object:
        ...


class _HttpxClient(Protocol):
    def request(
        self,
        *,
        method: str,
        url: str,
        json: Mapping[str, object] | None = None,
        params: Mapping[str, object] | None = None,
    ) -> _HttpxResponse:
        ...

    def get(self, url: str) -> _HttpxResponse:
        ...

    def close(self) -> None:
        ...


class WhirrClient:
    """HTTP client for workers to interact with the whirr server."""

    server_url: str
    timeout: float
    _client: _HttpxClient

    def __init__(self, server_url: str, timeout: float = 30.0) -> None:
        """Initialize the client.

        Args:
            server_url: Base URL of the whirr server (e.g., "http://head-node:8080")
            timeout: Request timeout in seconds

        """
        if httpx is None:
            msg = (
                "httpx is required for server mode. "
                "Install with: pip install whirr[server]"
            )
            raise ImportError(msg)

        self.server_url = server_url.rstrip("/")
        self.timeout = timeout
        client = cast("object", httpx.Client(timeout=timeout))
        self._client = cast("_HttpxClient", client)

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> Self:
        """Enter the client context and return self."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the client context and close the HTTP client."""
        self.close()

    @overload
    def _request(
        self,
        method: str,
        path: str,
        json: Mapping[str, object] | None = None,
        params: Mapping[str, object] | None = None,
        *,
        response_model: type[ResponseModel],
    ) -> ResponseModel:
        ...

    @overload
    def _request(
        self,
        method: str,
        path: str,
        json: Mapping[str, object] | None = None,
        params: Mapping[str, object] | None = None,
        *,
        response_model: None = None,
    ) -> dict[str, JSONValue]:
        ...

    def _request(
        self,
        method: str,
        path: str,
        json: Mapping[str, object] | None = None,
        params: Mapping[str, object] | None = None,
        *,
        response_model: type[ResponseModel] | None = None,
    ) -> ResponseModel | dict[str, JSONValue]:
        """Make an HTTP request to the server."""
        url = f"{self.server_url}{path}"
        try:
            response = self._client.request(
                method=method,
                url=url,
                json=json,
                params=params,
            )
            _ = response.raise_for_status()
            data = response.json()
            if response_model is None:
                return cast("dict[str, JSONValue]", data)
            return response_model.model_validate(data)
        except httpx.HTTPStatusError as e:  # pyright: ignore[reportOptionalMemberAccess] - guarded by __init__
            # Try to get error detail from response
            try:
                detail = ErrorResponse.model_validate(e.response.json()).detail
            except (ValidationError, ValueError):
                detail = str(e)
            msg = f"Server error: {detail}"
            raise WhirrClientError(msg) from e
        except httpx.RequestError as e:  # pyright: ignore[reportOptionalMemberAccess] - guarded by __init__
            msg = f"Connection error: {e}"
            raise WhirrClientError(msg) from e

    # --- Worker Operations ---

    def register_worker(
        self,
        worker_id: str,
        hostname: str | None = None,
        gpu_ids: list[int] | None = None,
    ) -> MessageResponse:
        """Register a worker with the server.

        Args:
            worker_id: Unique worker identifier
            hostname: Worker hostname (defaults to current hostname)
            gpu_ids: List of GPU indices available to this worker

        Returns:
            Registration response with worker info

        """
        return self._request(
            "POST",
            "/api/v1/workers/register",
            json={
                "worker_id": worker_id,
                "hostname": hostname or socket.gethostname(),
                "gpu_ids": gpu_ids or [],
            },
            response_model=MessageResponse,
        )

    def unregister_worker(self, worker_id: str) -> MessageResponse:
        """Unregister a worker from the server.

        Args:
            worker_id: Worker identifier

        Returns:
            Unregistration response

        """
        return self._request(
            "POST",
            "/api/v1/workers/unregister",
            json={"worker_id": worker_id},
            response_model=MessageResponse,
        )

    # --- Job Operations ---

    def claim_job(
        self,
        worker_id: str,
        gpu_id: int | None = None,
        lease_seconds: int = 60,
    ) -> JobResponse | None:
        """Attempt to claim the next available job.

        Args:
            worker_id: Worker identifier
            gpu_id: GPU index to use for this job (if any)
            lease_seconds: Lease duration in seconds

        Returns:
            Job dict if claimed, None if no jobs available

        """
        result = self._request(
            "POST",
            "/api/v1/jobs/claim",
            json={
                "worker_id": worker_id,
                "gpu_id": gpu_id,
                "lease_seconds": lease_seconds,
            },
            response_model=JobClaimResponse,
        )
        # Server returns {"job": null} when no jobs available
        return result.job

    def renew_lease(
        self,
        job_id: int,
        worker_id: str,
        lease_seconds: int = 60,
    ) -> HeartbeatResponse:
        """Renew the lease for a job (heartbeat).

        Args:
            job_id: Job ID
            worker_id: Worker identifier
            lease_seconds: New lease duration

        Returns:
            Response with lease info and cancel_requested flag

        """
        return self._request(
            "POST",
            f"/api/v1/jobs/{job_id}/heartbeat",
            json={
                "worker_id": worker_id,
                "lease_seconds": lease_seconds,
            },
            response_model=HeartbeatResponse,
        )

    def complete_job(
        self,
        job_id: int,
        worker_id: str,
        exit_code: int,
        run_id: str | None = None,
        error_message: str | None = None,
    ) -> MessageResponse:
        """Mark a job as completed.

        Args:
            job_id: Job ID
            worker_id: Worker identifier
            exit_code: Process exit code (0 = success)
            run_id: Associated run ID if any
            error_message: Error message if failed

        Returns:
            Completion response

        """
        return self._request(
            "POST",
            f"/api/v1/jobs/{job_id}/complete",
            json={
                "worker_id": worker_id,
                "exit_code": exit_code,
                "run_id": run_id,
                "error_message": error_message,
            },
            response_model=MessageResponse,
        )

    def fail_job(
        self,
        job_id: int,
        worker_id: str,
        error_message: str,
    ) -> MessageResponse:
        """Mark a job as failed.

        Args:
            job_id: Job ID
            worker_id: Worker identifier
            error_message: Error description

        Returns:
            Failure response

        """
        return self._request(
            "POST",
            f"/api/v1/jobs/{job_id}/fail",
            json={
                "worker_id": worker_id,
                "error_message": error_message,
            },
            response_model=MessageResponse,
        )

    def get_job(self, job_id: int) -> JobResponse | None:
        """Get job details.

        Args:
            job_id: Job ID

        Returns:
            Job dict or None if not found

        """
        try:
            return self._request(
                "GET",
                f"/api/v1/jobs/{job_id}",
                response_model=JobResponse,
            )
        except WhirrClientError:
            return None

    # --- Submit Operations ---

    def submit_job(
        self,
        command_argv: list[str],
        workdir: str,
        name: str | None = None,
        config: dict[str, JSONValue] | None = None,
        tags: list[str] | None = None,
    ) -> JobCreateResponse:
        """Submit a new job to the queue.

        Args:
            command_argv: Command to run as list of arguments
            workdir: Working directory for the command
            name: Optional job name
            config: Optional configuration dict
            tags: Optional list of tags

        Returns:
            Created job info with job_id

        """
        return self._request(
            "POST",
            "/api/v1/jobs",
            json={
                "command_argv": command_argv,
                "workdir": workdir,
                "name": name,
                "config": config,
                "tags": tags,
            },
            response_model=JobCreateResponse,
        )

    def cancel_job(self, job_id: int) -> JobCancelResponse:
        """Cancel a job.

        Args:
            job_id: Job ID to cancel

        Returns:
            Cancellation response with previous status

        """
        return self._request(
            "POST",
            f"/api/v1/jobs/{job_id}/cancel",
            response_model=JobCancelResponse,
        )

    # --- Status Operations ---

    def get_status(self) -> StatusResponse:
        """Get server status and statistics.

        Returns:
            Server status including job counts, worker counts, etc.

        """
        return self._request("GET", "/api/v1/status", response_model=StatusResponse)

    def get_active_jobs(self) -> list[JobResponse]:
        """Get all queued and running jobs.

        Returns:
            List of active jobs

        """
        result = self._request(
            "GET",
            "/api/v1/jobs",
            params={"status": "active"},
            response_model=JobListResponse,
        )
        return result.jobs

    def get_workers(self) -> list[WorkerResponse]:
        """Get all registered workers.

        Returns:
            List of worker info dicts

        """
        result = self._request(
            "GET",
            "/api/v1/workers",
            response_model=WorkerListResponse,
        )
        return result.workers

    # --- Run Operations ---

    def get_runs(
        self,
        status: str | None = None,
        tag: str | None = None,
        limit: int = 50,
    ) -> list[RunResponse]:
        """Get runs with optional filtering.

        Args:
            status: Filter by status (running, completed, failed)
            tag: Filter by tag
            limit: Maximum number of runs to return

        Returns:
            List of run dicts

        """
        params: dict[str, int | str] = {"limit": limit}
        if status:
            params["status"] = status
        if tag:
            params["tag"] = tag

        result = self._request(
            "GET",
            "/api/v1/runs",
            params=params,
            response_model=RunListResponse,
        )
        return result.runs

    def get_run(self, run_id: str) -> RunResponse | None:
        """Get run details.

        Args:
            run_id: Run ID

        Returns:
            Run dict or None if not found

        """
        try:
            return self._request(
                "GET",
                f"/api/v1/runs/{run_id}",
                response_model=RunResponse,
            )
        except WhirrClientError:
            return None

    def get_metrics(self, run_id: str) -> list[RunMetricRecord]:
        """Get metrics for a run.

        Args:
            run_id: Run ID

        Returns:
            List of metric records from the run's metrics.jsonl

        """
        result = self._request(
            "GET",
            f"/api/v1/runs/{run_id}/metrics",
            response_model=RunMetricsResponse,
        )
        return result.metrics

    def list_artifacts(self, run_id: str) -> list[ArtifactRecord]:
        """List all artifacts for a run.

        Args:
            run_id: Run ID

        Returns:
            List of artifact dicts with path, size, and modified timestamp

        """
        result = self._request(
            "GET",
            f"/api/v1/runs/{run_id}/artifacts",
            response_model=RunArtifactsResponse,
        )
        return result.artifacts

    def get_artifact(self, run_id: str, path: str) -> bytes:
        """Download an artifact file from a run.

        Args:
            run_id: Run ID
            path: Relative path to the artifact within the run directory

        Returns:
            Raw file content as bytes

        Raises:
            WhirrClientError: If artifact not found or access denied

        """
        url = f"{self.server_url}/api/v1/runs/{run_id}/artifacts/{path}"
        try:
            response = self._client.get(url)
            _ = response.raise_for_status()
        except httpx.HTTPStatusError as e:  # pyright: ignore[reportOptionalMemberAccess] - guarded by __init__
            try:
                detail = ErrorResponse.model_validate(e.response.json()).detail
            except (ValidationError, ValueError):
                detail = str(e)
            msg = f"Error fetching artifact: {detail}"
            raise WhirrClientError(msg) from e
        except httpx.RequestError as e:  # pyright: ignore[reportOptionalMemberAccess] - guarded by __init__
            msg = f"Connection error: {e}"
            raise WhirrClientError(msg) from e
        else:
            return response.content

    # --- Convenience Methods ---

    def wait_for_job(
        self,
        job_id: int,
        poll_interval: float = 1.0,
        timeout: float | None = None,
    ) -> JobResponse:
        """Wait for a job to complete.

        Polls the job status until it's no longer queued or running.

        Args:
            job_id: Job ID to wait for
            poll_interval: Seconds between status checks (default: 1.0)
            timeout: Maximum seconds to wait (default: None = wait forever)

        Returns:
            Final job dict with status

        Raises:
            TimeoutError: If timeout is reached before job completes
            WhirrClientError: If job not found or server error

        """
        start = time.time()
        while True:
            job = self.get_job(job_id)
            if job is None:
                msg = f"Job {job_id} not found"
                raise WhirrClientError(msg)

            if job.status not in ("queued", "running"):
                return job

            if timeout is not None and (time.time() - start) > timeout:
                msg = f"Job {job_id} did not complete within {timeout}s"
                raise TimeoutError(msg)

            time.sleep(poll_interval)

    def submit_and_wait(  # noqa: PLR0913
        self,
        command_argv: list[str],
        workdir: str,
        name: str | None = None,
        config: dict[str, JSONValue] | None = None,
        tags: list[str] | None = None,
        poll_interval: float = 1.0,
        timeout: float | None = None,
    ) -> JobResponse:
        """Submit a job and wait for it to complete.

        Convenience method that combines submit_job() and wait_for_job().

        Args:
            command_argv: Command to run as list of arguments
            workdir: Working directory for the command
            name: Optional job name
            config: Optional configuration dict
            tags: Optional list of tags
            poll_interval: Seconds between status checks
            timeout: Maximum seconds to wait

        Returns:
            Final job dict with status and results

        """
        result = self.submit_job(
            command_argv=command_argv,
            workdir=workdir,
            name=name,
            config=config,
            tags=tags,
        )
        job_id = result.job_id
        return self.wait_for_job(job_id, poll_interval=poll_interval, timeout=timeout)


# Convenience function
def get_client(server_url: str, timeout: float = 30.0) -> WhirrClient:
    """Create a WhirrClient instance.

    Args:
        server_url: Base URL of the whirr server
        timeout: Request timeout in seconds

    Returns:
        WhirrClient instance

    """
    return WhirrClient(server_url, timeout)
