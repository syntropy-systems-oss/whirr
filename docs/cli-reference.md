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
| `--server URL` | `-s` | Server URL for remote submission |

**Examples:**

```bash
# Basic submission (local mode)
whirr submit -- python train.py

# With name
whirr submit --name "baseline-v1" -- python train.py

# With name and tags
whirr submit -n "lora-r8" -t "lora,llama,sweep" -- python train.py --lora-r 8

# Remote submission to server
whirr submit --server http://head-node:8080 -- python train.py

# Complex command
whirr submit -- python -m torch.distributed.launch --nproc_per_node=1 train.py
```

**Notes:**
- The `--` separator is required
- The command is stored as-is and executed in the current working directory
- Returns the job ID on success
- Use `--server` for multi-machine setups with a central whirr server

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

| Option | Short | Description |
|--------|-------|-------------|
| `--gpu INT` | `-g` | GPU device index (for multi-GPU setups) |
| `--server URL` | `-s` | Server URL for remote mode |
| `--data-dir PATH` | `-d` | Data directory for runs (required for remote mode) |

**Local Mode (default):**

1. Claims the next queued job atomically from local SQLite database
2. Runs it in a subprocess with:
   - Separate process group (for clean termination)
   - stdout/stderr captured to `output.log`
3. Sends heartbeat every 30 seconds
4. Marks job complete/failed when done
5. Repeats until queue is empty

**Remote Mode (--server):**

1. Connects to the whirr server via HTTP API
2. Claims jobs from the central queue
3. Writes output to shared filesystem (--data-dir)
4. Sends heartbeats to renew job lease
5. Reports completion to server

**Graceful Shutdown:**

Press `Ctrl+C` to stop the worker gracefully:
- Finishes the current job
- Then exits

Press `Ctrl+C` twice to force stop:
- Kills the current job immediately
- Exits

**Examples:**

```bash
# Local mode - single GPU
whirr worker

# Local mode - multi-GPU
CUDA_VISIBLE_DEVICES=0 whirr worker --gpu 0 &
CUDA_VISIBLE_DEVICES=1 whirr worker --gpu 1 &

# Remote mode - connect to server
whirr worker --server http://head-node:8080 --data-dir /mnt/shared/whirr

# Remote mode with GPU assignment
whirr worker --server http://head:8080 --data-dir /data/whirr --gpu 0
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

### `whirr retry`

Retry a failed or cancelled job.

```bash
whirr retry JOB_ID
```

**Arguments:**

| Argument | Description |
|----------|-------------|
| `JOB_ID` | Job ID to retry |

**Behavior:**
- Creates a new job with the same command, workdir, name, and tags
- Sets `parent_job_id` to link to the original job
- Increments the `attempt` counter

**Examples:**

```bash
# Retry job #5
whirr retry 5
# Output: Created retry job #8 (attempt 2 of job #5)

# Check the retry
whirr status 8
```

**Notes:**
- Can only retry jobs with status `failed` or `cancelled`
- Retrying a `running` or `queued` job will fail

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

### `whirr sweep`

Generate and submit jobs from a sweep configuration file.

```bash
whirr sweep CONFIG_FILE [OPTIONS]
```

**Arguments:**

| Argument | Description |
|----------|-------------|
| `CONFIG_FILE` | Path to YAML sweep configuration |

**Options:**

| Option | Description |
|--------|-------------|
| `--prefix TEXT` | Prefix for generated job names |
| `--dry-run` | Show jobs without submitting |

**Sweep Configuration (YAML):**

```yaml
# sweep.yaml
program: python train.py
name: lr-sweep
method: grid  # or "random"
max_runs: 10  # only for random method

parameters:
  lr:
    values: [0.01, 0.001, 0.0001]
  batch_size:
    values: [16, 32, 64]
  dropout:
    distribution: uniform
    min: 0.1
    max: 0.5
```

**Parameter Types:**

| Type | Fields | Description |
|------|--------|-------------|
| Discrete | `values: [...]` | List of specific values |
| Uniform | `distribution: uniform`, `min`, `max` | Float in range |
| Log Uniform | `distribution: log_uniform`, `min`, `max` | Log-scale float |
| Int Uniform | `distribution: int_uniform`, `min`, `max` | Integer in range |

**Methods:**

| Method | Description |
|--------|-------------|
| `grid` | All combinations of discrete values |
| `random` | Random sampling (requires `max_runs`) |

**Examples:**

```bash
# Preview what jobs will be created
whirr sweep sweep.yaml --dry-run

# Submit all sweep jobs
whirr sweep sweep.yaml

