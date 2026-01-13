# CLI Reference

Complete reference for all whirr commands.

## Global Behavior

### The `--` Separator

All commands that accept a user command use `--` to separate whirr flags from the command:

```bash
whirr submit --name "my-job" --tags test -- python train.py --lr 0.01
#            ^^^^^^^^^^^^^^^^^^^^^^^^      ^^^^^^^^^^^^^^^^^^^^^^^
#            whirr flags                   your command (passed literally)
```

This prevents ambiguity when your command has flags that might conflict with whirr's flags.

---

## Commands

### `whirr init`

Initialize whirr in the current directory.

```bash
whirr init
```

Creates:
- `.whirr/whirr.db` - SQLite database
- `.whirr/config.yaml` - Configuration file
- `.whirr/runs/` - Directory for run data

**Options:** None

**Notes:**
- Safe to run multiple times (will report "Already initialized")
- Must be run before other commands

---

### `whirr submit`

Submit a job to the queue.

```bash
whirr submit [OPTIONS] -- COMMAND [ARGS]...
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--name TEXT` | `-n` | Job name (for identification) |
| `--tags TEXT` | `-t` | Comma-separated tags |

**Examples:**

```bash
# Basic submission
whirr submit -- python train.py

# With name
whirr submit --name "baseline-v1" -- python train.py

# With name and tags
whirr submit -n "lora-r8" -t "lora,llama,sweep" -- python train.py --lora-r 8

# Complex command
whirr submit -- python -m torch.distributed.launch --nproc_per_node=1 train.py
```

**Notes:**
- The `--` separator is required
- The command is stored as-is and executed in the current working directory
- Returns the job ID on success

---

### `whirr status`

Show job status.

```bash
whirr status [JOB_ID]
```

**Arguments:**

| Argument | Description |
|----------|-------------|
| `JOB_ID` | (Optional) Show details for specific job |

**Without JOB_ID** - Shows table of all active jobs (queued + running):

```
┌────┬──────────┬─────────┬─────────┬──────────────┐
│ ID │ Name     │ Status  │ Runtime │ Submitted    │
├────┼──────────┼─────────┼─────────┼──────────────┤
│ 1  │ baseline │ running │ 5m 32s  │ 5 minutes ago│
│ 2  │ lora-r8  │ queued  │ -       │ 3 minutes ago│
└────┴──────────┴─────────┴─────────┴──────────────┘
```

**With JOB_ID** - Shows detailed information:

```
Job #1: baseline
  status: running
  command: python train.py --lr 0.01
  workdir: /home/user/project
  worker: host:gpu0
  submitted: 2024-01-15 10:30:00
  started: 2024-01-15 10:30:05
  runtime: 5m 32s
```

---

### `whirr worker`

Start a worker to process jobs from the queue.

```bash
whirr worker [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--device INT` | GPU device index (for multi-GPU setups) |

**Behavior:**

1. Claims the next queued job atomically
2. Runs it in a subprocess with:
   - Separate process group (for clean termination)
   - stdout/stderr captured to `output.log`
3. Sends heartbeat every 30 seconds
4. Marks job complete/failed when done
5. Repeats until queue is empty

**Graceful Shutdown:**

Press `Ctrl+C` to stop the worker gracefully:
- Finishes the current job
- Then exits

Press `Ctrl+C` twice to force stop:
- Kills the current job immediately
- Exits

**Multi-GPU Example:**

```bash
# Terminal 1
CUDA_VISIBLE_DEVICES=0 whirr worker --device 0

# Terminal 2
CUDA_VISIBLE_DEVICES=1 whirr worker --device 1
```

---

### `whirr logs`

View job output logs.

```bash
whirr logs JOB_ID [OPTIONS]
```

**Arguments:**

| Argument | Description |
|----------|-------------|
| `JOB_ID` | Job ID to show logs for |

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--follow` | `-f` | Follow output in real-time (like `tail -f`) |

**Examples:**

```bash
# Show current logs
whirr logs 1

# Follow logs as they're written
whirr logs 1 --follow
```

**Notes:**
- Handles jobs that haven't started yet (waits for log file)
- `--follow` can be interrupted with `Ctrl+C`

---

### `whirr cancel`

Cancel a job.

```bash
whirr cancel [JOB_ID] [OPTIONS]
```

**Arguments:**

| Argument | Description |
|----------|-------------|
| `JOB_ID` | Job ID to cancel |

**Options:**

| Option | Description |
|--------|-------------|
| `--all-queued` | Cancel all queued jobs |

**Behavior by Status:**

| Job Status | Action |
|------------|--------|
| `queued` | Immediately marked as `cancelled` |
| `running` | SIGTERM sent, then SIGKILL after 10s grace period |

**Examples:**

```bash
# Cancel specific job
whirr cancel 1

# Cancel all queued jobs
whirr cancel --all-queued
```

---

### `whirr runs`

List experiment runs.

```bash
whirr runs [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--status TEXT` | `-s` | Filter by status (running, completed, failed) |
| `--tag TEXT` | `-t` | Filter by tag |
| `--last INT` | `-n` | Number of runs to show (default: 20) |

**Examples:**

```bash
# List recent runs
whirr runs

# Only failed runs
whirr runs --status failed

# Runs with specific tag
whirr runs --tag lora

# Last 50 runs
whirr runs --last 50
```

---

### `whirr show`

Show detailed information about a run.

```bash
whirr show RUN_ID
```

**Arguments:**

| Argument | Description |
|----------|-------------|
| `RUN_ID` | Run ID (full or prefix) |

**Output includes:**
- Run metadata (name, status, duration)
- Configuration
- Summary metrics
- Recent logged metrics

**Example:**

```bash
whirr show job-1
whirr show local-2024  # Prefix match works
```

---

### `whirr doctor`

Check whirr configuration and health.

```bash
whirr doctor
```

**Checks:**
- `.whirr` directory exists
- SQLite WAL mode enabled
- Database readable
- Runs directory writable

**Example output:**

```
✓ whirr directory: .whirr
✓ SQLite: WAL mode enabled
✓ Database: 5 jobs, 3 runs
✓ Runs directory: writable
```

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Error (invalid arguments, not initialized, etc.) |

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `WHIRR_JOB_ID` | Set by worker when running a job |
| `WHIRR_RUN_DIR` | Set by worker - path to run directory |
| `WHIRR_RUN_ID` | Set by worker - run ID |

These are automatically set when your script runs via `whirr worker`. You typically don't need to use them directly - `whirr.init()` detects them automatically.
