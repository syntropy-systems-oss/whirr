# Copyright (c) Syntropy Systems
"""Ablation study session management."""

import secrets
import string
from pathlib import Path

from whirr.models.ablation import AblationIndex, AblationSession


def generate_session_id() -> str:
    """Generate a short random session ID."""
    chars = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(6))


def get_ablations_dir(whirr_dir: Path) -> Path:
    """Get the ablations directory."""
    return whirr_dir / "ablations"


def get_session_path(session_id: str, whirr_dir: Path) -> Path:
    """Get path to session file by session_id."""
    return get_ablations_dir(whirr_dir) / f"{session_id}.json"


def get_index_path(whirr_dir: Path) -> Path:
    """Get path to index.json."""
    return get_ablations_dir(whirr_dir) / "index.json"


def load_index(whirr_dir: Path) -> AblationIndex:
    """Load name -> session_id index."""
    index_path = get_index_path(whirr_dir)
    if not index_path.exists():
        return AblationIndex()
    return AblationIndex.model_validate_json(index_path.read_text())


def save_index(whirr_dir: Path, index: AblationIndex) -> None:
    """Save name -> session_id index."""
    index_path = get_index_path(whirr_dir)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    _ = index_path.write_text(index.model_dump_json(indent=2))


def session_exists(name: str, whirr_dir: Path) -> bool:
    """Check if a session with the given name exists."""
    index = load_index(whirr_dir)
    return name in index.entries


def load_session_by_name(name: str, whirr_dir: Path) -> AblationSession:
    """Load a session by name."""
    index = load_index(whirr_dir)
    if name not in index.entries:
        msg = f"Ablation session '{name}' not found"
        raise FileNotFoundError(msg)
    session_id = index.entries[name]
    path = get_session_path(session_id, whirr_dir)
    return AblationSession.load(path)


def create_session(
    name: str,
    metric: str,
    whirr_dir: Path,
) -> AblationSession:
    """Create a new ablation session."""
    if session_exists(name, whirr_dir):
        msg = f"Session '{name}' already exists"
        raise ValueError(msg)

    session_id = generate_session_id()
    seed_base = secrets.randbelow(2**31)

    session = AblationSession(
        session_id=session_id,
        name=name,
        metric=metric,
        seed_base=seed_base,
    )

    # Set path and save
    session.set_path(get_session_path(session_id, whirr_dir))
    session.save()

    # Update index
    index = load_index(whirr_dir)
    index.entries[name] = session_id
    save_index(whirr_dir, index)

    return session
