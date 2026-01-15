# Copyright (c) Syntropy Systems
"""Sweep configuration and job generation."""
from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, TypedDict, cast

import yaml

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from whirr.models.base import JSONValue

SCI_NOTATION_THRESHOLD = 1e-4


class SweepParamSpec(TypedDict, total=False):
    """Parameter specification for sweeps."""

    values: list[JSONValue]
    distribution: str
    min: float
    max: float


@dataclass
class SweepConfig:
    """Configuration for a parameter sweep."""

    program: str
    parameters: dict[str, SweepParamSpec]
    method: str = "grid"  # grid, random
    name: str | None = None
    max_runs: int | None = None  # For random sweeps

    @classmethod
    def from_yaml(cls, path: Path) -> SweepConfig:
        """Load sweep configuration from YAML file."""
        with path.open() as f:
            data = cast("dict[str, object]", yaml.safe_load(f))

        if "program" not in data:
            msg = "Sweep config must have 'program' field"
            raise ValueError(msg)
        if "parameters" not in data:
            msg = "Sweep config must have 'parameters' field"
            raise ValueError(msg)

        return cls(
            program=cast("str", data["program"]),
            parameters=cast("dict[str, SweepParamSpec]", data["parameters"]),
            method=cast("str", data.get("method", "grid")),
            name=cast("Optional[str]", data.get("name")),
            max_runs=cast("Optional[int]", data.get("max_runs")),
        )


@dataclass
class SweepJob:
    """A single job configuration from a sweep."""

    command: list[str]
    name: str
    tags: list[str]
    config: dict[str, JSONValue]


def generate_grid_combinations(
    parameters: dict[str, SweepParamSpec],
) -> Iterator[dict[str, JSONValue]]:
    """Generate all combinations for a grid sweep.

    Each parameter should have a 'values' key with a list of values.
    """
    # Extract parameter names and their values
    param_names: list[str] = []
    param_values: list[list[JSONValue]] = []

    for name, spec in parameters.items():
        if "values" not in spec:
            msg = f"Parameter '{name}' must have 'values' for grid sweep"
            raise ValueError(msg)
        param_names.append(name)
        param_values.append(spec["values"])

    # Generate all combinations
    for combo in itertools.product(*param_values):
        yield dict(zip(param_names, combo))


def generate_random_combinations(
    parameters: dict[str, SweepParamSpec],
    max_runs: int,
    seed: int | None = None,
) -> Iterator[dict[str, JSONValue]]:
    """Generate random parameter combinations.

    Supports:
    - values: list of discrete values
    - distribution: 'uniform', 'log_uniform', 'int_uniform'
    - min/max: range for distributions
    """
    rng = random.Random(seed)  # noqa: S311

    for _ in range(max_runs):
        config: dict[str, JSONValue] = {}
        for name, spec in parameters.items():
            if "values" in spec:
                config[name] = rng.choice(spec["values"])
            elif "distribution" in spec:
                dist = spec["distribution"]
                min_val = float(spec.get("min", 0.0))
                max_val = float(spec.get("max", 1.0))

                if dist == "uniform":
                    config[name] = rng.uniform(min_val, max_val)
                elif dist == "log_uniform":
                    log_min = math.log(min_val)
                    log_max = math.log(max_val)
                    config[name] = math.exp(rng.uniform(log_min, log_max))
                elif dist == "int_uniform":
                    config[name] = rng.randint(int(min_val), int(max_val))
                else:
                    msg = f"Unknown distribution: {dist}"
                    raise ValueError(msg)
            else:
                msg = f"Parameter '{name}' must have 'values' or 'distribution'"
                raise ValueError(msg)
        yield config


def generate_sweep_jobs(
    sweep: SweepConfig,
    prefix: str | None = None,
) -> list[SweepJob]:
    """Generate all jobs for a sweep configuration.

    Returns list of SweepJob objects with command, name, tags, and config.
    """
    # Determine base name
    base_name = prefix or sweep.name or "sweep"

    # Generate parameter combinations
    if sweep.method == "grid":
        combinations = list(generate_grid_combinations(sweep.parameters))
    elif sweep.method == "random":
        if sweep.max_runs is None:
            msg = "Random sweeps require 'max_runs' to be set"
            raise ValueError(msg)
        combinations = list(
            generate_random_combinations(sweep.parameters, sweep.max_runs)
        )
    else:
        msg = f"Unknown sweep method: {sweep.method}"
        raise ValueError(msg)

    # Build jobs
    jobs: list[SweepJob] = []
    for i, params in enumerate(combinations):
        # Build command with parameters as CLI args
        command = sweep.program.split()
        for key, value in params.items():
            # Format value appropriately
            if isinstance(value, float):
                # Use scientific notation for very small values
                if abs(value) < SCI_NOTATION_THRESHOLD and value != 0.0:
                    formatted = f"{value:.2e}"
                else:
                    formatted = str(value)
            else:
                formatted = str(value)
            command.extend([f"--{key}", formatted])

        # Generate job name
        job_name = f"{base_name}-{i}"

        # Tags include sweep name
        tags = [f"sweep:{base_name}"]

        jobs.append(
            SweepJob(
                command=command,
                name=job_name,
                tags=tags,
                config=params,
            )
        )

    return jobs
