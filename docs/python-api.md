# Python API Reference

The whirr Python library for tracking experiments.

## Quick Example

```python
import whirr

run = whirr.init(name="my-experiment", config={"lr": 0.01})

for epoch in range(100):
    loss = train_epoch()
    run.log({"loss": loss}, step=epoch)

run.summary({"best_loss": 0.1})
run.finish()
```

---

## Module: `whirr`

### `whirr.init()`

Initialize a new run for tracking.

```python
whirr.init(
    name: str = None,
    config: dict = None,
    tags: list[str] = None,
    system_metrics: bool = True,
    capture_git: bool = True,
    capture_pip: bool = True
) -> Run
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | `str` | Human-readable name for the run |
| `config` | `dict` | Configuration/hyperparameters to record |
| `tags` | `list[str]` | Tags for organizing runs |
| `system_metrics` | `bool` | Enable automatic system metrics collection (default: True) |
| `capture_git` | `bool` | Capture git commit hash and dirty state (default: True) |
| `capture_pip` | `bool` | Capture pip freeze snapshot to requirements.txt (default: True) |

**Returns:** A `Run` instance

**Example:**

```python
import whirr

run = whirr.init(
    name="baseline-lr-0.01",
    config={
        "lr": 0.01,
        "batch_size": 32,
        "epochs": 100,
        "model": "resnet50"
    },
    tags=["baseline", "resnet"]
)

# Disable environment capture for faster startup
run = whirr.init(
    name="quick-test",
    capture_git=False,
    capture_pip=False
)
```

**Behavior:**

- Detects if running inside a whirr worker (via `WHIRR_JOB_ID` env var)
- If in worker context: links to the job's run directory
- If direct execution: creates a new run with `local-*` ID
- If `capture_git=True`: records git commit hash, branch, and dirty state
- If `capture_pip=True`: saves pip freeze output to `requirements.txt` in run directory

---

## Class: `Run`

The main interface for tracking an experiment run.

### Constructor

```python
Run(
    name: str = None,
    config: dict = None,
    tags: list[str] = None
)
```

Typically you should use `whirr.init()` instead of constructing directly.

---

### `run.log()`

Log metrics for this run.

```python
run.log(metrics: dict, step: int = None) -> None
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `metrics` | `dict` | Key-value pairs of metrics |
| `step` | `int` | Optional step/epoch number |

**Example:**

```python
# With step
run.log({"loss": 0.5, "accuracy": 0.85}, step=10)

# Without step (just logs the values)
run.log({"loss": 0.5})

# Multiple metrics
run.log({
    "train_loss": 0.5,
    "val_loss": 0.6,
    "train_acc": 0.85,
    "val_acc": 0.80,
    "learning_rate": 0.001
}, step=epoch)
```

**Notes:**

- Metrics are appended to `metrics.jsonl`
- Each log entry includes `_timestamp` (UTC) and `_idx` (monotonic counter)
- If `step` is provided, it should be monotonically increasing
- Call frequently - data is flushed immediately

---

### `run.summary()`

Record final summary metrics.

```python
run.summary(metrics: dict) -> None
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `metrics` | `dict` | Final metrics to record |

**Example:**

```python
run.summary({
    "best_loss": 0.123,
    "best_epoch": 87,
    "final_accuracy": 0.956,
    "total_training_time": 3600
})
```

**Notes:**

- Called once at the end of training
- Stored in `meta.json` under the `summary` key
- Displayed in `whirr runs` and `whirr show` output

---

### `run.save_artifact()`

Save a file as an artifact.

```python
run.save_artifact(
    source_path: str,
    artifact_name: str = None
) -> Path
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `source_path` | `str` | Path to the file to save |
| `artifact_name` | `str` | Name for the artifact (default: original filename) |

**Returns:** Path to the saved artifact

**Example:**

```python
# Save model checkpoint
run.save_artifact("checkpoints/model_best.pt", "best_model.pt")

# Save with original name
run.save_artifact("results.csv")
```

**Notes:**

- Files are copied to `{run_dir}/artifacts/`
- Original file is not modified

---

### `run.finish()`

