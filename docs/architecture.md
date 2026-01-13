# Architecture Overview

How whirr works under the hood.

## Design Philosophy

**North Star:** "Optimize for 'I can trust it overnight' before 'it has features.'"

whirr prioritizes reliability over features. An ML experiment that fails silently or produces orphaned processes is worse than one that never ran.

## Core Components

```
┌─────────────────────────────────────────────────────────────────┐
│                         User's Machine                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌─────────┐     ┌─────────────┐     ┌──────────────────────┐ │
│   │  CLI    │────▶│   SQLite    │◀────│      Worker(s)       │ │
│   │ (submit)│     │  (whirr.db) │     │  (claim & execute)   │ │
│   └─────────┘     └─────────────┘     └──────────────────────┘ │
│                          │                      │               │
│                          ▼                      ▼               │
│                   ┌─────────────┐      ┌───────────────┐       │
│                   │    Runs     │      │  JobRunner    │       │
│                   │  (index)    │      │ (subprocess)  │       │
│                   └─────────────┘      └───────────────┘       │
│                                               │                 │
│                                               ▼                 │
│                                        ┌────────────┐          │
│                                        │ .whirr/runs│          │
│                                        │  (files)   │          │
│                                        └────────────┘          │
└─────────────────────────────────────────────────────────────────┘
```

## Data Model

### Two Execution Modes

whirr supports two ways to track experiments:

1. **Queued runs** - Submit via CLI, execute via worker
2. **Direct runs** - Run script directly with `whirr.init()`

Both produce the same output structure. The difference is scheduling.

```
┌──────────────────┐         ┌──────────────────┐
│   whirr submit   │         │  python train.py │
│   (creates job)  │         │  (direct run)    │
└────────┬─────────┘         └────────┬─────────┘
         │                            │
         ▼                            │
┌──────────────────┐                  │
│  whirr worker    │                  │
│  (runs job)      │                  │
└────────┬─────────┘                  │
         │                            │
         ▼                            ▼
┌─────────────────────────────────────────────┐
│              whirr.init()                   │
│         (creates Run instance)              │
└────────────────────┬────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────┐
│           .whirr/runs/{run_id}/             │
│   meta.json, config.json, metrics.jsonl    │
└─────────────────────────────────────────────┘
```

### Jobs vs Runs

| Concept | Purpose | Storage |
|---------|---------|---------|
| **Job** | Scheduling unit (queue position, worker assignment) | `jobs` table in SQLite |
| **Run** | Experiment record (metrics, config, results) | `runs` table + filesystem |

A job creates a run when it executes. Direct runs have no associated job.

### Run ID Format

| Source | Format | Example |
|--------|--------|---------|
| Queued (via job) | `job-{id}` | `job-1`, `job-42` |
| Direct (no queue) | `local-{timestamp}-{random}` | `local-20240115-103045-a1b2` |

## SQLite Concurrency

### Why SQLite?

- Zero deployment complexity
- ACID transactions
- Good enough for local workloads
- WAL mode handles concurrent readers/writers

### Configuration

```python
conn = sqlite3.connect('whirr.db', timeout=5.0)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA busy_timeout=5000')
```

- **WAL mode**: Allows concurrent reads during writes
- **busy_timeout**: Wait up to 5 seconds if database is locked

### Atomic Job Claiming

Multiple workers can safely compete for jobs:

```sql
-- Worker claims next job atomically
BEGIN IMMEDIATE;  -- Acquire write lock immediately

UPDATE jobs
SET status = 'running',
    worker_id = ?,
    started_at = ?,
    heartbeat_at = ?
WHERE id = (
    SELECT id FROM jobs
    WHERE status = 'queued'
    ORDER BY created_at, id
    LIMIT 1
)
RETURNING id, name, command_argv, workdir, ...;

COMMIT;
```

The `BEGIN IMMEDIATE` + `UPDATE...RETURNING` pattern ensures:
- Only one worker gets each job
- No race conditions
- No duplicate execution

## Process Management

### The Overnight Killer Bug

When a worker crashes, what happens to its child process (your training script)?

**Bad scenario:**
1. Worker crashes
2. Training script keeps running (orphaned)
3. Job gets requeued (heartbeat timeout)
4. New worker starts same job
5. Now two instances running → GPU conflict, corrupted data

### Solution: Process Groups + PDEATHSIG

```python
def start(self):
    self._process = subprocess.Popen(
        self._command_argv,
        cwd=self._workdir,
        stdout=self._log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,  # New process group
        preexec_fn=self._setup_child,
    )

def _setup_child(self):
    # On Linux: kill child if parent dies
    if sys.platform == "linux":
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        PR_SET_PDEATHSIG = 1
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL)
```

**How it works:**

1. `start_new_session=True` creates a new process group
2. `PDEATHSIG` (Linux) tells kernel: "if my parent dies, send me SIGKILL"
3. On cancel/shutdown, we kill the entire process group

