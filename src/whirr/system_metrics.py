"""System metrics collection (GPU, CPU, memory)."""

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread
from typing import Optional, Union


@dataclass
class SystemMetrics:
    """System metrics snapshot."""

    timestamp: str
    cpu_percent: Optional[float] = None
    memory_used_gb: Optional[float] = None
    memory_total_gb: Optional[float] = None
    gpu_utilization: Optional[float] = None
    gpu_memory_used_gb: Optional[float] = None
    gpu_memory_total_gb: Optional[float] = None
    gpu_name: Optional[str] = None

    def to_dict(self) -> dict[str, Union[str, float]]:
        """Convert to dictionary, excluding None values."""
        result: dict[str, Union[str, float]] = {"_timestamp": self.timestamp}
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


def get_cpu_metrics() -> tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Get CPU and memory metrics.

    Returns (cpu_percent, memory_used_gb, memory_total_gb).
    """
    try:
        import psutil
        cpu_percent = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        memory_used_gb = mem.used / (1024 ** 3)
        memory_total_gb = mem.total / (1024 ** 3)
        return cpu_percent, memory_used_gb, memory_total_gb
    except ImportError:
        return None, None, None
    except Exception:
        return None, None, None


def get_gpu_metrics() -> tuple[
    Optional[float],
    Optional[float],
    Optional[float],
    Optional[str],
]:
    """
    Get GPU metrics using nvidia-smi.

    Returns (utilization, memory_used_gb, memory_total_gb, gpu_name).
    """
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total,name",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode != 0:
            return None, None, None, None

        # Parse first GPU
        line = result.stdout.strip().split("\n")[0]
        parts = [p.strip() for p in line.split(",")]

        if len(parts) >= 4:
            util = float(parts[0])
            mem_used = float(parts[1]) / 1024  # MB to GB
            mem_total = float(parts[2]) / 1024  # MB to GB
            name = parts[3]
            return util, mem_used, mem_total, name

    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass

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
    """
    Background collector that periodically samples system metrics.

    Writes to system.jsonl in the run directory.
    """

    def __init__(self, run_dir: Path, interval: float = 10.0):
        """
        Initialize collector.

        Args:
            run_dir: Directory to write system.jsonl
            interval: Collection interval in seconds
        """
        self._run_dir = run_dir
        self._interval = interval
        self._stop_event = Event()
        self._thread: Optional[Thread] = None
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
            except Exception:
                pass  # Don't crash on collection errors

            self._stop_event.wait(timeout=self._interval)

    def _write_metrics(self, metrics: SystemMetrics) -> None:
        """Append metrics to system.jsonl."""
        with open(self._metrics_path, "a") as f:
            f.write(json.dumps(metrics.to_dict()) + "\n")
            f.flush()
