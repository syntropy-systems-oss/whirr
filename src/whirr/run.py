"""Run tracking for whirr experiments."""

import atexit
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from whirr.config import find_whirr_dir, get_db_path, get_runs_dir
from whirr.db import complete_run, create_run, get_connection


def utcnow() -> str:
    """Get current UTC time as ISO format string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Run:
    """
    A whirr experiment run.

    Tracks metrics, configuration, and artifacts for an experiment.
    Works both in worker context (via WHIRR_JOB_ID) and direct execution.
    """

    def __init__(
        self,
        name: Optional[str] = None,
        config: Optional[dict[str, Any]] = None,
        tags: Optional[list[str]] = None,
        run_id: Optional[str] = None,
        run_dir: Optional[Path] = None,
        job_id: Optional[int] = None,
        system_metrics: bool = True,
        system_metrics_interval: float = 10.0,
    ):
        """
        Initialize a run.

        In most cases, use whirr.init() instead of constructing directly.
        """
        self._finished = False
        self._metric_idx = 0
        self._summary_data: Optional[dict] = None
        self._system_metrics_collector = None

        # Detect worker context
        env_job_id = os.environ.get("WHIRR_JOB_ID")
        env_run_dir = os.environ.get("WHIRR_RUN_DIR")

        if env_job_id and env_run_dir:
            # Running under worker
            self.job_id = int(env_job_id)
            self.run_dir = Path(env_run_dir)
            self.run_id = self.run_dir.name
        else:
            # Direct run - use local-prefixed ID to avoid collision with job IDs
            self.job_id = job_id
            now = datetime.now(timezone.utc)
            rand = str(uuid.uuid4())[:6]
            self.run_id = run_id or f"local-{now.strftime('%Y%m%d-%H%M%S')}-{rand}"

            if run_dir:
                self.run_dir = run_dir
            else:
                runs_dir = get_runs_dir()
                self.run_dir = runs_dir / self.run_id

        # Store metadata
        self.name = name or self.run_id
        self.config = config or {}
        self.tags = tags or []
        self.started_at = utcnow()

        # Setup run directory structure
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._artifacts_dir = self.run_dir / "artifacts"
        self._artifacts_dir.mkdir(exist_ok=True)

        self._metrics_path = self.run_dir / "metrics.jsonl"
        self._meta_path = self.run_dir / "meta.json"
        self._config_path = self.run_dir / "config.json"

        # Write config to separate file
        with open(self._config_path, "w") as f:
            json.dump(self.config, f, indent=2)

        # Write initial metadata
        self._write_meta()

        # Register with database if we have a whirr context
        whirr_dir = find_whirr_dir()
        if whirr_dir:
            db_path = get_db_path(whirr_dir)
            conn = get_connection(db_path)
            try:
                create_run(
                    conn,
                    run_id=self.run_id,
                    run_dir=str(self.run_dir),
                    name=self.name,
                    config=self.config,
                    tags=self.tags,
                    job_id=self.job_id,
                )
            finally:
                conn.close()

        # Start system metrics collection if enabled
        if system_metrics:
            try:
                from whirr.system_metrics import SystemMetricsCollector
                self._system_metrics_collector = SystemMetricsCollector(
                    self.run_dir,
                    interval=system_metrics_interval,
                )
                self._system_metrics_collector.start()
            except Exception:
                pass  # Don't fail if system metrics unavailable

    def _write_meta(self) -> None:
        """Write/update metadata file."""
        meta = {
            "id": self.run_id,
            "name": self.name,
            "tags": self.tags,
            "started_at": self.started_at,
            "finished_at": None,
            "status": "running",
            "summary": self._summary_data,
            "config_file": "config.json",
            "metrics_file": "metrics.jsonl",
            "artifacts_dir": "artifacts",
        }

        if self._finished:
            meta["finished_at"] = self._finished_at
            meta["status"] = self._status

        with open(self._meta_path, "w") as f:
            json.dump(meta, f, indent=2)

    def log(
        self,
        metrics: dict[str, Any],
        step: Optional[int] = None,
    ) -> None:
        """
        Log metrics for this run.

        Args:
            metrics: Dictionary of metric names to values
            step: Optional step number (should be monotonic if provided)
        """
        if self._finished:
            raise RuntimeError("Cannot log to a finished run")

        entry = {
            "_idx": self._metric_idx,
            "_timestamp": utcnow(),
        }

        if step is not None:
            entry["step"] = step

        entry.update(metrics)

        with open(self._metrics_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
            f.flush()

        self._metric_idx += 1

    def summary(self, metrics: dict[str, Any]) -> None:
        """
        Set summary metrics for this run.

        These are the final metrics that will be displayed in run listings.
        """
        if self._finished:
            raise RuntimeError("Cannot set summary on a finished run")

        self._summary_data = metrics
        self._write_meta()

    def save_artifact(
        self,
        source_path: str,
        dest_name: Optional[str] = None,
    ) -> Path:
        """
        Save an artifact file to the run's artifacts directory.

        Args:
            source_path: Path to the file to save
            dest_name: Optional destination filename (defaults to source basename)

        Returns:
            Path to the saved artifact
        """
        if self._finished:
            raise RuntimeError("Cannot save artifacts to a finished run")

        source = Path(source_path)
        if not source.exists():
            raise FileNotFoundError(f"Artifact source not found: {source_path}")

        dest_name = dest_name or source.name
        dest = self._artifacts_dir / dest_name

        # Copy the file
        shutil.copy2(source, dest)

        return dest

    def finish(self, status: str = "completed") -> None:
        """
        Mark this run as finished.

        Args:
            status: Final status ("completed" or "failed")
        """
        if self._finished:
            return

        # Stop system metrics collection
        if self._system_metrics_collector:
            self._system_metrics_collector.stop()

        self._finished = True
        self._finished_at = utcnow()
        self._status = status
        self._write_meta()

        # Update database
        whirr_dir = find_whirr_dir()
        if whirr_dir:
            db_path = get_db_path(whirr_dir)
            conn = get_connection(db_path)
            try:
                complete_run(
                    conn,
                    run_id=self.run_id,
                    status=status,
                    summary=self._summary_data,
                )
            finally:
                conn.close()

    def __enter__(self) -> "Run":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - auto-finish with appropriate status."""
        if not self._finished:
            status = "failed" if exc_type is not None else "completed"
            self.finish(status=status)


