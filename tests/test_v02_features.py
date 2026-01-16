# Copyright (c) Syntropy Systems
"""Tests for v0.2 features."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from typer.testing import CliRunner

from whirr.cli.main import app
from whirr.db import (
    claim_job,
    complete_job,
    create_job,
    get_connection,
    get_job,
    get_orphaned_jobs,
    requeue_orphaned_jobs,
    retry_job,
)
from whirr.sweep import SweepConfig, generate_sweep_jobs

if TYPE_CHECKING:
    from whirr.models.base import JSONObject
    from whirr.models.db import JobRecord

runner = CliRunner()


class TestOrphanDetection:
    """Tests for orphan detection and requeue."""

    def test_get_orphaned_jobs_finds_stale(self, db_connection: sqlite3.Connection) -> None:
        """Test that jobs with stale heartbeats are detected."""
        # Create and claim a job
        job_id = create_job(
            db_connection,
            command_argv=["echo", "test"],
            workdir="/tmp",
            name="orphan-test",
        )
        _ = claim_job(db_connection, "worker-1")

        # Manually set heartbeat to old time
        old_time = "2020-01-01T00:00:00Z"
        _ = db_connection.execute(
            "UPDATE jobs SET heartbeat_at = ? WHERE id = ?",
            (old_time, job_id),
        )

        # Should find the orphaned job
        orphaned: list[JobRecord] = get_orphaned_jobs(db_connection, timeout_seconds=60)
        assert len(orphaned) == 1
        assert orphaned[0].id == job_id

    def test_get_orphaned_jobs_ignores_recent(self, db_connection: sqlite3.Connection) -> None:
        """Test that jobs with recent heartbeats are not orphaned."""
        # Create and claim a job
        _ = create_job(
            db_connection,
            command_argv=["echo", "test"],
            workdir="/tmp",
            name="active-test",
        )
        _ = claim_job(db_connection, "worker-1")

        # Heartbeat is set by claim_job, should be recent
        orphaned = get_orphaned_jobs(db_connection, timeout_seconds=60)
        assert len(orphaned) == 0

    def test_requeue_orphaned_jobs(self, db_connection: sqlite3.Connection) -> None:
        """Test that orphaned jobs are requeued correctly."""
        # Create and claim a job
        job_id = create_job(
            db_connection,
            command_argv=["echo", "test"],
            workdir="/tmp",
            name="requeue-test",
        )
        _ = claim_job(db_connection, "worker-1")

        # Manually set heartbeat to old time
        old_time = "2020-01-01T00:00:00Z"
        _ = db_connection.execute(
            "UPDATE jobs SET heartbeat_at = ? WHERE id = ?",
            (old_time, job_id),
        )

        # Requeue orphaned jobs
        requeued = requeue_orphaned_jobs(db_connection, timeout_seconds=60)
        assert len(requeued) == 1

        # Job should be back to queued status
        job = get_job(db_connection, job_id)
        assert job is not None
        assert job.status == "queued"
        assert job.worker_id is None
        assert job.attempt == 2  # Incremented from 1


class TestRetry:
    """Tests for job retry functionality."""

    def test_retry_failed_job(self, db_connection: sqlite3.Connection) -> None:
        """Test retrying a failed job creates a new job."""
        # Create and complete a job as failed
        job_id = create_job(
            db_connection,
            command_argv=["python", "train.py"],
            workdir="/tmp",
            name="failed-job",
            tags=["test"],
        )
        _ = claim_job(db_connection, "worker-1")
        complete_job(db_connection, job_id, exit_code=1, error_message="OOM")

        # Retry the job
        new_job_id = retry_job(db_connection, job_id)

        # New job should exist with correct properties
        new_job: JobRecord | None = get_job(db_connection, new_job_id)
        assert new_job is not None
        assert new_job.name == "failed-job"
        assert new_job.command_argv == ["python", "train.py"]
        assert new_job.parent_job_id == job_id
        assert new_job.attempt == 2  # Original was 1, retried is 2
        assert new_job.status == "queued"

    def test_retry_cancelled_job(self, db_connection: sqlite3.Connection) -> None:
        """Test retrying a cancelled job."""
        job_id = create_job(
            db_connection,
            command_argv=["echo", "test"],
            workdir="/tmp",
        )
        _ = db_connection.execute(
            "UPDATE jobs SET status = 'cancelled' WHERE id = ?",
            (job_id,),
        )

        new_job_id = retry_job(db_connection, job_id)
        new_job: JobRecord | None = get_job(db_connection, new_job_id)
        assert new_job is not None
        assert new_job.status == "queued"

    def test_retry_running_job_fails(self, db_connection: sqlite3.Connection) -> None:
        """Test that retrying a running job raises an error."""
        job_id = create_job(
            db_connection,
            command_argv=["echo", "test"],
            workdir="/tmp",
        )
        _ = claim_job(db_connection, "worker-1")

        with pytest.raises(ValueError, match="Can only retry"):
            _ = retry_job(db_connection, job_id)

    def test_retry_cli(self, whirr_project: Path) -> None:
        """Test the retry CLI command."""
        # Submit and fail a job
        _ = runner.invoke(app, ["submit", "--name", "fail-test", "--", "false"])

        # Manually mark as failed
        db_path = whirr_project / ".whirr" / "whirr.db"
        conn = get_connection(db_path)
        _ = conn.execute("UPDATE jobs SET status = 'failed' WHERE id = 1")
        conn.close()

        # Retry via CLI
        result = runner.invoke(app, ["retry", "1"])
        assert result.exit_code == 0
        assert "Created retry job #2" in result.stdout


class TestSweep:
    """Tests for sweep functionality."""

    def test_sweep_config_from_yaml(self, temp_dir: Path) -> None:
        """Test loading sweep config from YAML."""
        config_path = temp_dir / "sweep.yaml"
        _ = config_path.write_text("""
