# Copyright (c) Syntropy Systems
"""Tests for whirr database operations."""

import sqlite3
from pathlib import Path

from whirr.db import (
    cancel_job,
    claim_job,
    complete_job,
    complete_run,
    create_job,
    create_run,
    get_active_jobs,
    get_job,
    get_run,
    get_runs,
    get_workers,
    register_worker,
    unregister_worker,
)


class TestJobOperations:
    """Tests for job CRUD operations."""

    def test_create_job(self, db_connection: sqlite3.Connection) -> None:
        """Test creating a job."""
        job_id = create_job(
            db_connection,
            command_argv=["python", "train.py"],
            workdir="/tmp/test",
            name="test-job",
            tags=["test", "unit"],
        )

        assert job_id == 1

        job = get_job(db_connection, job_id)
        assert job is not None
        assert job.name == "test-job"
        assert job.status == "queued"
        assert job.workdir == "/tmp/test"

    def test_claim_job(self, db_connection: sqlite3.Connection) -> None:
        """Test atomic job claiming."""
        # Create a job
        _ = create_job(
            db_connection,
            command_argv=["python", "test.py"],
            workdir="/tmp/test",
            name="claim-test",
        )

        # Claim it
        job = claim_job(db_connection, "worker-1")

        assert job is not None
        assert job.command_argv == ["python", "test.py"]
        assert job.workdir == "/tmp/test"

        # Job should now be running
        updated = get_job(db_connection, job.id)
        assert updated is not None
        assert updated.status == "running"
        assert updated.worker_id == "worker-1"

    def test_claim_empty_queue(self, db_connection: sqlite3.Connection) -> None:
        """Test claiming when no jobs available."""
        job = claim_job(db_connection, "worker-1")
        assert job is None

    def test_claim_concurrent(self, db_connection: sqlite3.Connection) -> None:
        """Test that only one worker can claim a job."""
        # Create one job
        _ = create_job(
            db_connection,
            command_argv=["python", "test.py"],
            workdir="/tmp/test",
        )

        # First claim succeeds
        job1 = claim_job(db_connection, "worker-1")
        assert job1 is not None

        # Second claim gets nothing
        job2 = claim_job(db_connection, "worker-2")
        assert job2 is None

    def test_complete_job_success(self, db_connection: sqlite3.Connection) -> None:
        """Test completing a job successfully."""
        job_id = create_job(
            db_connection,
            command_argv=["python", "test.py"],
            workdir="/tmp/test",
        )
        _ = claim_job(db_connection, "worker-1")

        complete_job(db_connection, job_id, exit_code=0, run_id="run-123")

        job = get_job(db_connection, job_id)
        assert job is not None
        assert job.status == "completed"
        assert job.exit_code == 0
        assert job.run_id == "run-123"

    def test_complete_job_failure(self, db_connection: sqlite3.Connection) -> None:
        """Test completing a failed job."""
        job_id = create_job(
            db_connection,
            command_argv=["python", "test.py"],
            workdir="/tmp/test",
        )
        _ = claim_job(db_connection, "worker-1")

        complete_job(
            db_connection,
            job_id,
            exit_code=1,
            error_message="Out of memory",
        )

        job = get_job(db_connection, job_id)
        assert job is not None
        assert job.status == "failed"
        assert job.exit_code == 1
        assert job.error_message == "Out of memory"

    def test_cancel_queued_job(self, db_connection: sqlite3.Connection) -> None:
        """Test cancelling a queued job."""
        job_id = create_job(
            db_connection,
            command_argv=["python", "test.py"],
            workdir="/tmp/test",
        )

        old_status = cancel_job(db_connection, job_id)

        assert old_status == "queued"
        job = get_job(db_connection, job_id)
        assert job is not None
        assert job.status == "cancelled"

    def test_cancel_running_job(self, db_connection: sqlite3.Connection) -> None:
        """Test cancelling a running job sets cancel_requested_at flag."""
        job_id = create_job(
            db_connection,
            command_argv=["python", "test.py"],
            workdir="/tmp/test",
        )
        _ = claim_job(db_connection, "worker-1")

        old_status = cancel_job(db_connection, job_id)

        assert old_status == "running"
        job = get_job(db_connection, job_id)
        assert job is not None
        assert job.status == "running"  # Still running
        assert job.cancel_requested_at is not None

    def test_get_active_jobs(self, db_connection: sqlite3.Connection) -> None:
        """Test getting active jobs."""
        _ = create_job(db_connection, command_argv=["cmd1"], workdir="/tmp", name="job1")
        _ = create_job(db_connection, command_argv=["cmd2"], workdir="/tmp", name="job2")
        job3_id = create_job(db_connection, command_argv=["cmd3"], workdir="/tmp", name="job3")

        # Claim and complete one
        _ = claim_job(db_connection, "worker-1")
        complete_job(db_connection, job3_id, exit_code=0)

        active = get_active_jobs(db_connection)

        # Should have 2 jobs (one running, one queued)
        assert len(active) == 2


