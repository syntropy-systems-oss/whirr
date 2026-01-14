"""Configuration management for whirr."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class WhirrConfig:
    """Configuration for whirr."""

    # Heartbeat interval in seconds
    heartbeat_interval: int = 30

    # Timeout for considering a worker dead (seconds)
    heartbeat_timeout: int = 120

    # Grace period before SIGKILL after SIGTERM (seconds)
    kill_grace_period: int = 10

    # Poll interval for worker when no jobs available (seconds)
    poll_interval: int = 5


def find_whirr_dir(start_path: Optional[Path] = None) -> Optional[Path]:
    """
    Find the nearest .whirr directory by walking up from start_path.

    Returns None if no .whirr directory is found.
    """
    if start_path is None:
        start_path = Path.cwd()

    current = start_path.resolve()

    while current != current.parent:
        whirr_dir = current / ".whirr"
        if whirr_dir.is_dir():
            return whirr_dir
        current = current.parent

    # Check root
    whirr_dir = current / ".whirr"
    if whirr_dir.is_dir():
        return whirr_dir

    return None


def get_global_config_dir() -> Path:
    """Get the global whirr config directory (~/.whirr)."""
    return Path.home() / ".whirr"


def load_config(whirr_dir: Optional[Path] = None) -> WhirrConfig:
    """
    Load configuration from .whirr/config.yaml or defaults.

    Looks for config in:
    1. Provided whirr_dir
    2. Nearest .whirr directory walking up
    3. ~/.whirr/config.yaml
    4. Defaults
    """
    config = WhirrConfig()

    # Find config file
    config_path = None

    if whirr_dir is not None:
        config_path = whirr_dir / "config.yaml"
    else:
        found_dir = find_whirr_dir()
        if found_dir is not None:
            config_path = found_dir / "config.yaml"
        else:
            global_config = get_global_config_dir() / "config.yaml"
            if global_config.exists():
                config_path = global_config

    if config_path is not None and config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        if "heartbeat_interval" in data:
            config.heartbeat_interval = data["heartbeat_interval"]
        if "heartbeat_timeout" in data:
            config.heartbeat_timeout = data["heartbeat_timeout"]
        if "kill_grace_period" in data:
            config.kill_grace_period = data["kill_grace_period"]
        if "poll_interval" in data:
            config.poll_interval = data["poll_interval"]

    return config


def get_db_path(whirr_dir: Optional[Path] = None) -> Path:
    """Get the path to the SQLite database."""
    if whirr_dir is None:
        whirr_dir = find_whirr_dir()

    if whirr_dir is None:
        raise RuntimeError(
            "No .whirr directory found. Run 'whirr init' first."
        )

    return whirr_dir / "whirr.db"


def get_runs_dir(whirr_dir: Optional[Path] = None) -> Path:
    """Get the path to the runs directory."""
    if whirr_dir is None:
        whirr_dir = find_whirr_dir()

    if whirr_dir is None:
        raise RuntimeError(
            "No .whirr directory found. Run 'whirr init' first."
        )

    return whirr_dir / "runs"


def require_whirr_dir() -> Path:
    """Get whirr directory or raise an error if not found."""
    whirr_dir = find_whirr_dir()
    if whirr_dir is None:
        raise RuntimeError(
            "No .whirr directory found. Run 'whirr init' first."
        )
    return whirr_dir
