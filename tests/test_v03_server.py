"""Tests for v0.3 server mode features."""

import re
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock


def strip_ansi(text: str) -> str:
    """Strip ANSI escape codes from text."""
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


from whirr.db import (
    Database,
    SQLiteDatabase,
    get_database,
    SQLITE_SCHEMA,
    POSTGRES_SCHEMA,
)


class TestDatabaseAbstraction:
    """Tests for the Database abstract base class and implementations."""

    def test_sqlite_database_creation(self, tmp_path):
        """Test SQLiteDatabase can be created and initialized."""
        db_path = tmp_path / "test.db"
        db = SQLiteDatabase(db_path)
        try:
            db.init_schema()
            # Verify schema was created
            row = db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'"
            ).fetchone()
            assert row is not None
        finally:
            db.close()

    def test_sqlite_create_and_claim_job(self, tmp_path):
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
            job = db.claim_job("worker-1")
            assert job is not None
            assert job["id"] == job_id
            assert job["command_argv"] == ["python", "test.py"]
            assert job["name"] == "test-job"

            # No more jobs to claim
            job2 = db.claim_job("worker-2")
            assert job2 is None
        finally:
            db.close()

    def test_sqlite_complete_job(self, tmp_path):
        """Test SQLiteDatabase complete_job."""
        db_path = tmp_path / "test.db"
        db = SQLiteDatabase(db_path)
        try:
            db.init_schema()

            job_id = db.create_job(["echo", "hello"], "/tmp")
            db.claim_job("worker-1")
            db.complete_job(job_id, exit_code=0)

            job = db.get_job(job_id)
            assert job["status"] == "completed"
            assert job["exit_code"] == 0
        finally:
            db.close()

    def test_sqlite_run_operations(self, tmp_path):
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
            run = db.get_run("test-run-123")
            assert run is not None
            assert run["id"] == "test-run-123"
            assert run["name"] == "Test Run"
            assert run["status"] == "running"

            # Complete the run
            db.complete_run("test-run-123", "completed", {"final_loss": 0.1})
            run = db.get_run("test-run-123")
            assert run["status"] == "completed"
        finally:
            db.close()

    def test_sqlite_worker_operations(self, tmp_path):
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
            workers = db.get_workers()
            assert len(workers) == 1
            assert workers[0]["id"] == "worker-1"
            assert workers[0]["hostname"] == "test-host"

            # Update status
            db.update_worker_status("worker-1", "busy", current_job_id=1)
            workers = db.get_workers()
            assert workers[0]["status"] == "busy"

            # Unregister
            db.unregister_worker("worker-1")
            workers = db.get_workers()
            assert workers[0]["status"] == "offline"
        finally:
            db.close()

    def test_get_database_factory_sqlite(self, tmp_path):
        """Test get_database factory with SQLite."""
        db_path = tmp_path / "test.db"
        db = get_database(db_path=db_path)
        try:
            assert isinstance(db, SQLiteDatabase)
        finally:
            db.close()

    def test_get_database_requires_config(self):
        """Test get_database raises error without config."""
        with pytest.raises(ValueError, match="Either db_path or connection_url"):
            get_database()


class TestClientModule:
    """Tests for the HTTP client module."""

    def test_client_import(self):
        """Test client module can be imported."""
        from whirr.client import WhirrClient, WhirrClientError, get_client
        assert WhirrClient is not None
        assert WhirrClientError is not None
        assert get_client is not None

    def test_client_requires_httpx(self):
        """Test client raises helpful error without httpx."""
        with patch.dict("sys.modules", {"httpx": None}):
            # Re-import to trigger ImportError
            import importlib
            import whirr.client
            importlib.reload(whirr.client)
            with pytest.raises(ImportError, match="httpx is required"):
                whirr.client.WhirrClient("http://localhost:8080")


class TestServerModule:
    """Tests for the server module."""

    def test_server_imports(self):
        """Test server module can be imported."""
        from whirr.server import create_app
        from whirr.server.models import (
            JobClaim,
            JobComplete,
            JobCreate,
            JobResponse,
            RunResponse,
            StatusResponse,
            WorkerRegistration,
            WorkerResponse,
        )
        assert create_app is not None
        assert JobClaim is not None

    def test_create_app_with_sqlite(self, tmp_path):
        """Test create_app with SQLite database."""
        from whirr.server.app import create_app

        db_path = tmp_path / "test.db"
        app = create_app(db_path=db_path)
        assert app is not None
        assert app.title == "whirr server"

    def test_server_routes_exist(self, tmp_path):
        """Test server has expected routes."""
        from whirr.server.app import create_app

        db_path = tmp_path / "test.db"
        app = create_app(db_path=db_path)

        routes = [route.path for route in app.routes]

        # Check key routes exist
        assert "/api/v1/workers/register" in routes
        assert "/api/v1/jobs/claim" in routes
        assert "/api/v1/jobs" in routes
        assert "/api/v1/runs" in routes
        assert "/api/v1/status" in routes
        assert "/health" in routes