Mark the run as complete.

```python
run.finish() -> None
```

**Example:**

```python
run.finish()
```

**Notes:**

- Records `finished_at` timestamp and duration
- Sets status to `completed`
- Safe to call multiple times
- Called automatically when using context manager
- After calling, `log()` will raise `RuntimeError`

---

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `run.run_id` | `str` | Unique run identifier |
| `run.run_dir` | `Path` | Directory containing run data |
| `run.config` | `dict` | Configuration dict (read-only) |

---

## Context Manager

Use `Run` as a context manager for automatic cleanup:

```python
with whirr.init(name="my-run", config=config) as run:
    for epoch in range(100):
        loss = train()
        run.log({"loss": loss}, step=epoch)

    run.summary({"final_loss": loss})

# Run is automatically finished here
# If an exception occurs, run is marked as "failed"
```

**Behavior:**

- On normal exit: `run.finish()` called, status = `completed`
- On exception: status = `failed`, exception re-raised

---

## Utility Functions

### `whirr.read_metrics()`

Read metrics from a JSONL file.

```python
from whirr.run import read_metrics

metrics = read_metrics(Path("path/to/metrics.jsonl"))
# Returns: list[dict]
```

**Notes:**

- Tolerates truncated final line (crash-safe)
- Returns empty list for missing/empty files

---

### `whirr.read_meta()`

Read run metadata.

```python
from whirr.run import read_meta

meta = read_meta(Path("path/to/run_dir"))
# Returns: dict with keys like name, status, config, summary, etc.
```

---

## File Formats

### `metrics.jsonl`

Append-only log of metrics:

```jsonl
{"_idx": 0, "_timestamp": "2024-01-15T10:30:00Z", "step": 0, "loss": 2.34}
{"_idx": 1, "_timestamp": "2024-01-15T10:30:15Z", "step": 1, "loss": 1.89}
{"_idx": 2, "_timestamp": "2024-01-15T10:30:30Z", "step": 2, "loss": 1.45}
```

Fields:
- `_idx`: Monotonically increasing counter (always present)
- `_timestamp`: UTC ISO timestamp (always present)
- `step`: User-provided step (optional)
- Other keys: User metrics

### `meta.json`

Run metadata:

```json
{
  "run_id": "job-1",
  "name": "baseline",
  "status": "completed",
  "started_at": "2024-01-15T10:30:00Z",
  "finished_at": "2024-01-15T11:30:00Z",
  "duration_seconds": 3600.5,
  "tags": ["baseline", "resnet"],
  "config_file": "config.json",
  "summary": {
    "best_loss": 0.123,
    "best_epoch": 87
  }
}
```

### `config.json`

Separate file for configuration (keeps meta.json small):

```json
{
  "lr": 0.01,
  "batch_size": 32,
  "epochs": 100
}
```

### `system.jsonl`

Automatic system metrics collected during the run (if `system_metrics=True`):

```jsonl
{"_timestamp": "2024-01-15T10:30:00Z", "cpu_percent": 45.2, "memory_percent": 62.1}
{"_timestamp": "2024-01-15T10:30:10Z", "cpu_percent": 78.5, "memory_percent": 63.0, "gpu_0_utilization": 95, "gpu_0_memory_used": 8192}
```

Fields (when available):
- `cpu_percent`: CPU utilization percentage
- `memory_percent`: System memory usage percentage
- `gpu_N_utilization`: GPU N utilization (requires nvidia-smi)
- `gpu_N_memory_used`: GPU N memory used in MB
- `gpu_N_memory_total`: GPU N total memory in MB
- `gpu_N_temperature`: GPU N temperature in Celsius

**Notes:**
- Collected every 10 seconds by default
- GPU metrics only available on systems with NVIDIA GPUs and nvidia-smi
- CPU/memory metrics require the `psutil` package (`pip install whirr[metrics]`)
- Disable with `whirr.init(system_metrics=False)`

### `git_info` in `meta.json`

When `capture_git=True`, git information is stored in `meta.json`:

```json
{
  "git_info": {
    "commit": "a1b2c3d4e5f6...",
    "branch": "main",
    "dirty": false,
    "remote_url": "git@github.com:user/repo.git"
  }
}
```

