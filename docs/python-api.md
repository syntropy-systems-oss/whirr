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
    system_metrics: bool = True
) -> Run
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | `str` | Human-readable name for the run |
| `config` | `dict` | Configuration/hyperparameters to record |
| `tags` | `list[str]` | Tags for organizing runs |
| `system_metrics` | `bool` | Enable automatic system metrics collection (default: True) |

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
```

**Behavior:**

- Detects if running inside a whirr worker (via `WHIRR_JOB_ID` env var)
- If in worker context: links to the job's run directory
- If direct execution: creates a new run with `local-*` ID

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