# Check if httpx is available for TestClient
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


@pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")
class TestServerAPI:
    """Tests for server API endpoints using TestClient."""

    @pytest.fixture
    def client(self, tmp_path):
        """Create a test client."""
        from fastapi.testclient import TestClient
        from whirr.server.app import create_app

        db_path = tmp_path / "test.db"
        app = create_app(db_path=db_path)
        return TestClient(app)

    def test_health_check(self, client):
        """Test health check endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    def test_register_worker(self, client):
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
        assert "registered" in response.json()["message"]

    def test_create_and_claim_job(self, client):
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
        job_id = response.json()["job_id"]
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
        job = response.json()["job"]
        assert job is not None
        assert job["id"] == job_id

    def test_claim_no_jobs(self, client):
        """Test claiming when no jobs available."""
        response = client.post(
            "/api/v1/jobs/claim",
            json={
                "worker_id": "test-worker",
                "lease_seconds": 60,
            },
        )
        assert response.status_code == 200
        assert response.json()["job"] is None

    def test_complete_job(self, client):
        """Test job completion."""
        # Create and claim a job
        client.post(
            "/api/v1/jobs",
            json={"command_argv": ["echo", "test"], "workdir": "/tmp"},
        )
        claim = client.post(
            "/api/v1/jobs/claim",
            json={"worker_id": "test-worker", "lease_seconds": 60},
        )
        job_id = claim.json()["job"]["id"]

        # Complete the job
        response = client.post(
            f"/api/v1/jobs/{job_id}/complete",
            json={
                "worker_id": "test-worker",
                "exit_code": 0,
            },
        )
        assert response.status_code == 200
        assert "completed" in response.json()["message"]

    def test_get_status(self, client):
        """Test status endpoint."""
        response = client.get("/api/v1/status")
        assert response.status_code == 200
        data = response.json()
        assert "queued" in data
        assert "running" in data
        assert "workers_online" in data


class TestServerCLI:
    """Tests for server CLI command."""

    def test_server_help(self):
        """Test server command shows help."""
        from typer.testing import CliRunner
        from whirr.cli.main import app

        runner = CliRunner()
        result = runner.invoke(app, ["server", "--help"])
        assert result.exit_code == 0
        assert "Start the whirr server" in result.output


class TestWorkerRemoteMode:
    """Tests for worker remote mode."""

    def test_worker_help_shows_server_option(self):
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

    def test_submit_help_shows_server_option(self):
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

    def test_job_claim_validation(self):
        """Test JobClaim model validation."""
        from whirr.server.models import JobClaim

        # Valid claim
        claim = JobClaim(worker_id="test", lease_seconds=60)
        assert claim.worker_id == "test"

        # Lease too short
        with pytest.raises(ValueError):
            JobClaim(worker_id="test", lease_seconds=5)

        # Lease too long
        with pytest.raises(ValueError):
            JobClaim(worker_id="test", lease_seconds=1000)

    def test_job_create_required_fields(self):
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

    def test_legacy_db_functions_work(self, tmp_path):
        """Test that legacy DB functions still work."""
        from whirr.db import (
            init_db,
            get_connection,
            create_job,
            claim_job,
            complete_job,
            get_job,
        )

        db_path = tmp_path / "test.db"
        init_db(db_path)

        conn = get_connection(db_path)
        try:
            job_id = create_job(conn, ["echo", "hello"], "/tmp", name="test")
            assert job_id == 1

            job = claim_job(conn, "worker-1")
            assert job["id"] == job_id

            complete_job(conn, job_id, exit_code=0)
            job = get_job(conn, job_id)
            assert job["status"] == "completed"
        finally:
            conn.close()

    def test_schema_alias(self):
        """Test SCHEMA alias for backward compatibility."""
        from whirr.db import SCHEMA, SQLITE_SCHEMA
        assert SCHEMA == SQLITE_SCHEMA
