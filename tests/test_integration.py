# Copyright (c) Syntropy Systems
"""Integration tests for whirr - end-to-end workflows."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from whirr.db import (
    claim_job,
    create_job,
    get_connection,
)
from whirr.models.run import RunMeta
from whirr.run import Run, read_metrics
from whirr.runner import JobRunner

if TYPE_CHECKING:
    from whirr.models.db import JobRecord


class TestWorkerEndToEnd:
    """Test that jobs actually run through the worker system."""

    def test_job_runs_and_produces_output(self, whirr_project: Path) -> None:
        """Test that a submitted job actually executes and produces output."""
        db_path = whirr_project / ".whirr" / "whirr.db"
        runs_dir = whirr_project / ".whirr" / "runs"

        # Create a simple job that writes to a file
        output_file = whirr_project / "test_output.txt"
        conn = get_connection(db_path)
        job_id = create_job(
            conn,
            command_argv=[sys.executable, "-c", f"print('hello from job'); open('{output_file}', 'w').write('success')"],
            workdir=str(whirr_project),
            name="test-job",
        )
        conn.close()

        # Claim the job (simulating worker)
        conn = get_connection(db_path)
        job: JobRecord | None = claim_job(conn, "test-worker")
        conn.close()

        assert job is not None
        assert job.id == job_id

        # Run the job using JobRunner
        run_dir = runs_dir / f"job-{job_id}"
        run_dir.mkdir(parents=True)

        runner = JobRunner(
            command_argv=job.command_argv,
            workdir=Path(job.workdir),
            run_dir=run_dir,
        )
        runner.start()
        exit_code = runner.wait()

        # Verify job completed
        assert exit_code == 0
        assert output_file.exists()
        assert output_file.read_text() == "success"

        # Verify output.log captured stdout
        log_path = run_dir / "output.log"
        assert log_path.exists()
        assert "hello from job" in log_path.read_text()

    def test_job_captures_stderr(self, whirr_project: Path) -> None:
        """Test that stderr is captured in output.log."""
        db_path = whirr_project / ".whirr" / "whirr.db"
        runs_dir = whirr_project / ".whirr" / "runs"

        conn = get_connection(db_path)
        job_id = create_job(
            conn,
            command_argv=[sys.executable, "-c", "import sys; sys.stderr.write('error output\n')"],
            workdir=str(whirr_project),
            name="stderr-test",
        )
        conn.close()

        conn = get_connection(db_path)
        job: JobRecord | None = claim_job(conn, "test-worker")
        conn.close()
        assert job is not None

        run_dir = runs_dir / f"job-{job_id}"
        run_dir.mkdir(parents=True)

        runner = JobRunner(
            command_argv=job.command_argv,
            workdir=Path(job.workdir),
            run_dir=run_dir,
        )
        runner.start()
        _ = runner.wait()

        log_path = run_dir / "output.log"
        assert "error output" in log_path.read_text()

    def test_job_nonzero_exit_code(self, whirr_project: Path) -> None:
        """Test that non-zero exit codes are captured."""
        db_path = whirr_project / ".whirr" / "whirr.db"
        runs_dir = whirr_project / ".whirr" / "runs"

        conn = get_connection(db_path)
        job_id = create_job(
            conn,
            command_argv=[sys.executable, "-c", "import sys; sys.exit(42)"],
            workdir=str(whirr_project),
            name="exit-code-test",
        )
        conn.close()

        conn = get_connection(db_path)
        job: JobRecord | None = claim_job(conn, "test-worker")
        conn.close()
        assert job is not None

        run_dir = runs_dir / f"job-{job_id}"
        run_dir.mkdir(parents=True)

        runner = JobRunner(
            command_argv=job.command_argv,
            workdir=Path(job.workdir),
            run_dir=run_dir,
        )
        runner.start()
        exit_code = runner.wait()

        assert exit_code == 42


class TestCancelRunningJob:
    """Test that cancellation actually kills running processes."""

    def test_cancel_kills_running_process(self, whirr_project: Path) -> None:
        """Test that cancelling a running job terminates the process."""
        db_path = whirr_project / ".whirr" / "whirr.db"
        runs_dir = whirr_project / ".whirr" / "runs"

        # Create a long-running job
        conn = get_connection(db_path)
        job_id = create_job(
            conn,
            command_argv=[sys.executable, "-c", "import time; time.sleep(60)"],
            workdir=str(whirr_project),
            name="long-running",
        )
        conn.close()

        conn = get_connection(db_path)
        job: JobRecord | None = claim_job(conn, "test-worker")
        conn.close()
        assert job is not None

        run_dir = runs_dir / f"job-{job_id}"
        run_dir.mkdir(parents=True)

        runner = JobRunner(
            command_argv=job.command_argv,
            workdir=Path(job.workdir),
            run_dir=run_dir,
        )
        runner.start()

        # Give it a moment to start
        time.sleep(0.5)
        assert runner.is_running

        # Kill the job (simulating cancel)
        exit_code = runner.kill(grace_period=1.0)

        # Process should be dead
        assert not runner.is_running
        # Exit code should indicate termination (negative on Unix = signal)
        assert exit_code != 0

    def test_cancel_kills_entire_process_group(self, whirr_project: Path) -> None:
        """Test that cancel kills child processes too (process group)."""
        db_path = whirr_project / ".whirr" / "whirr.db"
        runs_dir = whirr_project / ".whirr" / "runs"

        # Create a job that spawns a subprocess
        # The parent sleeps, child writes to file every 0.1s
        marker_file = whirr_project / "child_alive.txt"
        script = f"""
