# Getting Started with whirr

This guide walks you through setting up whirr and running your first experiment.

## Installation

```bash
pip install whirr
```

Or install from source:

```bash
git clone https://github.com/syntropy-systems-oss/whirr.git
cd whirr
pip install -e .
```

## Quick Start

### 1. Initialize a Project

Navigate to your ML project directory and initialize whirr:

```bash
cd my-ml-project
whirr init
```

This creates a `.whirr/` directory containing:
- `whirr.db` - SQLite database for job/run tracking
- `config.yaml` - Project configuration
- `runs/` - Directory for experiment data

### 2. Submit a Job

Queue an experiment to run:

```bash
whirr submit --name "baseline" -- python train.py --lr 0.01
```

**Important:** The `--` separator divides whirr flags from your command. Everything after `--` is passed to your script exactly as written.

More examples:

```bash
# With tags for organization
whirr submit --name "lora-r8" --tags lora,llama -- python train.py --lora-r 8

# Complex commands work fine
whirr submit -- python train.py --config configs/exp1.yaml --seed 42
```

### 3. Start a Worker

Workers pull jobs from the queue and execute them:

```bash
whirr worker
```

The worker will:
- Claim the next queued job
- Run it in a subprocess
- Capture stdout/stderr to `output.log`
- Send heartbeats every 30 seconds
- Mark the job complete/failed when done
- Repeat until no jobs remain

**Tip:** Run the worker in tmux or screen for long-running experiments:

```bash
tmux new -s worker
whirr worker
# Ctrl+B, D to detach
```

### 4. Monitor Progress

Check job status:

```bash
whirr status
```

Output:
```
┌────┬──────────┬─────────┬─────────┬──────────────┐
│ ID │ Name     │ Status  │ Runtime │ Submitted    │
├────┼──────────┼─────────┼─────────┼──────────────┤
│ 1  │ baseline │ running │ 5m 32s  │ 5 minutes ago│
│ 2  │ lora-r8  │ queued  │ -       │ 3 minutes ago│
└────┴──────────┴─────────┴─────────┴──────────────┘
```

View logs:

```bash
# Current output
whirr logs 1

# Follow in real-time (like tail -f)
whirr logs 1 --follow
```

### 5. View Results

List completed runs:

```bash
whirr runs
```

Show details for a specific run:

```bash
whirr show <run-id>
```

## Adding Metric Tracking

### Basic Usage

Add whirr tracking to your training script:

```python
import whirr

# Initialize a run
run = whirr.init(
    name="my-experiment",
    config={"lr": 0.01, "batch_size": 32}
)

# Training loop
for epoch in range(100):
    loss = train_epoch()
    acc = evaluate()

    # Log metrics
    run.log({"loss": loss, "accuracy": acc}, step=epoch)

# Record final results
run.summary({"best_loss": best_loss, "best_epoch": best_epoch})

# Finish the run
run.finish()
```

### Context Manager

Use the context manager for automatic cleanup:

```python
import whirr

with whirr.init(name="my-experiment", config=config) as run:
    for epoch in range(100):
        loss = train_epoch()
        run.log({"loss": loss}, step=epoch)

    run.summary({"final_loss": loss})
# Automatically marked complete (or failed if exception raised)
```

### Works Both Ways

The same code works whether you:
- Run directly: `python train.py`
- Run via queue: `whirr submit -- python train.py`

When run via the queue, whirr automatically links the run to the job.

## Running Parameter Sweeps

Create a sweep configuration file:

```yaml
# sweep.yaml
program: python train.py
name: lr-sweep
method: grid
parameters:
  lr:
    values: [0.01, 0.001, 0.0001]
  batch_size:
    values: [32, 64]
```

Preview and submit the sweep:

```bash
# Preview jobs without submitting
whirr sweep sweep.yaml --dry-run

# Submit all jobs
whirr sweep sweep.yaml
# Output: Submitted 6 jobs (3 lr values × 2 batch sizes)
```

This creates jobs like:
```
python train.py --lr 0.01 --batch_size 32
python train.py --lr 0.01 --batch_size 64
python train.py --lr 0.001 --batch_size 32
...
```

## Live Monitoring

Watch job and worker status in real-time:

```bash
whirr watch
```

This displays a live-updating dashboard showing all active jobs and workers. Press `Ctrl+C` to exit.

## Retrying Failed Jobs

If a job fails, retry it:

```bash
whirr retry 5
# Output: Created retry job #8 (attempt 2 of job #5)
```

The retry creates a new job with the same command, linking back to the original.

## Cancelling Jobs

Cancel a queued job:

```bash
whirr cancel 2
```

Cancel a running job (sends SIGTERM, then SIGKILL):

```bash
whirr cancel 1
```

Cancel all queued jobs:

```bash
whirr cancel --all-queued
```

## Health Check

Verify your setup:

```bash
whirr doctor
```

Output:
```
✓ whirr directory: .whirr
✓ SQLite: WAL mode enabled
✓ Database: 2 jobs, 1 run
✓ Runs directory: writable
```

## Project Structure

After running some experiments, your project looks like:

```
my-ml-project/
├── train.py
├── .whirr/
│   ├── config.yaml
│   ├── whirr.db
│   └── runs/
│       ├── job-1/
│       │   ├── meta.json
│       │   ├── config.json
│       │   ├── metrics.jsonl
│       │   ├── system.jsonl   # CPU/GPU metrics
│       │   └── output.log
│       └── local-20240115-103045-a1b2/
│           ├── meta.json
│           ├── config.json
│           ├── metrics.jsonl
│           └── system.jsonl
```

- `job-*` directories are from queued runs
- `local-*` directories are from direct runs (no queue)

## Next Steps

- [CLI Reference](cli-reference.md) - All commands and options
- [Python API](python-api.md) - Full API documentation
- [Architecture](architecture.md) - How whirr works under the hood