**Notes:**
- `dirty`: True if there are uncommitted changes
- Only captured if the working directory is a git repository
- Disable with `whirr.init(capture_git=False)`

### `requirements.txt`

When `capture_pip=True`, pip freeze output is saved:

```
torch==2.1.0
numpy==1.24.0
whirr==0.4.0
...
```

**Notes:**
- Captures exact package versions for reproducibility
- Saved to `{run_dir}/requirements.txt`
- Disable with `whirr.init(capture_pip=False)`

---

## Integration Patterns

### With argparse

```python
import argparse
import whirr

parser = argparse.ArgumentParser()
parser.add_argument("--lr", type=float, default=0.01)
parser.add_argument("--epochs", type=int, default=100)
args = parser.parse_args()

run = whirr.init(
    name=f"lr-{args.lr}",
    config=vars(args)  # Convert namespace to dict
)

for epoch in range(args.epochs):
    # training...
    run.log({"loss": loss}, step=epoch)

run.finish()
```

### With PyTorch

```python
import torch
import whirr

run = whirr.init(name="resnet-training", config={"lr": 0.01})

model = ResNet()
optimizer = torch.optim.Adam(model.parameters(), lr=0.01)

for epoch in range(100):
    for batch in dataloader:
        loss = train_step(model, batch, optimizer)

    val_loss = evaluate(model, val_loader)
    run.log({"train_loss": loss, "val_loss": val_loss}, step=epoch)

    # Save best model
    if val_loss < best_loss:
        best_loss = val_loss
        torch.save(model.state_dict(), "best_model.pt")
        run.save_artifact("best_model.pt")

run.summary({"best_val_loss": best_loss})
run.finish()
```

### With Hugging Face Transformers

```python
import whirr
from transformers import Trainer, TrainingArguments

run = whirr.init(
    name="bert-finetune",
    config={"model": "bert-base", "lr": 2e-5}
)

class WhirrCallback(TrainerCallback):
    def on_log(self, args, state, control, logs=None, **kwargs):
        if logs:
            run.log(logs, step=state.global_step)

trainer = Trainer(
    model=model,
    args=training_args,
    callbacks=[WhirrCallback()]
)

trainer.train()
run.summary({"final_loss": trainer.state.best_metric})
run.finish()
```

---

## Class: `WhirrClient`

HTTP client for programmatic access to a whirr server. Useful for submitting jobs,
waiting for results, and retrieving metrics from external tools.

```python
from whirr.client import WhirrClient

client = WhirrClient("http://head-node:8080")
```

**Requires:** `pip install whirr[server]` (for httpx dependency)

---

### `client.submit_job()`

Submit a new job to the queue.

```python
client.submit_job(
    command_argv: list[str],
    workdir: str,
    name: str = None,
    config: dict = None,
    tags: list[str] = None
) -> dict
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `command_argv` | `list[str]` | Command to run as list of arguments |
| `workdir` | `str` | Working directory for the command |
| `name` | `str` | Optional job name |
| `config` | `dict` | Optional configuration dict |
| `tags` | `list[str]` | Optional list of tags |

**Returns:** Dict with `job_id`, `run_id`, `run_dir`, and `message`

**Example:**

```python
result = client.submit_job(
    command_argv=["python", "train.py", "--lr", "0.01"],
    workdir="/home/user/project",
    name="experiment-1",
    config={"lr": 0.01},
    tags=["baseline"],
)
job_id = result["job_id"]
run_id = result["run_id"]  # e.g., "job-1"
run_dir = result["run_dir"]  # e.g., "/srv/whirr/runs/job-1"
```

---

### `client.wait_for_job()`

Wait for a job to complete by polling its status.

```python
client.wait_for_job(
    job_id: int,
    poll_interval: float = 1.0,
    timeout: float = None
) -> dict
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `job_id` | `int` | Job ID to wait for |
| `poll_interval` | `float` | Seconds between status checks (default: 1.0) |
| `timeout` | `float` | Maximum seconds to wait (default: None = forever) |

