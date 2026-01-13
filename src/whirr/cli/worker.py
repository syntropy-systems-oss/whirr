"""whirr worker command."""

import os
import signal
import socket
import time
from pathlib import Path
from threading import Event, Thread
from typing import Optional

import typer
from rich.console import Console

from whirr.config import get_db_path, get_runs_dir, load_config, require_whirr_dir
from whirr.db import (
    claim_job,
    complete_job,
    get_connection,
    register_worker,
    requeue_orphaned_jobs,
    unregister_worker,
    update_job_heartbeat,
    update_job_process_info,
    update_worker_status,
)
from whirr.runner import JobRunner

console = Console()

# Shutdown event for graceful termination
_shutdown_event = Event()


def _signal_handler(signum, frame):
    """Handle SIGINT/SIGTERM for graceful shutdown."""
    console.print("\n[yellow]Shutdown requested, finishing current job...[/yellow]")
    _shutdown_event.set()


def worker(
    gpu: Optional[int] = typer.Option(
        None,
        "--gpu", "-g",
        help="GPU index to use (for multi-GPU setups)",
    ),
) -> None:
    """
    Start a worker to process jobs from the queue.

    The worker will:
    - Register itself in the database
    - Claim jobs atomically from the queue
    - Run them with proper process management
    - Send heartbeats during execution
    - Handle graceful shutdown on Ctrl+C
    """
    try:
        whirr_dir = require_whirr_dir()
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    config = load_config(whirr_dir)
    db_path = get_db_path(whirr_dir)
    runs_dir = get_runs_dir(whirr_dir)

    # Generate worker ID
    hostname = socket.gethostname()
    pid = os.getpid()
    if gpu is not None:
        worker_id = f"{hostname}:gpu{gpu}"
        os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu)
    else:
        worker_id = f"{hostname}:default"

    # Register worker
    conn = get_connection(db_path)
    try:
        register_worker(conn, worker_id, pid, hostname, gpu)

        # Check for and requeue orphaned jobs
        orphaned = requeue_orphaned_jobs(conn, config.heartbeat_timeout)
        if orphaned:
            console.print(f"[yellow]Requeued {len(orphaned)} orphaned job(s)[/yellow]")
            for job in orphaned:
                console.print(f"  - Job #{job['id']}: {job['name'] or 'unnamed'} (attempt {job['attempt'] + 1})")
    finally:
        conn.close()

    console.print(f"[green]Worker started:[/green] {worker_id}")
    console.print(f"[dim]Polling for jobs every {config.poll_interval}s...[/dim]")

    # Setup signal handlers
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        _worker_loop(worker_id, db_path, runs_dir, config)
    finally:
        # Unregister on exit
        conn = get_connection(db_path)
        try:
            unregister_worker(conn, worker_id)
        finally:
            conn.close()
        console.print("[dim]Worker stopped[/dim]")


def _worker_loop(worker_id: str, db_path: Path, runs_dir: Path, config) -> None:
    """Main worker loop."""
    while not _shutdown_event.is_set():
        conn = get_connection(db_path)
        try:
            job = claim_job(conn, worker_id)
            if job:
                update_worker_status(conn, worker_id, "busy", job["id"])
        finally:
            conn.close()

        if job is None:
            # No jobs available, wait and retry
            _shutdown_event.wait(timeout=config.poll_interval)
            continue

        # We have a job!
        console.print(f"\n[blue]Running job #{job['id']}:[/blue] {job['name'] or job['command_argv'][0]}")

        # Create run directory
        run_id = f"job-{job['id']}"
        run_dir = runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        # Create artifacts directory for future use
        artifacts_dir = run_dir / "artifacts"
        artifacts_dir.mkdir(exist_ok=True)

        # Setup environment for child process
        env = {
            "WHIRR_JOB_ID": str(job["id"]),
            "WHIRR_RUN_DIR": str(run_dir),
            "WHIRR_RUN_ID": run_id,
        }

        # Start the job
        runner = JobRunner(
            command_argv=job["command_argv"],
            workdir=Path(job["workdir"]),
            run_dir=run_dir,
            env=env,
        )
        runner.start()

        # Store process info for diagnostics
        if runner.pid and runner.pgid:
            conn = get_connection(db_path)
            try:
                update_job_process_info(conn, job["id"], runner.pid, runner.pgid)
            finally:
                conn.close()

        # Heartbeat thread
        heartbeat_stop = Event()
        cancel_requested = Event()

        def heartbeat_loop():
            while not heartbeat_stop.is_set():
                try:
                    conn = get_connection(db_path)
                    try:
                        cancel_time = update_job_heartbeat(conn, job["id"])
                        if cancel_time:
                            cancel_requested.set()
                    finally:
                        conn.close()
                except Exception:
                    pass
                heartbeat_stop.wait(timeout=config.heartbeat_interval)

        heartbeat_thread = Thread(target=heartbeat_loop, daemon=True)
        heartbeat_thread.start()

        # Wait for job to complete or cancellation
        exit_code = None
        error_message = None

        try:
            while runner.is_running:
                # Check for shutdown or cancellation
                if _shutdown_event.is_set() or cancel_requested.is_set():
                    reason = "shutdown" if _shutdown_event.is_set() else "cancelled"
                    console.print(f"[yellow]Killing job ({reason})...[/yellow]")
                    exit_code = runner.kill(grace_period=config.kill_grace_period)
                    error_message = f"Job {reason}"
                    break

                time.sleep(0.5)

            if exit_code is None:
                exit_code = runner.wait()

        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2.0)

        # Update job status
        conn = get_connection(db_path)
        try:
            complete_job(
                conn,
                job_id=job["id"],
                exit_code=exit_code,
                run_id=run_id,
                error_message=error_message,
            )
            update_worker_status(conn, worker_id, "idle", None)
        finally:
            conn.close()

        # Report result
        if exit_code == 0:
            console.print(f"[green]Job #{job['id']} completed[/green]")
        else:
            console.print(f"[red]Job #{job['id']} failed[/red] (exit code: {exit_code})")

        # If shutdown was requested, exit the loop
        if _shutdown_event.is_set():
            break
