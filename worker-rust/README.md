# whirr-worker (Rust)

Lightweight worker binary for whirr GPU job orchestration.

## Why Rust?

- **Minimal footprint**: ~5MB binary, ~10MB runtime memory
- **No runtime dependencies**: Single static binary, no Python/Node/etc.
- **Zero GC overhead**: No garbage collector pauses
- **Easy deployment**: `scp whirr-worker node:/usr/local/bin/`

## Building

```bash
# Debug build
make build

# Release build (optimized for size)
make release

# Cross-compile for Linux (for GPU nodes)
make linux-musl
```

## Usage

```bash
# Basic usage
whirr-worker --server http://head-node:8080 --data-dir /mnt/shared/whirr

# With GPU assignment
whirr-worker -s http://head:8080 -d /data/whirr --gpu 0

# With custom intervals
whirr-worker -s http://head:8080 -d /data \
  --poll-interval 5 \
  --heartbeat-interval 30 \
  --lease-seconds 60
```

## Options

| Option | Short | Env Var | Description |
|--------|-------|---------|-------------|
| `--server` | `-s` | `WHIRR_SERVER_URL` | Server URL |
| `--data-dir` | `-d` | `WHIRR_DATA_DIR` | Shared data directory |
| `--gpu` | `-g` | - | GPU index (sets CUDA_VISIBLE_DEVICES) |
| `--poll-interval` | - | - | Seconds between job polls (default: 5) |
| `--heartbeat-interval` | - | - | Seconds between heartbeats (default: 30) |
| `--lease-seconds` | - | - | Job lease duration (default: 60) |

## Environment Variables Set for Jobs

When a job runs, these environment variables are set:

- `WHIRR_JOB_ID`: Job ID number
- `WHIRR_RUN_DIR`: Path to run directory
- `WHIRR_RUN_ID`: Run ID string (e.g., "job-42")
- `CUDA_VISIBLE_DEVICES`: Set if `--gpu` specified

## Deployment

1. Build for your target platform:
   ```bash
   # For Linux x86_64 (most GPU servers)
   make linux-musl
   ```

2. Copy to GPU nodes:
   ```bash
   scp target/x86_64-unknown-linux-musl/release/whirr-worker gpu-node:/usr/local/bin/
   ```

3. Run as a service or in tmux:
   ```bash
   whirr-worker -s http://head:8080 -d /mnt/shared/whirr --gpu 0
   ```

## Memory Usage

Typical memory footprint:

| State | Memory |
|-------|--------|
| Idle (polling) | ~5 MB |
| Running job | ~8 MB |
| Peak | ~12 MB |

Compare to Python worker: 100-200 MB
