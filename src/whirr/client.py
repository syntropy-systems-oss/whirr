"""HTTP client for workers to communicate with the whirr server."""

import socket
import time
from typing import Optional, Union

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore


class WhirrClientError(Exception):
    """Error from whirr server communication."""

    pass


class WhirrClient:
    """HTTP client for workers to interact with the whirr server."""

    def __init__(self, server_url: str, timeout: float = 30.0):
        """
        Initialize the client.

        Args:
            server_url: Base URL of the whirr server (e.g., "http://head-node:8080")
            timeout: Request timeout in seconds
        """
        if httpx is None:
            raise ImportError(
                "httpx is required for server mode. "
                "Install with: pip install whirr[server]"
            )

        self.server_url = server_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> "WhirrClient":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> dict:
        """Make an HTTP request to the server."""
        url = f"{self.server_url}{path}"
        try:
            response = self._client.request(
                method=method,
                url=url,
                json=json,
                params=params,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:  # pyright: ignore[reportOptionalMemberAccess] - guarded by __init__
            # Try to get error detail from response
            try:
                detail = e.response.json().get("detail", str(e))
            except Exception:
                detail = str(e)
            raise WhirrClientError(f"Server error: {detail}") from e
        except httpx.RequestError as e:  # pyright: ignore[reportOptionalMemberAccess] - guarded by __init__
            raise WhirrClientError(f"Connection error: {e}") from e

    # --- Worker Operations ---

    def register_worker(
        self,
        worker_id: str,
        hostname: Optional[str] = None,
        gpu_ids: Optional[list[int]] = None,
    ) -> dict:
        """
        Register a worker with the server.

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
        )

    def unregister_worker(self, worker_id: str) -> dict:
        """
        Unregister a worker from the server.

        Args:
            worker_id: Worker identifier

        Returns:
            Unregistration response
        """
        return self._request(
            "POST",
            "/api/v1/workers/unregister",
            json={"worker_id": worker_id},
        )

    # --- Job Operations ---

    def claim_job(
        self,
        worker_id: str,
        gpu_id: Optional[int] = None,
        lease_seconds: int = 60,
    ) -> Optional[dict]:
        """
        Attempt to claim the next available job.

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
        )
        # Server returns {"job": null} when no jobs available
        return result.get("job")

    def renew_lease(
        self,
        job_id: int,
        worker_id: str,
        lease_seconds: int = 60,
    ) -> dict:
        """
        Renew the lease for a job (heartbeat).

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
        )

    def complete_job(
        self,
        job_id: int,
        worker_id: str,
        exit_code: int,
        run_id: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> dict:
        """
        Mark a job as completed.

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
        )

    def fail_job(
        self,
        job_id: int,
        worker_id: str,
        error_message: str,
    ) -> dict:
        """
        Mark a job as failed.

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
        )

    def get_job(self, job_id: int) -> Optional[dict]:
        """
        Get job details.

        Args:
            job_id: Job ID

        Returns:
            Job dict or None if not found
        """
        try:
            return self._request("GET", f"/api/v1/jobs/{job_id}")
        except WhirrClientError:
            return None

    # --- Submit Operations ---

    def submit_job(
        self,
        command_argv: list[str],
        workdir: str,
        name: Optional[str] = None,
        config: Optional[dict] = None,
        tags: Optional[list[str]] = None,
    ) -> dict:
        """
        Submit a new job to the queue.

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
        )

    def cancel_job(self, job_id: int) -> dict:
        """
        Cancel a job.

        Args:
            job_id: Job ID to cancel

        Returns:
            Cancellation response with previous status
        """
        return self._request("POST", f"/api/v1/jobs/{job_id}/cancel")

    # --- Status Operations ---

    def get_status(self) -> dict:
        """
        Get server status and statistics.

        Returns:
            Server status including job counts, worker counts, etc.
        """
        return self._request("GET", "/api/v1/status")

    def get_active_jobs(self) -> list[dict]:
        """
        Get all queued and running jobs.

        Returns:
            List of active jobs
        """
        result = self._request("GET", "/api/v1/jobs", params={"status": "active"})
        return result.get("jobs", [])

    def get_workers(self) -> list[dict]:
        """
        Get all registered workers.

        Returns:
            List of worker info dicts
        """
        result = self._request("GET", "/api/v1/workers")
        return result.get("workers", [])

    # --- Run Operations ---

    def get_runs(
        self,
        status: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """
        Get runs with optional filtering.

        Args:
            status: Filter by status (running, completed, failed)
            tag: Filter by tag
            limit: Maximum number of runs to return

        Returns:
            List of run dicts
        """
        params: dict[str, Union[int, str]] = {"limit": limit}
        if status:
            params["status"] = status
        if tag:
            params["tag"] = tag

        result = self._request("GET", "/api/v1/runs", params=params)
        return result.get("runs", [])

    def get_run(self, run_id: str) -> Optional[dict]:
        """
        Get run details.

        Args:
            run_id: Run ID

        Returns:
            Run dict or None if not found
        """
        try:
            return self._request("GET", f"/api/v1/runs/{run_id}")
        except WhirrClientError:
            return None

    def get_metrics(self, run_id: str) -> list[dict]:
        """
        Get metrics for a run.

        Args:
            run_id: Run ID

        Returns:
            List of metric records from the run's metrics.jsonl
        """
        result = self._request("GET", f"/api/v1/runs/{run_id}/metrics")
        return result.get("metrics", [])

    def list_artifacts(self, run_id: str) -> list[dict]:
        """
        List all artifacts for a run.

        Args:
            run_id: Run ID

        Returns:
            List of artifact dicts with path, size, and modified timestamp
        """
        result = self._request("GET", f"/api/v1/runs/{run_id}/artifacts")
        return result.get("artifacts", [])

    def get_artifact(self, run_id: str, path: str) -> bytes:
        """
        Download an artifact file from a run.

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
            response.raise_for_status()
            return response.content
        except httpx.HTTPStatusError as e:  # pyright: ignore[reportOptionalMemberAccess] - guarded by __init__
            try:
                detail = e.response.json().get("detail", str(e))
            except Exception:
                detail = str(e)
            raise WhirrClientError(f"Error fetching artifact: {detail}") from e
        except httpx.RequestError as e:  # pyright: ignore[reportOptionalMemberAccess] - guarded by __init__
            raise WhirrClientError(f"Connection error: {e}") from e

    # --- Convenience Methods ---

    def wait_for_job(
        self,
        job_id: int,
        poll_interval: float = 1.0,
        timeout: Optional[float] = None,
    ) -> dict:
        """
        Wait for a job to complete.

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
                raise WhirrClientError(f"Job {job_id} not found")

            status = job.get("status")
            if status not in ("queued", "running"):
                return job

            if timeout is not None and (time.time() - start) > timeout:
                raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")

            time.sleep(poll_interval)

    def submit_and_wait(
        self,
        command_argv: list[str],
        workdir: str,
        name: Optional[str] = None,
        config: Optional[dict] = None,
        tags: Optional[list[str]] = None,
        poll_interval: float = 1.0,
        timeout: Optional[float] = None,
    ) -> dict:
        """
        Submit a job and wait for it to complete.

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
        job_id = result["job_id"]
        return self.wait_for_job(job_id, poll_interval=poll_interval, timeout=timeout)


# Convenience function
def get_client(server_url: str, timeout: float = 30.0) -> WhirrClient:
    """
    Create a WhirrClient instance.

    Args:
        server_url: Base URL of the whirr server
        timeout: Request timeout in seconds

    Returns:
        WhirrClient instance
    """
    return WhirrClient(server_url, timeout)