import subprocess
import time
import sys

# Spawn a child that keeps writing
child = subprocess.Popen([
    sys.executable, '-c',
    "import time; f=open('{marker_file}', 'a'); [f.write(str(i)+'\\\\\\\\n') or f.flush() or time.sleep(0.1) for i in range(1000)]"
])

# Parent just waits
time.sleep(60)
"""
        conn = get_connection(db_path)
        job_id = create_job(
            conn,
            command_argv=[sys.executable, "-c", script],
            workdir=str(whirr_project),
            name="parent-child",
        )
        conn.close()

        conn = get_connection(db_path)
        job: JobRecord | None = claim_job(conn, "test-worker")
        conn.close()
        assert job is not None

        run_dir = runs_dir / f"job-{job_id}"
        run_dir.mkdir(parents=True)

        runner = JobRunner(
            command_argv=job.command_argv,
            workdir=Path(job.workdir),
            run_dir=run_dir,
        )
        runner.start()

        # Wait for child to start writing
        time.sleep(0.5)
        assert marker_file.exists(), "Child process should have started writing"

        # Record current file size
        _ = marker_file.stat().st_size

        # Kill the job
        _ = runner.kill(grace_period=1.0)

        # Wait a bit and check file stopped growing (child is dead)
        time.sleep(0.5)
        final_size = marker_file.stat().st_size

        # File should have stopped growing (child killed with parent)
        # Allow small tolerance for buffered writes
        time.sleep(0.3)
        after_wait_size = marker_file.stat().st_size

        assert after_wait_size == final_size, "Child process should be dead (file stopped growing)"


class TestDirectRun:
    """Test running whirr.init() directly without job queue."""

    def test_direct_run_creates_files(self, whirr_project: Path) -> None:
        """Test that a direct run (no job) creates the expected files."""
        # Create a run directly
        _ = whirr_project
        run = Run(name="direct-test", config={"lr": 0.01})

        # Log some metrics
        run.log({"loss": 1.0}, step=0)
        run.log({"loss": 0.5}, step=1)
        run.summary({"best_loss": 0.5})

        run.finish()

        # Verify files exist
        assert run.run_dir.exists()
        assert (run.run_dir / "meta.json").exists()
        assert (run.run_dir / "config.json").exists()
        assert (run.run_dir / "metrics.jsonl").exists()

        # Verify metrics
        metrics = read_metrics(run.run_dir / "metrics.jsonl")
        assert len(metrics) == 2
        first = metrics[0].model_dump()
        second = metrics[1].model_dump()
        assert first["loss"] == 1.0
        assert second["loss"] == 0.5

    def test_direct_run_id_format(self, whirr_project: Path) -> None:
        """Test that direct runs get local-* ID format."""
        _ = whirr_project
        run = Run(name="id-test")

        assert run.run_id.startswith("local-")

        run.finish()

    def test_direct_run_context_manager(self, whirr_project: Path) -> None:
        """Test direct run with context manager."""
        _ = whirr_project
        with Run(name="context-test") as run:
            run.log({"value": 42})

        # Should be marked completed
        meta = RunMeta.model_validate_json((run.run_dir / "meta.json").read_text())
        assert meta.status == "completed"

    def test_direct_run_exception_marks_failed(self, whirr_project: Path) -> None:
        """Test that exceptions in context manager mark run as failed."""
        _ = whirr_project
        run_holder: list[Run] = []

        def run_and_fail() -> None:
            with Run(name="fail-test") as run:
                run_holder.append(run)
                run.log({"value": 1})
                msg = "intentional error"
                if run_holder:
                    raise ValueError(msg)

        with pytest.raises(ValueError, match="intentional error"):
            run_and_fail()

        assert run_holder
        run_ref = run_holder[0]
        meta = RunMeta.model_validate_json((run_ref.run_dir / "meta.json").read_text())
        assert meta.status == "failed"


class TestLogsFollow:
    """Test the logs --follow functionality."""

    def test_logs_captures_live_output(self, whirr_project: Path) -> None:
        """Test that output.log captures output as it's written."""
        db_path = whirr_project / ".whirr" / "whirr.db"
        runs_dir = whirr_project / ".whirr" / "runs"

        # Job that writes output over time
        script = """
import time
import sys
for i in range(5):
    print(f"line {i}", flush=True)
    time.sleep(0.1)
"""
        conn = get_connection(db_path)
        job_id = create_job(
            conn,
            command_argv=[sys.executable, "-c", script],
            workdir=str(whirr_project),
            name="streaming-output",
        )
        conn.close()

        conn = get_connection(db_path)
        job: JobRecord | None = claim_job(conn, "test-worker")
        conn.close()
        assert job is not None

        run_dir = runs_dir / f"job-{job_id}"
        run_dir.mkdir(parents=True)

        runner = JobRunner(
            command_argv=job.command_argv,
            workdir=Path(job.workdir),
            run_dir=run_dir,
        )
        runner.start()

        log_path = run_dir / "output.log"

        # Wait for log file to appear
        for _ in range(20):
            if log_path.exists():
                break
            time.sleep(0.1)

        assert log_path.exists(), "Log file should be created"

        # Wait for job to complete
        _ = runner.wait()

        # Verify all output captured
        content = log_path.read_text()
        for i in range(5):
            assert f"line {i}" in content


