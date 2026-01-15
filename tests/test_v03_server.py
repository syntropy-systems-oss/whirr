# Copyright (c) Syntropy Systems
"""Tests for v0.3 server mode features."""

import importlib.util
import re
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from whirr.db import (
    SQLITE_SCHEMA,
    SQLiteDatabase,
    get_database,
)
from whirr.models.api import (
    HealthResponse,
    JobClaimResponse,
    JobCreateResponse,
    JobResponse,
    MessageResponse,
    RunArtifactsResponse,
    RunMetricsResponse,
    StatusResponse,
)
from whirr.models.db import JobRecord, RunRecord, WorkerRecord
from whirr.models.run import ArtifactRecord, RunMetricRecord

if TYPE_CHECKING:
    import sqlite3


def strip_ansi(text: str) -> str:
    """Strip ANSI escape codes from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class TestDatabaseAbstraction:
    """Tests for the Database abstract base class and implementations."""

    def test_sqlite_database_creation(self, tmp_path: Path) -> None:
        """Test SQLiteDatabase can be created and initialized."""
        db_path = tmp_path / "test.db"
        db = SQLiteDatabase(db_path)
        try:
            db.init_schema()
            # Verify schema was created
            row = cast(
                "sqlite3.Row | None",
                db.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'"
                ).fetchone(),
            )
            assert row is not None
        finally:
            db.close()

    def test_sqlite_create_and_claim_job(self, tmp_path: Path) -> None:
        """Test SQLiteDatabase job operations."""
        db_path = tmp_path / "test.db"
        db = SQLiteDatabase(db_path)
        try:
            db.init_schema()

            # Create a job
            job_id = db.create_job(
                command_argv=["python", "test.py"],
                workdir="/tmp",
                name="test-job",
                tags=["test"],
            )
            assert job_id == 1

            # Claim the job
            job: JobRecord | None = db.claim_job("worker-1")
            assert job is not None
            assert job.id == job_id
            assert job.command_argv == ["python", "test.py"]
            assert job.name == "test-job"

            # No more jobs to claim
            job2 = db.claim_job("worker-2")
            assert job2 is None
        finally:
            db.close()

    def test_sqlite_complete_job(self, tmp_path: Path) -> None:
        """Test SQLiteDatabase complete_job."""
        db_path = tmp_path / "test.db"
        db = SQLiteDatabase(db_path)
        try:
            db.init_schema()

            job_id = db.create_job(["echo", "hello"], "/tmp")
            _ = db.claim_job("worker-1")
            db.complete_job(job_id, exit_code=0)

            job = db.get_job(job_id)
            assert job is not None
            assert job.status == "completed"
            assert job.exit_code == 0
        finally:
            db.close()

    def test_sqlite_run_operations(self, tmp_path: Path) -> None:
        """Test SQLiteDatabase run operations."""
        db_path = tmp_path / "test.db"
        db = SQLiteDatabase(db_path)
        try:
            db.init_schema()

            # Create a run
            db.create_run(
                run_id="test-run-123",
                run_dir="/tmp/runs/test-run-123",
                name="Test Run",
                config={"lr": 0.01},
                tags=["test"],
            )

            # Get the run
            run: RunRecord | None = db.get_run("test-run-123")
            assert run is not None
            assert run.id == "test-run-123"
            assert run.name == "Test Run"
            assert run.status == "running"

            # Complete the run
            db.complete_run("test-run-123", "completed", {"final_loss": 0.1})
            run = db.get_run("test-run-123")
            assert run is not None
            assert run.status == "completed"
        finally:
            db.close()

    def test_sqlite_worker_operations(self, tmp_path: Path) -> None:
        """Test SQLiteDatabase worker operations."""
        db_path = tmp_path / "test.db"
        db = SQLiteDatabase(db_path)
        try:
            db.init_schema()

            # Register a worker
            db.register_worker(
                worker_id="worker-1",
                pid=1234,
                hostname="test-host",
                gpu_index=0,
            )

            # Get workers
            workers: list[WorkerRecord] = db.get_workers()
            assert len(workers) == 1
            assert workers[0].id == "worker-1"
            assert workers[0].hostname == "test-host"

            # Update status
            db.update_worker_status("worker-1", "busy", current_job_id=1)
            workers = db.get_workers()
            assert workers[0].status == "busy"

            # Unregister
            db.unregister_worker("worker-1")
            workers = db.get_workers()
            assert workers[0].status == "offline"
        finally:
            db.close()

    def test_get_database_factory_sqlite(self, tmp_path: Path) -> None:
        """Test get_database factory with SQLite."""
        db_path = tmp_path / "test.db"
        db = get_database(db_path=db_path)
        try:
            assert isinstance(db, SQLiteDatabase)
        finally:
            db.close()

    def test_get_database_requires_config(self) -> None:
        """Test get_database raises error without config."""
        with pytest.raises(ValueError, match="Either db_path or connection_url"):
            _ = get_database()


class TestClientModule:
    """Tests for the HTTP client module."""

    def test_client_import(self) -> None:
        """Test client module can be imported."""
        from whirr.client import WhirrClient, WhirrClientError, get_client
        assert WhirrClient is not None
        assert WhirrClientError is not None
        assert get_client is not None

    def test_client_requires_httpx(self) -> None:
        """Test client raises helpful error without httpx."""
        import importlib

        import whirr.client as client_module

        try:
            with patch.dict("sys.modules", {"httpx": None}):
                # Re-import to trigger ImportError
                _ = importlib.reload(client_module)
                with pytest.raises(ImportError, match="httpx is required"):
                    _ = client_module.WhirrClient("http://localhost:8080")
        finally:
            # Reload module to restore normal state for other tests
            _ = importlib.reload(client_module)

    def test_client_wait_for_job_timeout(self) -> None:
        """Test wait_for_job raises TimeoutError."""
        from whirr.client import WhirrClient

        client = WhirrClient("http://localhost:9999")
        # Mock get_job to always return running status
        client.get_job = MagicMock(return_value=JobResponse(id=1, command_argv=["echo", "test"], workdir="/tmp", status="running"))

        with pytest.raises(TimeoutError, match="did not complete"):
            _ = client.wait_for_job(1, poll_interval=0.01, timeout=0.05)

    def test_client_wait_for_job_completes(self) -> None:
        """Test wait_for_job returns when job completes."""
        from whirr.client import WhirrClient

        client = WhirrClient("http://localhost:9999")
        # Mock get_job to return completed after first call
        call_count: list[int] = [0]
        def mock_get_job(job_id: int) -> JobResponse:
            call_count[0] += 1
            if call_count[0] >= 2:
                return JobResponse(
                    id=job_id,
                    command_argv=["echo", "test"],
                    workdir="/tmp",
                    status="completed",
                    exit_code=0,
                )
            return JobResponse(
                id=job_id,
                command_argv=["echo", "test"],
                workdir="/tmp",
                status="running",
            )

        client.get_job = mock_get_job

        result = client.wait_for_job(1, poll_interval=0.01)
        assert result.status == "completed"
        assert call_count[0] == 2

    def test_client_wait_for_job_not_found(self) -> None:
        """Test wait_for_job raises error if job not found."""
        from whirr.client import WhirrClient, WhirrClientError

        client = WhirrClient("http://localhost:9999")
        client.get_job = MagicMock(return_value=None)

        with pytest.raises(WhirrClientError, match="not found"):
            _ = client.wait_for_job(999, poll_interval=0.01)

    def test_client_get_metrics(self) -> None:
        """Test get_metrics method."""
        from whirr.client import WhirrClient

        client = WhirrClient("http://localhost:9999")
        request_mock = MagicMock(
            return_value=RunMetricsResponse(
                metrics=[
                    RunMetricRecord.model_validate({"step": 0, "loss": 1.0}),
                    RunMetricRecord.model_validate({"step": 1, "loss": 0.5}),
                ],
                count=2,
            )
        )
        with patch.object(client, "_request", request_mock):
            metrics = client.get_metrics("test-run")
            assert len(metrics) == 2
            record = metrics[0].model_dump()
            assert record["loss"] == 1.0
            request_mock.assert_called_with(
                "GET",
                "/api/v1/runs/test-run/metrics",
                response_model=RunMetricsResponse,
            )

    def test_client_list_artifacts(self) -> None:
        """Test list_artifacts method."""
        from whirr.client import WhirrClient

        client = WhirrClient("http://localhost:9999")
        request_mock = MagicMock(
            return_value=RunArtifactsResponse(
                artifacts=[
                    ArtifactRecord(
                        path="metrics.jsonl",
                        size=100,
                        modified="2024-01-01T00:00:00Z",
                    ),
                    ArtifactRecord(
                        path="output.log",
                        size=500,
                        modified="2024-01-01T00:00:00Z",
                    ),
                ],
                count=2,
            )
        )
        with patch.object(client, "_request", request_mock):
            artifacts = client.list_artifacts("test-run")
            assert len(artifacts) == 2
            assert artifacts[0].path == "metrics.jsonl"
            request_mock.assert_called_with(
                "GET",
                "/api/v1/runs/test-run/artifacts",
                response_model=RunArtifactsResponse,
            )

    def test_client_get_artifact(self) -> None:
        """Test get_artifact method."""
        from whirr.client import WhirrClient

        client = WhirrClient("http://localhost:9999")

        # Mock the httpx client's get method
        mock_response = MagicMock()
        mock_response.content = b"file content here"
        mock_response.raise_for_status = MagicMock()
        client_httpx = MagicMock()
        get_mock = MagicMock(return_value=mock_response)
        client_httpx.get = get_mock
        with patch.object(client, "_client", client_httpx):
            content = client.get_artifact("test-run", "output.log")
            assert content == b"file content here"
            get_mock.assert_called_with(
                "http://localhost:9999/api/v1/runs/test-run/artifacts/output.log"
            )


class TestServerModule:
    """Tests for the server module."""

    def test_server_imports(self) -> None:
        """Test server module can be imported."""
        from whirr.server import create_app
        from whirr.server.models import (
            JobClaim,
        )
        assert create_app is not None
        assert JobClaim is not None

    def test_create_app_with_sqlite(self, tmp_path: Path) -> None:
        """Test create_app with SQLite database."""
        from whirr.server.app import create_app

        db_path = tmp_path / "test.db"
        app = create_app(db_path=db_path)
        assert app is not None
        assert app.title == "whirr server"

    def test_server_routes_exist(self, tmp_path: Path) -> None:
        """Test server has expected routes."""
        from whirr.server.app import create_app

        db_path = tmp_path / "test.db"
        app = create_app(db_path=db_path)

        routes = [cast("str", getattr(route, "path", "")) for route in app.routes]

        # Check key routes exist
        assert "/api/v1/workers/register" in routes
        assert "/api/v1/jobs/claim" in routes
        assert "/api/v1/jobs" in routes
        assert "/api/v1/runs" in routes
        assert "/api/v1/status" in routes
        assert "/health" in routes


# Check if httpx is available for TestClient
HAS_HTTPX = importlib.util.find_spec("httpx") is not None


@pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")
class TestServerAPI:
    """Tests for server API endpoints using TestClient."""

    @pytest.fixture
    def client(self, tmp_path: Path) -> TestClient:
        """Create a test client."""
        from whirr.server.app import create_app

        db_path = tmp_path / "test.db"
        app = create_app(db_path=db_path)
        return TestClient(app)

    def test_health_check(self, client: TestClient) -> None:
        """Test health check endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        payload = HealthResponse.model_validate_json(response.text)
        assert payload.status == "healthy"

    def test_register_worker(self, client: TestClient) -> None:
        """Test worker registration."""
        response = client.post(
            "/api/v1/workers/register",
            json={
                "worker_id": "test-worker",
                "hostname": "test-host",
                "gpu_ids": [0, 1],
            },
        )
        assert response.status_code == 200
        payload = MessageResponse.model_validate_json(response.text)
        assert "registered" in payload.message

    def test_create_and_claim_job(self, client: TestClient) -> None:
        """Test job creation and claiming."""
        # Create a job
        response = client.post(
            "/api/v1/jobs",
            json={
                "command_argv": ["python", "test.py"],
                "workdir": "/tmp",
                "name": "test-job",
            },
        )
        assert response.status_code == 200
        created = JobCreateResponse.model_validate_json(response.text)
        job_id = created.job_id
        assert job_id == 1

        # Claim the job
        response = client.post(
            "/api/v1/jobs/claim",
            json={
                "worker_id": "test-worker",
                "lease_seconds": 60,
            },
        )
        assert response.status_code == 200
        claim = JobClaimResponse.model_validate_json(response.text)
        job = claim.job
        assert job is not None
        assert job.id == job_id

    def test_claim_no_jobs(self, client: TestClient) -> None:
        """Test claiming when no jobs available."""
        response = client.post(
            "/api/v1/jobs/claim",
            json={
                "worker_id": "test-worker",
                "lease_seconds": 60,
            },
        )
        assert response.status_code == 200
        claim = JobClaimResponse.model_validate_json(response.text)
        assert claim.job is None

    def test_complete_job(self, client: TestClient) -> None:
        """Test job completion."""
        # Create and claim a job
        _ = client.post(
            "/api/v1/jobs",
            json={"command_argv": ["echo", "test"], "workdir": "/tmp"},
        )
        claim = client.post(
            "/api/v1/jobs/claim",
            json={"worker_id": "test-worker", "lease_seconds": 60},
        )
        claim_payload = JobClaimResponse.model_validate_json(claim.text)
        assert claim_payload.job is not None
        job_id = claim_payload.job.id

        # Complete the job
        response = client.post(
            f"/api/v1/jobs/{job_id}/complete",
            json={
                "worker_id": "test-worker",
                "exit_code": 0,
            },
        )
        assert response.status_code == 200
        payload = MessageResponse.model_validate_json(response.text)
        assert "completed" in payload.message

    def test_get_status(self, client: TestClient) -> None:
        """Test status endpoint."""
        response = client.get("/api/v1/status")
        assert response.status_code == 200
        payload = StatusResponse.model_validate_json(response.text)
        assert payload.queued >= 0
        assert payload.running >= 0
        assert payload.workers_online >= 0

    def test_get_run_metrics(self, client: TestClient, tmp_path: Path) -> None:
        """Test get run metrics endpoint."""
        import json

        # Create a run in the database
        from whirr.server.app import get_db
        db = get_db()
        db.create_run(
            run_id="test-run-metrics",
            run_dir=str(tmp_path / "runs" / "test-run-metrics"),
            name="Test Run",
        )

        # Create metrics file
        run_dir = tmp_path / "runs" / "test-run-metrics"
        run_dir.mkdir(parents=True)
        metrics_file = run_dir / "metrics.jsonl"
        _ = metrics_file.write_text(
            "\n".join(
                [
                    json.dumps({"step": 0, "loss": 1.0}),
                    json.dumps({"step": 1, "loss": 0.5}),
                    json.dumps({"step": 2, "loss": 0.25}),
                ]
            )
            + "\n"
        )

        # Get metrics via API
        response = client.get("/api/v1/runs/test-run-metrics/metrics")
        assert response.status_code == 200
        payload = RunMetricsResponse.model_validate_json(response.text)
        assert payload.count == 3
        assert len(payload.metrics) == 3
        metric_first = payload.metrics[0].model_dump()
        metric_last = payload.metrics[2].model_dump()
        assert metric_first["loss"] == 1.0
        assert metric_last["loss"] == 0.25

    def test_get_run_metrics_not_found(self, client: TestClient) -> None:
        """Test get metrics for non-existent run."""
        response = client.get("/api/v1/runs/nonexistent/metrics")
        assert response.status_code == 404

    def test_get_run_metrics_no_file(self, client: TestClient, tmp_path: Path) -> None:
        """Test get metrics when no metrics file exists."""
        from whirr.server.app import get_db
        db = get_db()
        db.create_run(
            run_id="test-run-no-metrics",
            run_dir=str(tmp_path / "runs" / "test-run-no-metrics"),
            name="Test Run",
        )

        response = client.get("/api/v1/runs/test-run-no-metrics/metrics")
        assert response.status_code == 200
        payload = RunMetricsResponse.model_validate_json(response.text)
        assert payload.count == 0
        assert payload.metrics == []

    def test_create_job_returns_run_id(self, client: TestClient) -> None:
        """Test job creation returns run_id and run_dir."""
        response = client.post(
            "/api/v1/jobs",
            json={
                "command_argv": ["python", "test.py"],
                "workdir": "/tmp",
                "name": "test-job",
            },
        )
        assert response.status_code == 200
        payload = JobCreateResponse.model_validate_json(response.text)
        assert payload.run_id == f"job-{payload.job_id}"

    def test_list_artifacts(self, client: TestClient, tmp_path: Path) -> None:
        """Test list artifacts endpoint."""
        from whirr.server.app import get_db

        # Create a run with some files
        run_dir = tmp_path / "runs" / "test-artifacts"
        run_dir.mkdir(parents=True)
        _ = (run_dir / "metrics.jsonl").write_text('{"loss": 0.5}\n')
        _ = (run_dir / "output.log").write_text("some output\n")
        artifacts_dir = run_dir / "artifacts"
        artifacts_dir.mkdir()
        _ = (artifacts_dir / "model.pt").write_bytes(b"model data")

        db = get_db()
        db.create_run(
            run_id="test-artifacts",
            run_dir=str(run_dir),
            name="Test Run",
        )

        response = client.get("/api/v1/runs/test-artifacts/artifacts")
        assert response.status_code == 200
        payload = RunArtifactsResponse.model_validate_json(response.text)
        assert payload.count == 3

        paths = [artifact.path for artifact in payload.artifacts]
        assert "metrics.jsonl" in paths
        assert "output.log" in paths
        assert "artifacts/model.pt" in paths

    def test_list_artifacts_not_found(self, client: TestClient) -> None:
        """Test list artifacts for non-existent run."""
        response = client.get("/api/v1/runs/nonexistent/artifacts")
        assert response.status_code == 404

    def test_get_artifact(self, client: TestClient, tmp_path: Path) -> None:
        """Test get artifact endpoint."""
        from whirr.server.app import get_db

        # Create a run with a file
        run_dir = tmp_path / "runs" / "test-get-artifact"
        run_dir.mkdir(parents=True)
        _ = (run_dir / "output.log").write_text("hello world\n")

        db = get_db()
        db.create_run(
            run_id="test-get-artifact",
            run_dir=str(run_dir),
            name="Test Run",
        )

        response = client.get("/api/v1/runs/test-get-artifact/artifacts/output.log")
        assert response.status_code == 200
        assert response.content == b"hello world\n"

    def test_get_artifact_not_found(self, client: TestClient, tmp_path: Path) -> None:
        """Test get artifact that doesn't exist."""
        from whirr.server.app import get_db

        run_dir = tmp_path / "runs" / "test-artifact-missing"
        run_dir.mkdir(parents=True)

        db = get_db()
        db.create_run(
            run_id="test-artifact-missing",
            run_dir=str(run_dir),
            name="Test Run",
        )

        response = client.get("/api/v1/runs/test-artifact-missing/artifacts/missing.txt")
        assert response.status_code == 404

    def test_get_artifact_path_traversal(self, client: TestClient, tmp_path: Path) -> None:
        """Test get artifact blocks path traversal."""
        from whirr.server.app import get_db

        run_dir = tmp_path / "runs" / "test-traversal"
        run_dir.mkdir(parents=True)

        db = get_db()
        db.create_run(
            run_id="test-traversal",
            run_dir=str(run_dir),
            name="Test Run",
        )

        # Use percent-encoded dots (%2e) to bypass URL normalization
        # that would otherwise collapse the path before reaching the server
        response = client.get("/api/v1/runs/test-traversal/artifacts/%2e%2e/%2e%2e/%2e%2e/etc/passwd")
        assert response.status_code == 403


