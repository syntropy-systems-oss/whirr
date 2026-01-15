# Copyright (c) Syntropy Systems
"""Run tracking for whirr experiments."""
from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import sys
import uuid
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from pydantic import ValidationError
from typing_extensions import Self

from whirr.config import find_whirr_dir, get_db_path, get_runs_dir
from whirr.db import complete_run, create_run, get_connection
from whirr.models.run import GitInfo, RunConfig, RunMeta, RunMetricRecord, RunSummary

if TYPE_CHECKING:
    from types import TracebackType

    from whirr.models.base import JSONValue


class _MetricsCollector(Protocol):
    def start(self) -> None:
        ...

    def stop(self) -> None:
        ...


def utcnow() -> str:
    """Get current UTC time as ISO format string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_command(
    argv: list[str],
    *,
    timeout: float,
) -> subprocess.CompletedProcess[str] | None:
    cmd_path = shutil.which(argv[0])
    if cmd_path is None:
        return None
    try:
        return subprocess.run(  # noqa: S603
            [cmd_path, *argv[1:]],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _capture_git_info() -> GitInfo | None:
    """Capture git repository information."""
    # Check if we're in a git repo
    result = _run_command(
        ["git", "rev-parse", "--is-inside-work-tree"],
        timeout=5,
    )
    if result is None or result.returncode != 0:
        return None

    # Get commit hash
    commit_result = _run_command(["git", "rev-parse", "HEAD"], timeout=5)
    if commit_result is None:
        return None
    commit = commit_result.stdout.strip()

    # Get short hash
    short_hash_result = _run_command(["git", "rev-parse", "--short", "HEAD"], timeout=5)
    if short_hash_result is None:
        return None
    short_hash = short_hash_result.stdout.strip()

    # Check if dirty (uncommitted changes)
    dirty_result = _run_command(["git", "status", "--porcelain"], timeout=5)
    if dirty_result is None:
        return None
    is_dirty = bool(dirty_result.stdout.strip())

    # Get branch name
    branch_result = _run_command(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        timeout=5,
    )
    if branch_result is None:
        return None
    branch = branch_result.stdout.strip()

    # Get remote URL (if any)
    remote_result = _run_command(
        ["git", "remote", "get-url", "origin"],
        timeout=5,
    )
    remote = (
        remote_result.stdout.strip()
        if remote_result is not None and remote_result.returncode == 0
        else None
    )

    return GitInfo(
        commit=commit,
        short_hash=short_hash,
        branch=branch,
        dirty=is_dirty,
        remote=remote,
    )


def _capture_pip_freeze() -> list[str] | None:
    """Capture installed packages via pip freeze."""
    result = _run_command(["pip", "freeze"], timeout=30)
    if result is not None and result.returncode == 0:
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    # Try python -m pip as fallback
    result = _run_command([sys.executable, "-m", "pip", "freeze"], timeout=30)
    if result is not None and result.returncode == 0:
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    return None


class Run:
    """A whirr experiment run.

    Tracks metrics, configuration, and artifacts for an experiment.
    Works both in worker context (via WHIRR_JOB_ID) and direct execution.
    """

    _finished: bool
    _metric_idx: int
    _summary_data: RunSummary | None
    _system_metrics_collector: _MetricsCollector | None
    _finished_at: str | None
    _status: str

    job_id: int | None
    run_dir: Path
    run_id: str
    name: str
    config: dict[str, JSONValue]
    tags: list[str]
    started_at: str
    _artifacts_dir: Path
    _metrics_path: Path
    _meta_path: Path
    _config_path: Path
    git_info: GitInfo | None
    pip_packages: list[str] | None

    def __init__(  # noqa: PLR0913, PLR0915
        self,
        name: str | None = None,
        config: dict[str, JSONValue] | None = None,
        tags: list[str] | None = None,
        run_id: str | None = None,
        run_dir: Path | None = None,
        job_id: int | None = None,
        system_metrics: bool = True,  # noqa: FBT001, FBT002
        system_metrics_interval: float = 10.0,
        capture_git: bool = True,  # noqa: FBT001, FBT002
        capture_pip: bool = True,  # noqa: FBT001, FBT002
    ) -> None:
        """Initialize a run.

        In most cases, use whirr.init() instead of constructing directly.
        """
        self._finished = False
        self._metric_idx = 0
        self._summary_data = None
        self._system_metrics_collector = None
        self._finished_at = None
        self._status = "running"

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
        run_config = RunConfig.model_validate(self.config)
        _ = self._config_path.write_text(run_config.model_dump_json(indent=2))

        # Capture git info
        self.git_info = None
        if capture_git:
            self.git_info = _capture_git_info()
            if self.git_info:
                git_path = self.run_dir / "git.json"
                _ = git_path.write_text(self.git_info.model_dump_json(indent=2))

        # Capture pip freeze
        self.pip_packages = None
        if capture_pip:
            self.pip_packages = _capture_pip_freeze()
            if self.pip_packages:
                requirements_path = self.run_dir / "requirements.txt"
                with requirements_path.open("w") as f:
                    _ = f.write("\n".join(self.pip_packages) + "\n")

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
                from whirr.system_metrics import (  # noqa: PLC0415
                    SystemMetricsCollector,
                )
                self._system_metrics_collector = SystemMetricsCollector(
                    self.run_dir,
                    interval=system_metrics_interval,
                )
                self._system_metrics_collector.start()
            except (ImportError, OSError, RuntimeError):
                self._system_metrics_collector = None

    def _write_meta(self) -> None:
        """Write/update metadata file."""
        meta = RunMeta(
            id=self.run_id,
            name=self.name,
            tags=self.tags,
            started_at=self.started_at,
            finished_at=self._finished_at if self._finished else None,
            status=self._status if self._finished else "running",
            summary=self._summary_data,
            config_file="config.json",
            metrics_file="metrics.jsonl",
            artifacts_dir="artifacts",
            git=self.git_info,
            git_file="git.json" if self.git_info else None,
            requirements_file="requirements.txt" if self.pip_packages else None,
            pip_packages_count=len(self.pip_packages) if self.pip_packages else None,
        )

        _ = self._meta_path.write_text(meta.model_dump_json(indent=2))

    def log(
        self,
        metrics: dict[str, JSONValue],
        step: int | None = None,
    ) -> None:
        """Log metrics for this run.

        Args:
            metrics: Dictionary of metric names to values
            step: Optional step number (should be monotonic if provided)

        """
        if self._finished:
            msg = "Cannot log to a finished run"
            raise RuntimeError(msg)

        entry: dict[str, JSONValue] = {
            "_idx": self._metric_idx,
            "_timestamp": utcnow(),
        }

        if step is not None:
            entry["step"] = step

        entry.update(metrics)

        record = RunMetricRecord.model_validate(entry)
        with self._metrics_path.open("a") as f:
            _ = f.write(record.model_dump_json(by_alias=True, exclude_none=True) + "\n")
            _ = f.flush()

        self._metric_idx += 1

    def summary(self, metrics: dict[str, JSONValue]) -> None:
        """Set summary metrics for this run.

        These are the final metrics that will be displayed in run listings.
        """
        if self._finished:
            msg = "Cannot set summary on a finished run"
            raise RuntimeError(msg)

        self._summary_data = RunSummary.model_validate(metrics)
        self._write_meta()

    def save_artifact(
        self,
        source_path: str,
        dest_name: str | None = None,
    ) -> Path:
        """Save an artifact file to the run's artifacts directory.

        Args:
            source_path: Path to the file to save
            dest_name: Optional destination filename (defaults to source basename)

        Returns:
            Path to the saved artifact

        """
        if self._finished:
            msg = "Cannot save artifacts to a finished run"
            raise RuntimeError(msg)

        source = Path(source_path)
        if not source.exists():
            msg = f"Artifact source not found: {source_path}"
            raise FileNotFoundError(msg)

        dest_name = dest_name or source.name
        dest = self._artifacts_dir / dest_name

        # Copy the file
        _ = shutil.copy2(source, dest)

        return dest

    def finish(self, status: str = "completed") -> None:
        """Mark this run as finished.

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
                    summary=self._summary_data.values if self._summary_data else None,
                )
            finally:
                conn.close()

    @property
    def finished(self) -> bool:
        """Return whether the run has finished."""
        return self._finished

    def __enter__(self) -> Self:
        """Context manager entry."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Context manager exit - auto-finish with appropriate status."""
        if not self._finished:
            status = "failed" if exc_type is not None else "completed"
            self.finish(status=status)