class TestWorkerWorkdir:
    """Test that jobs run in the correct working directory."""

    def test_job_runs_in_specified_workdir(self, whirr_project: Path) -> None:
        """Test that job executes in the workdir specified."""
        db_path = whirr_project / ".whirr" / "whirr.db"
        runs_dir = whirr_project / ".whirr" / "runs"

        # Create a subdirectory to use as workdir
        subdir = whirr_project / "subproject"
        subdir.mkdir()

        # Job that writes cwd to a file
        output_file = whirr_project / "cwd_output.txt"
        script = f"import os; open('{output_file}', 'w').write(os.getcwd())"

        conn = get_connection(db_path)
        job_id = create_job(
            conn,
            command_argv=[sys.executable, "-c", script],
            workdir=str(subdir),  # Run in subdir
            name="workdir-test",
        )
        conn.close()

        conn = get_connection(db_path)
        job: JobRecord | None = claim_job(conn, "test-worker")
        conn.close()
        assert job is not None

        run_dir = runs_dir / f"job-{job_id}"
        run_dir.mkdir(parents=True)

        runner = JobRunner(
            command_argv=job.command_argv,
            workdir=Path(job.workdir),
            run_dir=run_dir,
        )
        runner.start()
        _ = runner.wait()

        # Verify job ran in the correct directory
        # Use resolve() to handle macOS /var -> /private/var symlink
        recorded_cwd = Path(output_file.read_text()).resolve()
        expected_cwd = subdir.resolve()
        assert recorded_cwd == expected_cwd
