# Copyright (c) Syntropy Systems
"""Concurrency and stress tests for whirr."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from whirr.db import (
    claim_job,
    complete_job,
    create_job,
    get_connection,
    get_job,
)

if TYPE_CHECKING:
    from whirr.models.db import JobRecord


class TestConcurrentClaims:
    """Test concurrent job claiming by multiple workers."""

    def test_concurrent_claims_no_duplicates(self, whirr_project: Path) -> None:
        """Verify each job is claimed by exactly one worker."""
        db_path = whirr_project / ".whirr" / "whirr.db"
        num_jobs = 20
        num_workers = 4

        # Create jobs
        conn = get_connection(db_path)
        job_ids: list[int] = []
        for i in range(num_jobs):
            job_id = create_job(
                conn,
                command_argv=["echo", str(i)],
                workdir="/tmp",
                name=f"job-{i}",
            )
            job_ids.append(job_id)
        conn.close()

        # Track which worker claimed which jobs
        claims: dict[int, str] = {}
        errors: list[str] = []
        lock = threading.Lock()

        def worker_loop(worker_id: str) -> None:
            """Worker that claims and processes jobs."""
            while True:
                conn = get_connection(db_path)
                try:
                    job: JobRecord | None = claim_job(conn, worker_id)
                    if job is None:
                        break

                    with lock:
                        if job.id in claims:
                            errors.append(
                                f"Job {job.id} claimed by both {claims[job.id]} and {worker_id}"
                            )
                        claims[job.id] = worker_id

                    # Simulate work
                    time.sleep(0.01)

                    # Complete job
                    complete_job(conn, job.id, exit_code=0)
                except Exception as e:  # noqa: BLE001
                    with lock:
                        errors.append(str(e))
                finally:
                    conn.close()

        # Start workers
        threads: list[threading.Thread] = []
        for i in range(num_workers):
            t = threading.Thread(target=worker_loop, args=(f"worker-{i}",))
            threads.append(t)
            t.start()

        # Wait for completion
        for t in threads:
            t.join(timeout=10.0)

        # Verify results
        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(claims) == num_jobs, f"Expected {num_jobs} claims, got {len(claims)}"

        # Verify each job completed
        conn = get_connection(db_path)
        for job_id in job_ids:
            job = get_job(conn, job_id)
            assert job is not None
            assert job.status == "completed", f"Job {job_id} not completed: {job.status}"
        conn.close()

    def test_no_database_locked_errors(self, whirr_project: Path) -> None:
        """Verify no 'database locked' errors under concurrent load."""
        db_path = whirr_project / ".whirr" / "whirr.db"
        num_operations = 50
        num_threads = 8

        errors: list[str] = []
        lock = threading.Lock()

        def stress_db(thread_id: int) -> None:
            """Perform many database operations."""
            def run_operation(op_index: int) -> None:
                conn = None
                try:
                    conn = get_connection(db_path)
                    # Create job
                    _ = create_job(
                        conn,
                        command_argv=["echo", f"{thread_id}-{op_index}"],
                        workdir="/tmp",
                    )
                    # Claim it
                    job: JobRecord | None = claim_job(conn, f"worker-{thread_id}")
                    if job is not None:
                        complete_job(conn, job.id, exit_code=0)
                except Exception as e:  # noqa: BLE001
                    with lock:
                        errors.append(f"Thread {thread_id}, op {op_index}: {e}")
                finally:
                    if conn is not None:
                        conn.close()

            for i in range(num_operations):
                run_operation(i)

        # Start threads
        threads: list[threading.Thread] = []
        for i in range(num_threads):
            t = threading.Thread(target=stress_db, args=(i,))
            threads.append(t)
            t.start()

        # Wait for completion
        for t in threads:
            t.join(timeout=30.0)

        # Should have no errors
        assert len(errors) == 0, f"Database errors occurred: {errors[:5]}..."
