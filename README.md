# whirr

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://github.com/syntropy-systems-oss/whirr/actions/workflows/tests.yml/badge.svg)](https://github.com/syntropy-systems-oss/whirr/actions/workflows/tests.yml)

Local experiment orchestration. Queue jobs, track metrics, wake up to results.

**whirr** is a lightweight, self-hosted alternative to experiment tracking tools. No cloud, no accounts, no external dependencies - just SQLite and your filesystem.

## Features

- **Job Queue** - Submit experiments, run them on workers
- **Metric Tracking** - Log metrics from Python, store as append-only JSONL
- **Multi-GPU Support** - One worker per GPU, automatic device assignment
- **Process Safety** - Orphan prevention with PDEATHSIG, graceful shutdown
- **Concurrent Access** - SQLite WAL mode for safe multi-process access
- **Web Dashboard** - Interactive UI for viewing runs and comparing metrics
- **Reproducibility** - Automatic capture of git commit and pip freeze
- **Server Mode** - Multi-machine orchestration with PostgreSQL backend

## Installation

```bash
pip install whirr
```

Optional dependencies:

```bash
# For dashboard
pip install whirr[dashboard]

# For server mode (PostgreSQL, HTTP API)
pip install whirr[server]

# For all features
pip install whirr[all]
```

Or install from source:

```bash
git clone https://github.com/syntropy-systems-oss/whirr.git
cd whirr
pip install -e ".[all]"
```

## Quick Start

### Initialize a Project

```bash
cd your-ml-project
whirr init
```

This creates a `.whirr/` directory with the database and configuration.

### Submit Jobs

```bash
# Submit a training job
whirr submit --name baseline -- python train.py --lr 0.01

# With tags for organization
whirr submit --name sweep-lr-001 --tags sweep,lr -- python train.py --lr 0.001
```

### Start Workers

```bash
# Start a worker (claims and runs jobs from the queue)
whirr worker

# For multi-GPU, start one worker per GPU
CUDA_VISIBLE_DEVICES=0 whirr worker --device 0 &
CUDA_VISIBLE_DEVICES=1 whirr worker --device 1 &
```

### Monitor Progress

```bash
# View active jobs
whirr status

# View specific job details
whirr status 1

# Follow job logs
whirr logs 1 --follow

# List completed runs
whirr runs
```

### Cancel Jobs

```bash
# Cancel a specific job
whirr cancel 1

# Cancel all queued jobs
whirr cancel --all-queued
```

## Python Library

Track metrics directly from your training scripts:

```python
import whirr

# Initialize a run
run = whirr.init(
    name="my-experiment",
    config={"lr": 0.01, "batch_size": 32},
    tags=["baseline"]
)

# Log metrics during training
for epoch in range(100):
    loss = train_epoch()
    run.log({"loss": loss, "epoch": epoch}, step=epoch)

# Save summary metrics
run.summary({"best_loss": 0.1, "best_epoch": 42})

# Save artifacts
run.save_artifact("model.pt", "best_model.pt")

run.finish()
```

Or use as a context manager:

```python
with whirr.init(name="my-run") as run:
    run.log({"loss": 0.5})
    # Automatically marked complete or failed
```

### Direct Runs (No Queue)

You can use whirr for tracking without the job queue:

```bash
python train.py  # Uses whirr.init() directly
whirr runs       # Shows the run
```

## Server Mode (Multi-Machine)

For distributed setups with multiple GPU machines:

### Start the Server

```bash
# Using Docker Compose (recommended)
docker-compose up -d

# Or manually with PostgreSQL
whirr server --database-url postgresql://user:pass@localhost:5432/whirr
```

### Start Workers on GPU Machines

**Option 1: Rust Worker (Recommended for production)**

The Rust worker is a lightweight ~2MB binary with minimal memory footprint (~10MB RAM):

```bash
# Build the Rust worker
cd worker-rust && cargo build --release

# Copy to GPU nodes
scp target/release/whirr-worker gpu-node:/usr/local/bin/

# Run on GPU node
whirr-worker --server http://head-node:8080 --data-dir /mnt/shared/whirr --gpu 0
```

**Option 2: Python Worker**

```bash
# On each GPU machine, connect to the server
whirr worker --server http://head-node:8080 --data-dir /mnt/shared/whirr

# With GPU assignment
whirr worker --server http://head-node:8080 --data-dir /mnt/shared/whirr --gpu 0
```

### Submit Jobs Remotely

```bash
whirr submit --server http://head-node:8080 -- python train.py --lr 0.01
```

## Project Structure

```
your-project/
├── .whirr/
│   ├── config.yaml      # Project configuration
│   ├── whirr.db         # SQLite database (WAL mode)
│   └── runs/
│       └── run-id/
│           ├── meta.json       # Run metadata
│           ├── config.json     # Run configuration
│           ├── metrics.jsonl   # Logged metrics
│           ├── output.log      # stdout/stderr
│           └── artifacts/      # Saved files
```

## CLI Reference

| Command | Description |
|---------|-------------|
| `whirr init` | Initialize whirr in current directory |
| `whirr submit -- CMD` | Submit a job to the queue |
| `whirr status [ID]` | Show job status |
| `whirr worker` | Start a worker process |
| `whirr logs ID` | View job output |
| `whirr cancel ID` | Cancel a job |
| `whirr runs` | List all runs |
| `whirr dashboard` | Launch web dashboard |
| `whirr compare ID1 ID2` | Compare runs side-by-side |
| `whirr export ID` | Export run data |
| `whirr server` | Start multi-machine server |
| `whirr doctor` | Diagnose configuration issues |

## Documentation

Full documentation is available in the [`docs/`](docs/) directory:

- [Getting Started](docs/getting-started.md) - Installation and first steps
- [CLI Reference](docs/cli-reference.md) - All commands and options
- [Python API](docs/python-api.md) - Library reference and examples
- [Architecture](docs/architecture.md) - How whirr works under the hood

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run specific test file
pytest tests/test_db.py -v
```

## License

MIT License - see [LICENSE](LICENSE) for details.
