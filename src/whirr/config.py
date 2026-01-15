# Copyright (c) Syntropy Systems
"""Configuration management for whirr."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

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


def find_whirr_dir(start_path: Path | None = None) -> Path | None:
    """Find the nearest .whirr directory by walking up from start_path.

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


def load_config(whirr_dir: Path | None = None) -> WhirrConfig:
    """Load configuration from .whirr/config.yaml or defaults.

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
        with config_path.open() as f:
            data = cast("dict[str, object]", yaml.safe_load(f) or {})

        heartbeat_interval = data.get("heartbeat_interval")
        if isinstance(heartbeat_interval, (int, float)):
            config.heartbeat_interval = int(heartbeat_interval)
        heartbeat_timeout = data.get("heartbeat_timeout")
        if isinstance(heartbeat_timeout, (int, float)):
            config.heartbeat_timeout = int(heartbeat_timeout)
        kill_grace_period = data.get("kill_grace_period")
        if isinstance(kill_grace_period, (int, float)):
            config.kill_grace_period = int(kill_grace_period)
        poll_interval = data.get("poll_interval")
        if isinstance(poll_interval, (int, float)):
            config.poll_interval = int(poll_interval)

    return config


def get_db_path(whirr_dir: Path | None = None) -> Path:
    """Get the path to the SQLite database."""
    if whirr_dir is None:
        whirr_dir = find_whirr_dir()

    if whirr_dir is None:
        msg = "No .whirr directory found. Run 'whirr init' first."
        raise RuntimeError(
            msg
        )

    return whirr_dir / "whirr.db"


def get_runs_dir(whirr_dir: Path | None = None) -> Path:
    """Get the path to the runs directory."""
    if whirr_dir is None:
        whirr_dir = find_whirr_dir()

    if whirr_dir is None:
        msg = "No .whirr directory found. Run 'whirr init' first."
        raise RuntimeError(
            msg
        )

    return whirr_dir / "runs"


def require_whirr_dir() -> Path:
    """Get whirr directory or raise an error if not found."""
    whirr_dir = find_whirr_dir()
    if whirr_dir is None:
        msg = "No .whirr directory found. Run 'whirr init' first."
        raise RuntimeError(
            msg
        )
    return whirr_dir
