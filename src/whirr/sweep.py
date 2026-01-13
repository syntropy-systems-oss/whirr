"""Sweep configuration and job generation."""

import itertools
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

import yaml


@dataclass
class SweepConfig:
    """Configuration for a parameter sweep."""

    program: str
    parameters: dict[str, dict[str, Any]]
    method: str = "grid"  # grid, random
    name: Optional[str] = None
    max_runs: Optional[int] = None  # For random sweeps

    @classmethod
    def from_yaml(cls, path: Path) -> "SweepConfig":
        """Load sweep configuration from YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)

        if "program" not in data:
            raise ValueError("Sweep config must have 'program' field")
        if "parameters" not in data:
            raise ValueError("Sweep config must have 'parameters' field")

        return cls(
            program=data["program"],
            parameters=data["parameters"],
            method=data.get("method", "grid"),
            name=data.get("name"),
            max_runs=data.get("max_runs"),
        )


@dataclass
class SweepJob:
    """A single job configuration from a sweep."""

    command: list[str]
    name: str
    tags: list[str]
    config: dict[str, Any]


def generate_grid_combinations(parameters: dict[str, dict]) -> Iterator[dict[str, Any]]:
    """
    Generate all combinations for a grid sweep.

    Each parameter should have a 'values' key with a list of values.
    """
    # Extract parameter names and their values
    param_names = []
    param_values = []

    for name, spec in parameters.items():
        if "values" not in spec:
            raise ValueError(f"Parameter '{name}' must have 'values' for grid sweep")
        param_names.append(name)
        param_values.append(spec["values"])

    # Generate all combinations
    for combo in itertools.product(*param_values):
        yield dict(zip(param_names, combo))


def generate_random_combinations(
    parameters: dict[str, dict],
    max_runs: int,
    seed: Optional[int] = None,
) -> Iterator[dict[str, Any]]:
    """
    Generate random parameter combinations.

    Supports:
    - values: list of discrete values
    - distribution: 'uniform', 'log_uniform', 'int_uniform'
    - min/max: range for distributions
    """
    if seed is not None:
        random.seed(seed)

    for _ in range(max_runs):
        config = {}
        for name, spec in parameters.items():
            if "values" in spec:
                config[name] = random.choice(spec["values"])
            elif "distribution" in spec:
                dist = spec["distribution"]
                min_val = spec.get("min", 0)
                max_val = spec.get("max", 1)

                if dist == "uniform":
                    config[name] = random.uniform(min_val, max_val)
                elif dist == "log_uniform":
                    import math
                    log_min = math.log(min_val)
                    log_max = math.log(max_val)
                    config[name] = math.exp(random.uniform(log_min, log_max))
                elif dist == "int_uniform":
                    config[name] = random.randint(int(min_val), int(max_val))
                else:
                    raise ValueError(f"Unknown distribution: {dist}")
            else:
                raise ValueError(f"Parameter '{name}' must have 'values' or 'distribution'")
        yield config


def generate_sweep_jobs(sweep: SweepConfig, prefix: Optional[str] = None) -> list[SweepJob]:
    """
    Generate all jobs for a sweep configuration.

    Returns list of SweepJob objects with command, name, tags, and config.
    """
    # Determine base name
    base_name = prefix or sweep.name or "sweep"

    # Generate parameter combinations
    if sweep.method == "grid":
        combinations = list(generate_grid_combinations(sweep.parameters))
    elif sweep.method == "random":
        if sweep.max_runs is None:
            raise ValueError("Random sweeps require 'max_runs' to be set")
        combinations = list(generate_random_combinations(sweep.parameters, sweep.max_runs))
    else:
        raise ValueError(f"Unknown sweep method: {sweep.method}")

    # Build jobs
    jobs = []
    for i, params in enumerate(combinations):
        # Build command with parameters as CLI args
        command = sweep.program.split()
        for key, value in params.items():
            # Format value appropriately
            if isinstance(value, float):
                # Use scientific notation for very small values
                if abs(value) < 0.0001 and value != 0:
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

        jobs.append(SweepJob(
            command=command,
            name=job_name,
            tags=tags,
            config=params,
        ))

    return jobs