# Module-level active run tracking
_active_run: Optional[Run] = None


def init(
    name: Optional[str] = None,
    config: Optional[dict[str, Any]] = None,
    tags: Optional[list[str]] = None,
    system_metrics: bool = True,
) -> Run:
    """
    Initialize a new whirr run.

    This is the main entry point for tracking experiments.
    Works both when run directly and when executed by a whirr worker.

    Args:
        name: Human-readable name for the run
        config: Configuration dictionary (hyperparameters, etc.)
        tags: List of tags for filtering
        system_metrics: Enable automatic system metrics collection (GPU, CPU)

    Returns:
        A Run instance for logging metrics

    Example:
        >>> run = whirr.init(name="baseline", config={"lr": 0.01})
        >>> run.log({"loss": 0.5}, step=0)
        >>> run.summary({"final_loss": 0.1})
        >>> run.finish()

    Or with context manager:
        >>> with whirr.init(name="baseline") as run:
        ...     run.log({"loss": 0.5})
    """
    global _active_run

    run = Run(name=name, config=config, tags=tags, system_metrics=system_metrics)
    _active_run = run

    # Register atexit handler for auto-finish
    atexit.register(_atexit_finish)

    return run


def _atexit_finish() -> None:
    """Auto-finish the active run on exit."""
    global _active_run
    if _active_run is not None and not _active_run._finished:
        _active_run.finish()


def get_active_run() -> Optional[Run]:
    """Get the currently active run, if any."""
    return _active_run


def read_metrics(metrics_path: Path) -> list[dict]:
    """
    Read metrics from a JSONL file, tolerating partial final lines.

    Args:
        metrics_path: Path to metrics.jsonl file

    Returns:
        List of metric dictionaries
    """
    metrics = []

    if not metrics_path.exists():
        return metrics

    with open(metrics_path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    metrics.append(json.loads(line))
                except json.JSONDecodeError:
                    # Ignore truncated final line (crash mid-write)
                    pass

    return metrics


def read_meta(run_dir: Path) -> Optional[dict]:
    """Read run metadata from meta.json."""
    meta_path = run_dir / "meta.json"
    if not meta_path.exists():
        return None

    with open(meta_path) as f:
        return json.load(f)
