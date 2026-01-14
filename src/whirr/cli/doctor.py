"""whirr doctor command."""

import os
import shutil
import subprocess

from rich.console import Console

from whirr.config import find_whirr_dir, get_db_path
from whirr.db import get_active_jobs, get_connection

console = Console()


def doctor() -> None:
    """
    Check whirr setup and diagnose issues.

    Verifies:
    - whirr directory exists
    - SQLite database is healthy
    - GPU availability
    - Worker status
    """
    issues = []
    warnings = []

    # Check whirr directory
    whirr_dir = find_whirr_dir()
    if whirr_dir is None:
        console.print("[red]\u2717[/red] No .whirr directory found")
        console.print("  Run [bold]whirr init[/bold] to initialize a project")
        return

    console.print(f"[green]\u2713[/green] whirr directory: {whirr_dir}")

    # Check database
    db_path = get_db_path(whirr_dir)
    if not db_path.exists():
        console.print(f"[red]\u2717[/red] Database not found: {db_path}")
        issues.append("Database missing")
    else:
        try:
            conn = get_connection(db_path)

            # Check WAL mode
            result = conn.execute("PRAGMA journal_mode").fetchone()
            if result and result[0].lower() == "wal":
                console.print("[green]\u2713[/green] SQLite: WAL mode enabled")
            else:
                console.print(f"[yellow]\u26a0[/yellow] SQLite: journal_mode is {result[0]}, expected WAL")
                warnings.append("Not using WAL mode")

            # Check for active jobs
            active = get_active_jobs(conn)
            running = [j for j in active if j["status"] == "running"]
            queued = [j for j in active if j["status"] == "queued"]

            conn.close()

            console.print(f"[green]\u2713[/green] Database: {len(queued)} queued, {len(running)} running")

        except Exception as e:
            console.print(f"[red]\u2717[/red] Database error: {e}")
            issues.append(f"Database error: {e}")

    # Check runs directory
    runs_dir = whirr_dir / "runs"
    if runs_dir.exists():
        run_count = len(list(runs_dir.iterdir()))
        console.print(f"[green]\u2713[/green] Runs directory: {run_count} runs")
    else:
        console.print("[yellow]\u26a0[/yellow] Runs directory not found")
        warnings.append("Runs directory missing")

    # Check for nvidia-smi (GPU)
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                gpus = result.stdout.strip().split("\n")
                for i, gpu in enumerate(gpus):
                    console.print(f"[green]\u2713[/green] GPU {i}: {gpu.strip()}")
            else:
                console.print("[yellow]\u26a0[/yellow] nvidia-smi failed")
                warnings.append("nvidia-smi failed")
        except subprocess.TimeoutExpired:
            console.print("[yellow]\u26a0[/yellow] nvidia-smi timed out")
            warnings.append("nvidia-smi timed out")
        except Exception as e:
            console.print(f"[yellow]\u26a0[/yellow] GPU check failed: {e}")
            warnings.append(f"GPU check failed: {e}")
    else:
        console.print("[dim]\u2022[/dim] No GPU detected (nvidia-smi not found)")

    # Check CUDA_VISIBLE_DEVICES
    cuda_devices = os.environ.get("CUDA_VISIBLE_DEVICES")
    if cuda_devices:
        console.print(f"[dim]\u2022[/dim] CUDA_VISIBLE_DEVICES={cuda_devices}")

    # Summary
    console.print()
    if issues:
        console.print(f"[red]Found {len(issues)} issue(s)[/red]")
        for issue in issues:
            console.print(f"  - {issue}")
    elif warnings:
        console.print(f"[yellow]Found {len(warnings)} warning(s)[/yellow]")
        for warning in warnings:
            console.print(f"  - {warning}")
    else:
        console.print("[green]All checks passed[/green]")