class TestServerCLI:
    """Tests for server CLI command."""

    def test_server_help(self) -> None:
        """Test server command shows help."""
        from typer.testing import CliRunner

        from whirr.cli.main import app

        runner = CliRunner()
        result = runner.invoke(app, ["server", "--help"])
        assert result.exit_code == 0
        assert "Start the whirr server" in result.output


class TestWorkerRemoteMode:
    """Tests for worker remote mode."""

    def test_worker_help_shows_server_option(self) -> None:
        """Test worker help shows --server option."""
        from typer.testing import CliRunner

        from whirr.cli.main import app

        runner = CliRunner()
        result = runner.invoke(app, ["worker", "--help"])
        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "--server" in output
        assert "--data-dir" in output


class TestSubmitRemoteMode:
    """Tests for submit remote mode."""

    def test_submit_help_shows_server_option(self) -> None:
        """Test submit help shows --server option."""
        from typer.testing import CliRunner

        from whirr.cli.main import app

        runner = CliRunner()
        result = runner.invoke(app, ["submit", "--help"])
        assert result.exit_code == 0
        output = strip_ansi(result.output)
        assert "--server" in output


class TestModels:
    """Tests for Pydantic models."""

    def test_job_claim_validation(self) -> None:
        """Test JobClaim model validation."""
        from whirr.server.models import JobClaim

        # Valid claim
        claim = JobClaim(worker_id="test", lease_seconds=60)
        assert claim.worker_id == "test"

        # Lease too short
        with pytest.raises(ValueError, match="lease_seconds"):
            _ = JobClaim(worker_id="test", lease_seconds=5)

        # Lease too long
        with pytest.raises(ValueError, match="lease_seconds"):
            _ = JobClaim(worker_id="test", lease_seconds=1000)

    def test_job_create_required_fields(self) -> None:
        """Test JobCreate model required fields."""
        from whirr.server.models import JobCreate

        job = JobCreate(
            command_argv=["python", "test.py"],
            workdir="/tmp",
        )
        assert job.command_argv == ["python", "test.py"]
        assert job.name is None  # Optional


class TestBackwardCompatibility:
    """Tests to ensure backward compatibility with v0.3 code."""

    def test_legacy_db_functions_work(self, tmp_path: Path) -> None:
        """Test that legacy DB functions still work."""
        from whirr.db import (
            claim_job,
            complete_job,
            create_job,
            get_connection,
            get_job,
            init_db,
        )

        db_path = tmp_path / "test.db"
        init_db(db_path)

        conn = get_connection(db_path)
        try:
            job_id = create_job(conn, ["echo", "hello"], "/tmp", name="test")
            assert job_id == 1

            job = JobRecord.model_validate(claim_job(conn, "worker-1"))
            assert job.id == job_id

            complete_job(conn, job_id, exit_code=0)
            job = JobRecord.model_validate(get_job(conn, job_id))
            assert job.status == "completed"
        finally:
            conn.close()

    def test_schema_alias(self) -> None:
        """Test SCHEMA alias for backward compatibility."""
        from whirr.db import SCHEMA
        assert SCHEMA == SQLITE_SCHEMA