class TestRunOperations:
    """Tests for run CRUD operations."""

    def test_create_run(self, db_connection: sqlite3.Connection, temp_dir: Path) -> None:
        """Test creating a run."""
        run_dir = temp_dir / "runs" / "test-run"
        run_dir.mkdir(parents=True)

        create_run(
            db_connection,
            run_id="test-run",
            run_dir=str(run_dir),
            name="Test Run",
            config={"lr": 0.01},
            tags=["test"],
        )

        run = get_run(db_connection, "test-run")
        assert run is not None
        assert run.name == "Test Run"
        assert run.status == "running"

    def test_complete_run(self, db_connection: sqlite3.Connection, temp_dir: Path) -> None:
        """Test completing a run."""
        run_dir = temp_dir / "runs" / "test-run"
        run_dir.mkdir(parents=True)

        create_run(
            db_connection,
            run_id="test-run",
            run_dir=str(run_dir),
            name="Test Run",
        )

        complete_run(
            db_connection,
            run_id="test-run",
            status="completed",
            summary={"loss": 0.1, "accuracy": 0.95},
        )

        run = get_run(db_connection, "test-run")
        assert run is not None
        assert run.status == "completed"
        assert run.finished_at is not None

    def test_get_runs_with_filters(self, db_connection: sqlite3.Connection, temp_dir: Path) -> None:
        """Test filtering runs."""
        for i in range(3):
            run_dir = temp_dir / "runs" / f"run-{i}"
            run_dir.mkdir(parents=True)
            create_run(
                db_connection,
                run_id=f"run-{i}",
                run_dir=str(run_dir),
                name=f"Run {i}",
                tags=["test"] if i < 2 else ["prod"],
            )

        # Complete one
        complete_run(db_connection, "run-0", "completed")

        # Filter by status
        completed = get_runs(db_connection, status="completed")
        assert len(completed) == 1

        # Filter by tag
        test_runs = get_runs(db_connection, tag="test")
        assert len(test_runs) == 2


class TestWorkerOperations:
    """Tests for worker registration."""

    def test_register_worker(self, db_connection: sqlite3.Connection) -> None:
        """Test worker registration."""
        register_worker(db_connection, "host:gpu0", 1234, "host", 0)

        workers = get_workers(db_connection)
        assert len(workers) == 1
        assert workers[0].id == "host:gpu0"
        assert workers[0].pid == 1234
        assert workers[0].status == "idle"

    def test_unregister_worker(self, db_connection: sqlite3.Connection) -> None:
        """Test worker unregistration."""
        register_worker(db_connection, "host:gpu0", 1234, "host", 0)
        unregister_worker(db_connection, "host:gpu0")

        workers = get_workers(db_connection)
        assert workers[0].status == "offline"