**Result:** Worker death = child death. No orphans.

### Graceful Shutdown

```
User presses Ctrl+C
        │
        ▼
┌───────────────────┐
│ SIGINT received   │
│ Set shutdown flag │
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│ Finish current    │
│ job gracefully    │
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│ Exit worker loop  │
└───────────────────┘
```

Second Ctrl+C forces immediate exit (kills current job).

## Heartbeat System

Workers send heartbeats to prove they're alive:

```
Worker                          Database
   │                              │
   │ ──── claim job ────────────► │
   │ ◄─── job assigned ────────── │
   │                              │
   │ ──── heartbeat (30s) ──────► │  (updates heartbeat_at)
   │ ◄─── cancel_requested? ───── │  (returns if cancellation pending)
   │                              │
   │ ──── heartbeat (30s) ──────► │
   │                              │
   │     (worker dies)            │
   │                              │
   │                              │  (no heartbeat for 2+ min)
   │                              │  → can detect orphaned jobs
```

### Cancellation Flow

1. User runs `whirr cancel 1`
2. Database sets `cancel_requested_at` timestamp
3. Worker's heartbeat sees this flag
4. Worker kills the process group
5. Job marked as cancelled

### Orphan Detection & Recovery

When a worker crashes without finishing a job, the heartbeat stops:

```
Timeline:
┌─────────────────────────────────────────────────────────────┐
│  0s      30s      60s      90s      120s     150s          │
│  │        │        │        │        │        │            │
│  ▼        ▼        ▼        ▼        ▼        ▼            │
│  💚       💚       💀       ❌       ❌       🔄           │
│  start  heartbeat crash   stale   orphaned  requeued      │
└─────────────────────────────────────────────────────────────┘
```

**Detection:** Jobs with `status='running'` and `heartbeat_at` older than 2 minutes are considered orphaned.

**Recovery:** When a worker starts, it automatically:
1. Scans for orphaned jobs
2. Requeues them (sets `status='queued'`, clears `worker_id`)
3. Increments `attempt` counter to track retries

```python
# Worker startup
orphaned = requeue_orphaned_jobs(conn, timeout_seconds=120)
for job in orphaned:
    console.print(f"Requeued orphaned job #{job['id']}")
```

This ensures jobs stuck by worker crashes get automatically retried.

## File Storage

### Directory Structure

```
.whirr/
├── whirr.db              # SQLite database
├── config.yaml           # Project config
└── runs/
    └── {run_id}/
        ├── meta.json     # Run metadata
        ├── config.json   # Hyperparameters
        ├── metrics.jsonl # Time series metrics (user logged)
        ├── system.jsonl  # System metrics (CPU, GPU, memory)
        ├── output.log    # stdout/stderr
        └── artifacts/    # Saved files
```

### Why JSONL for Metrics?

- **Append-only**: No rewriting, no corruption risk
- **Crash-safe**: Partial final line is tolerated
- **Streamable**: Can read while writing
- **Human-readable**: Easy to debug

```jsonl
{"_idx": 0, "_timestamp": "2024-01-15T10:30:00Z", "step": 0, "loss": 2.34}
{"_idx": 1, "_timestamp": "2024-01-15T10:30:15Z", "step": 1, "loss": 1.89}
```

### Database as Index

The SQLite database is an **index**, not the source of truth. The filesystem is:

- Runs can exist without database entries (direct runs)
- Database can be rebuilt by scanning `runs/` directory
- Metrics are never stored in SQLite (only in JSONL files)

## Security Model

### Current (v0.1)

- Single-machine only
- No authentication
- File permissions are the security boundary

### Future (v0.4+)

- Server mode with HTTP API
- Shared token authentication
- Lease-based job claims

## Extension Points

### Where to Add Features

| Feature | Where |
|---------|-------|
| New CLI command | `src/whirr/cli/` + register in `main.py` |
| New metric type | Extend `Run.log()` in `run.py` |
| System metrics | Add `system_metrics.py` (v0.2) |
| Dashboard | Add `dashboard/` with FastAPI + React (v0.3) |

### Database Migrations

Currently no migration system. For schema changes:

1. Add new columns with defaults
2. Handle missing columns gracefully in code
3. Full migrations planned for v0.4

## Testing Strategy

| Layer | Test Type | Location |
|-------|-----------|----------|
| Database ops | Unit | `test_db.py` |
| Run class | Unit | `test_run.py` |
| CLI commands | Integration | `test_cli.py` |
| Concurrency | Stress | `test_concurrency.py` |
| End-to-end | Integration | `test_integration.py` |

Key invariants tested:
- Jobs are never claimed twice
- Cancellation kills processes
- Metrics survive crashes (partial line tolerance)
- Workers don't leave orphans