# With custom prefix
whirr sweep sweep.yaml --prefix "experiment-v2"
```

**Generated Commands:**

For a config with `program: python train.py` and parameters `lr` and `batch_size`:

```bash
python train.py --lr 0.01 --batch_size 16
python train.py --lr 0.01 --batch_size 32
# ... etc
```

---

### `whirr watch`

Watch job and worker status with live updates.

```bash
whirr watch [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--interval FLOAT` | `-i` | Refresh interval in seconds (default: 2.0) |

**Display:**

Shows two tables that update live:

1. **Jobs Table** - All active jobs (queued + running)
2. **Workers Table** - All registered workers

```
                         Jobs
┌────┬──────────┬─────────┬─────────┬─────────────────┐
│ ID │ Name     │ Status  │ Runtime │ Worker          │
├────┼──────────┼─────────┼─────────┼─────────────────┤
│ 1  │ baseline │ running │ 5m 32s  │ host:gpu0       │
│ 2  │ lora-r8  │ queued  │ -       │ -               │
└────┴──────────┴─────────┴─────────┴─────────────────┘
                       Workers
┌──────────────┬────────┬─────┬───────────┐
│ ID           │ Status │ Job │ Last Seen │
├──────────────┼────────┼─────┼───────────┤
│ host:gpu0    │ busy   │ 1   │ 2s ago    │
│ host:gpu1    │ idle   │ -   │ 5s ago    │
└──────────────┴────────┴─────┴───────────┘
Last updated: 14:32:15 (Ctrl+C to exit)
```

**Examples:**

```bash
# Default 2-second refresh
whirr watch

# Faster refresh (0.5 seconds)
whirr watch -i 0.5
```

**Notes:**
- Press `Ctrl+C` to exit
- Uses Rich library for terminal UI

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

### `whirr dashboard`

Launch an interactive web dashboard for viewing runs and metrics.

```bash
whirr dashboard [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--port INT` | `-p` | Port to serve on (default: 8501) |

**Examples:**

```bash
# Start dashboard on default port
whirr dashboard

# Start on custom port
whirr dashboard --port 8080
```

**Features:**
- View all runs in a filterable table
- Compare metrics across runs side-by-side
- Interactive charts for metrics over time
- View run configuration and summary

**Notes:**
- Requires `streamlit` package (included in `whirr[dashboard]`)
- Opens in your default web browser
- Press `Ctrl+C` to stop the dashboard

---

### `whirr compare`

Compare metrics across multiple runs.

```bash
whirr compare RUN_IDS... [OPTIONS]
```

**Arguments:**

| Argument | Description |
|----------|-------------|
| `RUN_IDS` | Two or more run IDs to compare |

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--metric TEXT` | `-m` | Specific metric to compare (default: all) |
| `--output TEXT` | `-o` | Output format: table, csv, json (default: table) |

**Examples:**

```bash
# Compare two runs
whirr compare job-1 job-2

# Compare specific metric
whirr compare job-1 job-2 job-3 --metric loss

# Export comparison to CSV
whirr compare job-1 job-2 --output csv > comparison.csv
```

---

### `whirr export`

Export run data to various formats.

```bash
whirr export RUN_ID [OPTIONS]
```

**Arguments:**

| Argument | Description |
|----------|-------------|
| `RUN_ID` | Run ID to export |

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--format TEXT` | `-f` | Output format: json, csv, wandb (default: json) |
| `--output PATH` | `-o` | Output file path (default: stdout) |

**Examples:**

```bash
# Export run to JSON
whirr export job-1 --format json > run.json

# Export metrics to CSV
whirr export job-1 --format csv --output metrics.csv
```

---

### `whirr server`

Start the whirr server for multi-machine orchestration.

```bash
whirr server [OPTIONS]
```

**Options:**

| Option | Short | Description |
|--------|-------|-------------|
| `--port INT` | `-p` | Port to listen on (default: 8080) |
| `--host TEXT` | | Host to bind to (default: 127.0.0.1) |
| `--database-url TEXT` | | PostgreSQL connection URL |
| `--data-dir PATH` | `-d` | Data directory for runs |
| `--reload` | | Enable auto-reload for development |

**Examples:**

```bash
# Start server with SQLite (development)
whirr server --port 8080

# Start server with PostgreSQL
whirr server --database-url postgresql://user:pass@localhost:5432/whirr

# Start with Docker Compose (recommended for production)
docker compose up -d
```

**Notes:**
- Requires `whirr[server]` dependencies (FastAPI, uvicorn, psycopg2)
- For production, use with PostgreSQL via Docker Compose
- Workers connect via `--server` flag

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
| `WHIRR_SERVER_URL` | Server URL for remote mode (alternative to `--server` flag) |
| `WHIRR_DATA_DIR` | Data directory for remote workers (alternative to `--data-dir` flag) |

These are automatically set when your script runs via `whirr worker`. You typically don't need to use them directly - `whirr.init()` detects them automatically.
