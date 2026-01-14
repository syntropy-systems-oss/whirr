"""Ablation study session management."""

import json
import random
import string
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from whirr.ablate.models import AblationRunResult


@dataclass
class FileValue:
    """File reference with inlined content."""

    path: str  # Relative to project root
    text: str  # Full file contents


@dataclass
class AblationSession:
    """Ablation study session."""

    session_id: str
    name: str
    metric: str
    seed_base: int
    baseline: Dict[str, Any] = field(default_factory=dict)
    deltas: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    replicates: int = 20
    runs: List[AblationRunResult] = field(default_factory=list)
    created_at: str = ""

    # Runtime only, not serialized
    _path: Optional[Path] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    @classmethod
    def load(cls, path: Path) -> "AblationSession":
        """Load session from JSON file."""
        with open(path) as f:
            data = json.load(f)

        # Convert runs from dicts to AblationRunResult objects
        runs = [AblationRunResult(**r) for r in data.pop("runs", [])]

        # Convert file values back to FileValue objects
        deltas = {}
        for delta_name, delta_values in data.pop("deltas", {}).items():
            deltas[delta_name] = {}
            for k, v in delta_values.items():
                if isinstance(v, dict) and "path" in v and "text" in v:
                    deltas[delta_name][k] = FileValue(path=v["path"], text=v["text"])
                else:
                    deltas[delta_name][k] = v

        session = cls(**data, deltas=deltas, runs=runs)
        session._path = path
        return session

    def save(self) -> None:
        """Save session to JSON file."""
        if self._path is None:
            raise ValueError("Session path not set")

        # Convert FileValue objects to dicts for serialization
        deltas_serialized = {}
        for delta_name, delta_values in self.deltas.items():
            deltas_serialized[delta_name] = {}
            for k, v in delta_values.items():
                if isinstance(v, FileValue):
                    deltas_serialized[delta_name][k] = {"path": v.path, "text": v.text}
                else:
                    deltas_serialized[delta_name][k] = v

        data = {
            "session_id": self.session_id,
            "name": self.name,
            "metric": self.metric,
            "seed_base": self.seed_base,
            "baseline": self.baseline,
            "deltas": deltas_serialized,
            "replicates": self.replicates,
            "created_at": self.created_at,
            "runs": [
                {
                    "run_id": r.run_id,
                    "job_id": r.job_id,
                    "condition": r.condition,
                    "replicate": r.replicate,
                    "seed": r.seed,
                    "metric_value": r.metric_value,
                    "status": r.status,
                    "outcome": r.outcome,
                }
                for r in self.runs
            ],
        }

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(data, f, indent=2)

    def get_condition_names(self) -> List[str]:
        """Return all condition names: baseline + delta names."""
        return ["baseline"] + list(self.deltas.keys())

    def get_seed(self, replicate: int) -> int:
        """Get deterministic seed for a replicate."""
        return self.seed_base + replicate


def generate_session_id() -> str:
    """Generate a short random session ID."""
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choices(chars, k=6))


def get_ablations_dir(whirr_dir: Path) -> Path:
    """Get the ablations directory."""
    return whirr_dir / "ablations"


def get_session_path(session_id: str, whirr_dir: Path) -> Path:
    """Get path to session file by session_id."""
    return get_ablations_dir(whirr_dir) / f"{session_id}.json"


def get_index_path(whirr_dir: Path) -> Path:
    """Get path to index.json."""
    return get_ablations_dir(whirr_dir) / "index.json"


def load_index(whirr_dir: Path) -> Dict[str, str]:
    """Load name -> session_id index."""
    index_path = get_index_path(whirr_dir)
    if not index_path.exists():
        return {}
    with open(index_path) as f:
        return json.load(f)


def save_index(whirr_dir: Path, index: Dict[str, str]) -> None:
    """Save name -> session_id index."""
    index_path = get_index_path(whirr_dir)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)


def session_exists(name: str, whirr_dir: Path) -> bool:
    """Check if a session with the given name exists."""
    index = load_index(whirr_dir)
    return name in index


def load_session_by_name(name: str, whirr_dir: Path) -> AblationSession:
    """Load a session by name."""
    index = load_index(whirr_dir)
    if name not in index:
        raise FileNotFoundError(f"Ablation session '{name}' not found")
    session_id = index[name]
    path = get_session_path(session_id, whirr_dir)
    return AblationSession.load(path)


def create_session(
    name: str,
    metric: str,
    whirr_dir: Path,
) -> AblationSession:
    """Create a new ablation session."""
    if session_exists(name, whirr_dir):
        raise ValueError(f"Session '{name}' already exists")

    session_id = generate_session_id()
    seed_base = random.randint(0, 2**31 - 1)

    session = AblationSession(
        session_id=session_id,
        name=name,
        metric=metric,
        seed_base=seed_base,
    )

    # Set path and save
    session._path = get_session_path(session_id, whirr_dir)
    session.save()

    # Update index
    index = load_index(whirr_dir)
    index[name] = session_id
    save_index(whirr_dir, index)

    return session