# Module-level active run tracking
_active_run_state: dict[str, Run | None] = {"run": None}


def init(  # noqa: PLR0913
    name: str | None = None,
    config: dict[str, JSONValue] | None = None,
    tags: list[str] | None = None,
    system_metrics: bool = True,  # noqa: FBT001, FBT002
    capture_git: bool = True,  # noqa: FBT001, FBT002
    capture_pip: bool = True,  # noqa: FBT001, FBT002
) -> Run:
    """Initialize a new whirr run.

    This is the main entry point for tracking experiments.
    Works both when run directly and when executed by a whirr worker.

    Args:
        name: Human-readable name for the run
        config: Configuration dictionary (hyperparameters, etc.)
        tags: List of tags for filtering
        system_metrics: Enable automatic system metrics collection (GPU, CPU)
        capture_git: Capture git commit hash and dirty state
        capture_pip: Capture pip freeze snapshot to requirements.txt

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
    run = Run(
        name=name,
        config=config,
        tags=tags,
        system_metrics=system_metrics,
        capture_git=capture_git,
        capture_pip=capture_pip,
    )
    _active_run_state["run"] = run

    # Register atexit handler for auto-finish
    _ = atexit.register(_atexit_finish)

    return run


def _atexit_finish() -> None:
    """Auto-finish the active run on exit."""
    active_run = _active_run_state["run"]
    if active_run is not None and not active_run.finished:
        active_run.finish()


def get_active_run() -> Run | None:
    """Get the currently active run, if any."""
    return _active_run_state["run"]


def read_metrics(metrics_path: Path) -> list[RunMetricRecord]:
    """Read metrics from a JSONL file, tolerating partial final lines.

    Args:
        metrics_path: Path to metrics.jsonl file

    Returns:
        List of parsed metric records

    """
    metrics: list[RunMetricRecord] = []

    if not metrics_path.exists():
        return metrics

    with metrics_path.open() as f:
        for raw_line in f:
            line = raw_line.strip()
            if line:
                with suppress(ValidationError):
                    metrics.append(RunMetricRecord.model_validate_json(line))

    return metrics


def read_meta(run_dir: Path) -> RunMeta | None:
    """Read run metadata from meta.json."""
    meta_path = run_dir / "meta.json"
    if not meta_path.exists():
        return None

    return RunMeta.model_validate_json(meta_path.read_text())