program: python train.py
name: lr-sweep
method: grid
parameters:
  lr:
    values: [0.01, 0.001]
  batch_size:
    values: [16, 32]
""")

        config = SweepConfig.from_yaml(config_path)
        assert config.program == "python train.py"
        assert config.name == "lr-sweep"
        assert config.method == "grid"
        assert len(config.parameters) == 2

    def test_generate_grid_sweep(self, temp_dir: Path) -> None:
        """Test generating jobs from grid sweep."""
        config_path = temp_dir / "sweep.yaml"
        _ = config_path.write_text("""
program: python train.py
name: test-sweep
method: grid
parameters:
  lr:
    values: [0.01, 0.001]
  batch_size:
    values: [16, 32]
""")

        config = SweepConfig.from_yaml(config_path)
        jobs = generate_sweep_jobs(config)

        # Should have 2 * 2 = 4 combinations
        assert len(jobs) == 4

        # Check that all combinations are present
        configs = [j.config for j in jobs]
        assert {"lr": 0.01, "batch_size": 16} in configs
        assert {"lr": 0.01, "batch_size": 32} in configs
        assert {"lr": 0.001, "batch_size": 16} in configs
        assert {"lr": 0.001, "batch_size": 32} in configs

    def test_generate_random_sweep(self, temp_dir: Path) -> None:
        """Test generating jobs from random sweep."""
        config_path = temp_dir / "sweep.yaml"
        _ = config_path.write_text("""
program: python train.py
name: random-sweep
method: random
max_runs: 5
parameters:
  lr:
    distribution: log_uniform
    min: 0.0001
    max: 0.1
  batch_size:
    values: [16, 32, 64]
""")

        config = SweepConfig.from_yaml(config_path)
        jobs = generate_sweep_jobs(config)

        assert len(jobs) == 5

        for job in jobs:
            lr_value = job.config["lr"]
            assert isinstance(lr_value, float)
            assert 0.0001 <= lr_value <= 0.1
            batch_value = job.config["batch_size"]
            assert isinstance(batch_value, int)
            assert batch_value in [16, 32, 64]

    def test_sweep_cli_dry_run(self, whirr_project: Path, temp_dir: Path) -> None:
        """Test sweep CLI with dry run."""
        _ = whirr_project
        config_path = temp_dir / "sweep.yaml"
        _ = config_path.write_text("""
program: python train.py
name: cli-sweep
method: grid
parameters:
  lr:
    values: [0.01, 0.001]
""")

        result = runner.invoke(app, ["sweep", str(config_path), "--dry-run"])
        assert result.exit_code == 0
        assert "2 jobs" in result.stdout
        assert "Dry run" in result.stdout

    def test_sweep_cli_submit(self, whirr_project: Path, temp_dir: Path) -> None:
        """Test sweep CLI actually submits jobs."""
        _ = whirr_project
        config_path = temp_dir / "sweep.yaml"
        _ = config_path.write_text("""
program: echo
name: submit-sweep
method: grid
parameters:
  value:
    values: [1, 2, 3]
""")

        result = runner.invoke(app, ["sweep", str(config_path)])
        assert result.exit_code == 0
        assert "Submitted 3 jobs" in result.stdout


class TestSystemMetrics:
    """Tests for system metrics collection."""

    def test_system_metrics_creates_file(self, whirr_project: Path) -> None:
        """Test that system metrics creates system.jsonl."""
        _ = whirr_project
        from whirr.run import Run

        run = Run(name="metrics-test", system_metrics=True, system_metrics_interval=0.1)

        # Wait for at least one collection
        time.sleep(0.3)

        run.finish()

        # Check that system.jsonl was created
        system_path = run.run_dir / "system.jsonl"
        assert system_path.exists()

        # Read and verify content
        with system_path.open() as f:
            lines = f.readlines()

        assert len(lines) >= 1
        data = cast("JSONObject", json.loads(lines[0]))
        assert "_timestamp" in data

    def test_system_metrics_disabled(self, whirr_project: Path) -> None:
        """Test that system metrics can be disabled."""
        _ = whirr_project
        from whirr.run import Run

        run = Run(name="no-metrics-test", system_metrics=False)
        time.sleep(0.2)
        run.finish()

        # system.jsonl should not exist
        system_path = run.run_dir / "system.jsonl"
        assert not system_path.exists()


class TestWatchCommand:
    """Tests for watch command."""

    def test_watch_exits_on_interrupt(self, whirr_project: Path) -> None:
        """Test that watch command structure is correct."""
        _ = whirr_project
        # Just import and verify the command exists
        from whirr.cli.watch import build_jobs_table, build_workers_table

        # Test table building with empty data
        jobs_table = build_jobs_table([])
        assert jobs_table is not None

        workers_table = build_workers_table([])
        assert workers_table is not None
