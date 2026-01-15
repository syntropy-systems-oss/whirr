# Copyright (c) Syntropy Systems
"""System metrics collection (GPU, CPU, memory)."""
from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event, Thread
from typing import TYPE_CHECKING, cast

from whirr.models.run import SystemMetricRecord

try:
    import psutil
except ImportError:
    psutil = None

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)
GPU_FIELD_COUNT = 4


@dataclass
class SystemMetrics:
    """System metrics snapshot."""

    timestamp: str
    cpu_percent: float | None = None
    memory_used_gb: float | None = None
    memory_total_gb: float | None = None
    gpu_utilization: float | None = None
    gpu_memory_used_gb: float | None = None
    gpu_memory_total_gb: float | None = None
    gpu_name: str | None = None

    def to_dict(self) -> dict[str, str | float]:
        """Convert to dictionary, excluding None values."""
        result: dict[str, str | float] = {"_timestamp": self.timestamp}
        if self.cpu_percent is not None:
            result["cpu_percent"] = self.cpu_percent
        if self.memory_used_gb is not None:
            result["memory_used_gb"] = round(self.memory_used_gb, 2)
        if self.memory_total_gb is not None:
            result["memory_total_gb"] = round(self.memory_total_gb, 2)
        if self.gpu_utilization is not None:
            result["gpu_util"] = self.gpu_utilization
        if self.gpu_memory_used_gb is not None:
            result["gpu_mem_used_gb"] = round(self.gpu_memory_used_gb, 2)
        if self.gpu_memory_total_gb is not None:
            result["gpu_mem_total_gb"] = round(self.gpu_memory_total_gb, 2)
        if self.gpu_name is not None:
            result["gpu_name"] = self.gpu_name
        return result


def get_cpu_metrics() -> tuple[float | None, float | None, float | None]:
    """Get CPU and memory metrics.

    Returns (cpu_percent, memory_used_gb, memory_total_gb).
    """
    if psutil is None:
        return None, None, None
    try:
        cpu_percent = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        used = cast("int", mem.used)
        total = cast("int", mem.total)
        memory_used_gb = used / (1024**3)
        memory_total_gb = total / (1024**3)
    except (AttributeError, OSError, ValueError):
        return None, None, None
    else:
        return cpu_percent, memory_used_gb, memory_total_gb


def get_gpu_metrics() -> tuple[
    float | None,
    float | None,
    float | None,
    str | None,
]:
    """Get GPU metrics using nvidia-smi.

    Returns (utilization, memory_used_gb, memory_total_gb, gpu_name).
    """
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        return None, None, None, None

    try:
        result = subprocess.run(  # noqa: S603
            [
                nvidia_smi,
                "--query-gpu=utilization.gpu,memory.used,memory.total,name",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, None, None, None

    if result.returncode != 0:
        return None, None, None, None

    # Parse first GPU
    line = result.stdout.strip().split("\n")[0]
    parts = [p.strip() for p in line.split(",")]

    if len(parts) >= GPU_FIELD_COUNT:
        try:
            util = float(parts[0])
            mem_used = float(parts[1]) / 1024  # MB to GB
            mem_total = float(parts[2]) / 1024  # MB to GB
            name = parts[3]
        except ValueError:
            return None, None, None, None
        else:
            return util, mem_used, mem_total, name

    return None, None, None, None


def collect_metrics() -> SystemMetrics:
    """Collect current system metrics."""
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    cpu_percent, mem_used, mem_total = get_cpu_metrics()
    gpu_util, gpu_mem_used, gpu_mem_total, gpu_name = get_gpu_metrics()

    return SystemMetrics(
        timestamp=timestamp,
        cpu_percent=cpu_percent,
        memory_used_gb=mem_used,
        memory_total_gb=mem_total,
        gpu_utilization=gpu_util,
        gpu_memory_used_gb=gpu_mem_used,
        gpu_memory_total_gb=gpu_mem_total,
        gpu_name=gpu_name,
    )


class SystemMetricsCollector:
    """Background collector that periodically samples system metrics.

    Writes to system.jsonl in the run directory.
    """

    _run_dir: Path
    _interval: float
    _stop_event: Event
    _thread: Thread | None
    _metrics_path: Path

    def __init__(self, run_dir: Path, interval: float = 10.0) -> None:
        """Initialize collector.

        Args:
            run_dir: Directory to write system.jsonl
            interval: Collection interval in seconds

        """
        self._run_dir = run_dir
        self._interval = interval
        self._stop_event = Event()
        self._thread = None
        self._metrics_path = run_dir / "system.jsonl"

    def start(self) -> None:
        """Start background collection."""
        if self._thread is not None:
            return

        self._stop_event.clear()
        self._thread = Thread(target=self._collection_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop background collection."""
        if self._thread is None:
            return

        self._stop_event.set()
        self._thread.join(timeout=2.0)
        self._thread = None

    def _collection_loop(self) -> None:
        """Background collection loop."""
        while not self._stop_event.is_set():
            try:
                metrics = collect_metrics()
                self._write_metrics(metrics)
            except Exception as exc:
                logger.exception("System metrics collection failed", exc_info=exc)

            _ = self._stop_event.wait(timeout=self._interval)

    def _write_metrics(self, metrics: SystemMetrics) -> None:
        """Append metrics to system.jsonl."""
        with self._metrics_path.open("a") as f:
            record = SystemMetricRecord.model_validate(metrics.to_dict())
            _ = f.write(record.model_dump_json(by_alias=True) + "\n")
            _ = f.flush()
