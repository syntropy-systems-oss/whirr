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
    server: Optional[str] = typer.Option(
        None,
        "--server", "-s",
        envvar="WHIRR_SERVER_URL",
        help="Server URL for remote mode (e.g., http://head-node:8080)",
    ),
    data_dir: Optional[Path] = typer.Option(
        None,
        "--data-dir", "-d",
        envvar="WHIRR_DATA_DIR",
        help="Data directory for runs (required for remote mode)",
    ),
) -> None:
    """
    Start a worker to process jobs from the queue.

    LOCAL MODE (default):
        The worker connects directly to the local SQLite database.

    REMOTE MODE (--server):
        The worker connects to a remote whirr server via HTTP.
        Requires --data-dir pointing to a shared filesystem.

    Examples:

        # Local mode (in a whirr project)
        whirr worker

        # Local mode with GPU
        whirr worker --gpu 0

        # Remote mode
        whirr worker --server http://head-node:8080 --data-dir /mnt/shared/whirr
    """
    if server:
        _remote_worker(server, data_dir, gpu)
    else:
        _local_worker(gpu)


def _local_worker(gpu: Optional[int]) -> None:
    """Run worker in local mode (SQLite database)."""
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
        _local_worker_loop(worker_id, db_path, runs_dir, config)
    finally:
        # Unregister on exit
        conn = get_connection(db_path)
        try:
            unregister_worker(conn, worker_id)
        finally:
            conn.close()
        console.print("[dim]Worker stopped[/dim]")


def _local_worker_loop(worker_id: str, db_path: Path, runs_dir: Path, config) -> None:
    """Main worker loop for local mode."""
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


def _remote_worker(server_url: str, data_dir: Optional[Path], gpu: Optional[int]) -> None:
    """Run worker in remote mode (HTTP API)."""
    if data_dir is None:
        console.print("[red]Error:[/red] --data-dir is required for remote mode")
        raise typer.Exit(1)

    try:
        from whirr.client import WhirrClient, WhirrClientError
    except ImportError:
        console.print("[red]Error:[/red] httpx is required for remote mode.")
        console.print("Install with: pip install whirr[server]")
        raise typer.Exit(1)

    # Ensure data directory exists
    data_dir = Path(data_dir)
    runs_dir = data_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    # Generate worker ID
    hostname = socket.gethostname()
    if gpu is not None:
        worker_id = f"{hostname}:gpu{gpu}"
        os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu)
    else:
        worker_id = f"{hostname}:default"

    # Setup signal handlers
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    client = WhirrClient(server_url)

    try:
        # Register with server
        gpu_ids = [gpu] if gpu is not None else []
        client.register_worker(worker_id, hostname, gpu_ids)
        console.print(f"[green]Worker started:[/green] {worker_id}")
        console.print(f"[dim]Connected to server: {server_url}[/dim]")
        console.print(f"[dim]Data directory: {data_dir}[/dim]")

        _remote_worker_loop(client, worker_id, runs_dir, gpu)

    except WhirrClientError as e:
        console.print(f"[red]Server error:[/red] {e}")
        raise typer.Exit(1)
    finally:
        try:
            client.unregister_worker(worker_id)
        except Exception:
            pass
        client.close()
        console.print("[dim]Worker stopped[/dim]")


def _remote_worker_loop(
    client,
    worker_id: str,
    runs_dir: Path,
    gpu: Optional[int],
    poll_interval: float = 5.0,
    heartbeat_interval: float = 30.0,
    lease_seconds: int = 60,
) -> None:
    """Main worker loop for remote mode."""
    from whirr.client import WhirrClientError

    while not _shutdown_event.is_set():
        try:
            job = client.claim_job(
                worker_id=worker_id,
                gpu_id=gpu,
                lease_seconds=lease_seconds,
            )
        except WhirrClientError as e:
            console.print(f"[yellow]Warning:[/yellow] Failed to claim job: {e}")
            _shutdown_event.wait(timeout=poll_interval)
            continue

        if job is None:
            # No jobs available, wait and retry
            _shutdown_event.wait(timeout=poll_interval)
            continue

        # We have a job!
        job_id = job["id"]
        console.print(f"\n[blue]Running job #{job_id}:[/blue] {job.get('name') or job['command_argv'][0]}")

        # Create run directory
        run_id = f"job-{job_id}"
        run_dir = runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        # Create artifacts directory
        artifacts_dir = run_dir / "artifacts"
        artifacts_dir.mkdir(exist_ok=True)

        # Setup environment for child process
        env = {
            "WHIRR_JOB_ID": str(job_id),
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

        # Heartbeat thread
        heartbeat_stop = Event()
        cancel_requested = Event()

        def heartbeat_loop():
            while not heartbeat_stop.is_set():
                try:
                    result = client.renew_lease(
                        job_id=job_id,
                        worker_id=worker_id,
                        lease_seconds=lease_seconds,
                    )
                    if result.get("cancel_requested"):
                        cancel_requested.set()
                except Exception as e:
                    console.print(f"[yellow]Warning:[/yellow] Heartbeat failed: {e}")
                heartbeat_stop.wait(timeout=heartbeat_interval)

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
                    exit_code = runner.kill(grace_period=5.0)
                    error_message = f"Job {reason}"
                    break

                time.sleep(0.5)

            if exit_code is None:
                exit_code = runner.wait()

        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2.0)

        # Report completion to server
        try:
            client.complete_job(
                job_id=job_id,
                worker_id=worker_id,
                exit_code=exit_code,
                run_id=run_id,
                error_message=error_message,
            )
        except WhirrClientError as e:
            console.print(f"[yellow]Warning:[/yellow] Failed to report completion: {e}")

        # Report result
        if exit_code == 0:
            console.print(f"[green]Job #{job_id} completed[/green]")
        else:
            console.print(f"[red]Job #{job_id} failed[/red] (exit code: {exit_code})")

        # If shutdown was requested, exit the loop
        if _shutdown_event.is_set():
            break
