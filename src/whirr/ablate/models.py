"""Data models for ablation studies."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class AblationRunResult:
    """Result from a single replicate run."""

    run_id: str
    job_id: int
    condition: str  # "baseline" or delta name
    replicate: int
    seed: int
    metric_value: Optional[float] = None
    status: str = "queued"  # queued, running, completed, failed
    outcome: Optional[str] = None  # None or "no_metric"