**Returns:** Final job dict with status

**Raises:**
- `TimeoutError`: If timeout reached before job completes
- `WhirrClientError`: If job not found

**Example:**

```python
# Wait indefinitely
job = client.wait_for_job(job_id)
print(f"Job finished with exit code: {job['exit_code']}")

# With timeout
try:
    job = client.wait_for_job(job_id, timeout=3600)  # 1 hour max
except TimeoutError:
    print("Job took too long")
```

---

### `client.submit_and_wait()`

Submit a job and wait for it to complete (convenience method).

```python
client.submit_and_wait(
    command_argv: list[str],
    workdir: str,
    name: str = None,
    config: dict = None,
    tags: list[str] = None,
    poll_interval: float = 1.0,
    timeout: float = None
) -> dict
```

**Example:**

```python
job = client.submit_and_wait(
    command_argv=["python", "train.py"],
    workdir="/home/user/project",
    name="quick-job",
    timeout=600,  # 10 minute timeout
)
print(f"Finished: {job['status']}")
```

---

### `client.get_metrics()`

Get metrics for a completed run.

```python
client.get_metrics(run_id: str) -> list[dict]
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `run_id` | `str` | Run ID (e.g., "job-1") |

**Returns:** List of metric records from the run's `metrics.jsonl`

**Example:**

```python
metrics = client.get_metrics("job-1")
for m in metrics:
    print(f"Step {m.get('step')}: loss={m.get('loss')}")
```

---

### `client.list_artifacts()`

List all files in a run directory.

```python
client.list_artifacts(run_id: str) -> list[dict]
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `run_id` | `str` | Run ID (e.g., "job-1") |

**Returns:** List of artifact dicts with `path`, `size`, and `modified` timestamp

**Example:**

```python
artifacts = client.list_artifacts("job-1")
for a in artifacts:
    print(f"{a['path']} ({a['size']} bytes)")

# Output:
# metrics.jsonl (1234 bytes)
# output.log (5678 bytes)
# artifacts/model.pt (1000000 bytes)
```

---

### `client.get_artifact()`

Download a file from a run directory.

```python
client.get_artifact(run_id: str, path: str) -> bytes
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `run_id` | `str` | Run ID (e.g., "job-1") |
| `path` | `str` | Relative path to the file (e.g., "output.log") |

**Returns:** Raw file content as bytes

**Example:**

```python
# Download log file
log_content = client.get_artifact("job-1", "output.log")
print(log_content.decode())

# Download model checkpoint
model_bytes = client.get_artifact("job-1", "artifacts/model.pt")
with open("downloaded_model.pt", "wb") as f:
    f.write(model_bytes)
```

---

### Other Client Methods

| Method | Description |
|--------|-------------|
| `get_job(job_id)` | Get job details |
| `get_run(run_id)` | Get run details |
| `get_runs(status, tag, limit)` | List runs with optional filtering |
| `get_status()` | Get server status and statistics |
| `cancel_job(job_id)` | Cancel a queued or running job |

---

### Full Example: Batch Job Submission

```python
from whirr.client import WhirrClient

client = WhirrClient("http://head-node:8080")

# Submit multiple jobs
configs = [
    {"lr": 0.01},
    {"lr": 0.001},
    {"lr": 0.0001},
]

job_ids = []
for i, config in enumerate(configs):
    result = client.submit_job(
        command_argv=["python", "train.py", "--lr", str(config["lr"])],
        workdir="/home/user/project",
        name=f"sweep-{i}",
        config=config,
        tags=["sweep"],
    )
    job_ids.append(result["job_id"])

print(f"Submitted {len(job_ids)} jobs")

# Wait for all to complete
results = []
for job_id in job_ids:
    job = client.wait_for_job(job_id)
    if job["status"] == "completed":
        run_id = job.get("run_id", f"job-{job_id}")
        metrics = client.get_metrics(run_id)
        results.append(metrics)
    else:
        print(f"Job {job_id} failed")

# Analyze results
for i, metrics in enumerate(results):
    if metrics:
        final = metrics[-1]
        print(f"Config {i}: final loss = {final.get('loss')}")
```
