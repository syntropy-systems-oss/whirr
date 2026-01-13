"""SQLite database layer with WAL mode and atomic operations."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# SQL schema for whirr database
SCHEMA = """
-- Jobs table (scheduling layer)
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    command_argv TEXT NOT NULL,  -- JSON array of argv tokens
    workdir TEXT NOT NULL,       -- Absolute path to run from
    config TEXT,  -- JSON
    status TEXT DEFAULT 'queued',  -- queued, running, completed, failed, cancelled
    tags TEXT,  -- JSON array

    -- Retry tracking
    attempt INTEGER DEFAULT 1,
    parent_job_id INTEGER REFERENCES jobs(id),

    -- Timestamps
    created_at TEXT DEFAULT (datetime('now')),
    started_at TEXT,
    finished_at TEXT,

    -- Worker assignment
    worker_id TEXT,
    heartbeat_at TEXT,

    -- Process tracking (for diagnostics and cancellation)
    pid INTEGER,
    pgid INTEGER,

    -- Result
    exit_code INTEGER,
    error_message TEXT,

    -- Cancellation
    cancel_requested_at TEXT,

    -- Link to run
    run_id TEXT
);

-- Runs table (primary entity - includes both queued and direct runs)
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    job_id INTEGER REFERENCES jobs(id),  -- NULL for direct runs
    name TEXT,
    config TEXT,  -- JSON
    tags TEXT,  -- JSON array

    status TEXT DEFAULT 'running',  -- running, completed, failed
    started_at TEXT DEFAULT (datetime('now')),
    finished_at TEXT,
    duration_seconds REAL,

    summary TEXT,  -- JSON, final metrics from run.summary()

    -- Environment capture
    git_hash TEXT,
    git_dirty INTEGER,
    hostname TEXT,

    -- Path to run folder
    run_dir TEXT
);

-- Workers table (self-registration)
CREATE TABLE IF NOT EXISTS workers (
    id TEXT PRIMARY KEY,
    pid INTEGER,
    hostname TEXT,
    gpu_index INTEGER,
    status TEXT DEFAULT 'idle',  -- idle, busy, offline
    current_job_id INTEGER,
    started_at TEXT DEFAULT (datetime('now')),
    last_heartbeat TEXT
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_heartbeat ON jobs(heartbeat_at);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_job_id ON runs(job_id);
"""


def get_connection(db_path: Path) -> sqlite3.Connection:
    """
    Get a database connection with proper settings for concurrent access.

    - isolation_level=None for explicit transaction control
    - WAL mode for concurrent readers/writers
    - busy_timeout to wait for locks instead of failing immediately
    - Row factory for dict-like access
    """
    conn = sqlite3.connect(str(db_path), timeout=5.0, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path) -> None:
    """Initialize the database with the schema."""
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


def utcnow() -> str:
    """Get current UTC time as ISO format string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Job Operations ---

def create_job(
    conn: sqlite3.Connection,
    command_argv: list[str],
    workdir: str,
    name: Optional[str] = None,
    config: Optional[dict] = None,
    tags: Optional[list[str]] = None,
    parent_job_id: Optional[int] = None,
) -> int:
    """Create a new job and return its ID."""
    cursor = conn.execute(
        """
        INSERT INTO jobs (name, command_argv, workdir, config, tags, parent_job_id, attempt)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            json.dumps(command_argv),
            workdir,
            json.dumps(config) if config else None,
            json.dumps(tags) if tags else None,
            parent_job_id,
            1 if parent_job_id is None else None,
        ),
    )
    return cursor.lastrowid


def claim_job(conn: sqlite3.Connection, worker_id: str) -> Optional[dict]:
    """
    Atomically claim the next queued job for a worker.

    Uses UPDATE...RETURNING with subquery for atomic claim.
    Returns the job dict if claimed, None if no jobs available.
    """
    try:
        conn.execute("BEGIN IMMEDIATE")

        # Atomic update with subquery - claim in a single statement
        now = utcnow()
        cursor = conn.execute(
            """
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
            RETURNING id, name, command_argv, workdir, config, tags, attempt
            """,
            (worker_id, now, now),
        )

        row = cursor.fetchone()
        conn.execute("COMMIT")

        if row is None:
            return None

        return {
            "id": row["id"],
            "name": row["name"],
            "command_argv": json.loads(row["command_argv"]),
            "workdir": row["workdir"],
            "config": json.loads(row["config"]) if row["config"] else None,
            "tags": json.loads(row["tags"]) if row["tags"] else None,
            "attempt": row["attempt"],
        }
    except Exception:
        conn.execute("ROLLBACK")
        raise


def update_job_process_info(
    conn: sqlite3.Connection,
    job_id: int,
    pid: int,
    pgid: int,
) -> None:
    """Store process info for a running job."""
    conn.execute(
        "UPDATE jobs SET pid = ?, pgid = ? WHERE id = ?",
        (pid, pgid, job_id),
    )


def update_job_heartbeat(conn: sqlite3.Connection, job_id: int) -> Optional[str]:
    """
    Update the heartbeat timestamp for a job.

    Returns cancel_requested_at if cancellation was requested, None otherwise.
    """
    now = utcnow()
    conn.execute(
        "UPDATE jobs SET heartbeat_at = ? WHERE id = ?",
        (now, job_id),
    )

    row = conn.execute(
        "SELECT cancel_requested_at FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()

    return row["cancel_requested_at"] if row else None


def complete_job(
    conn: sqlite3.Connection,
    job_id: int,
    exit_code: int,
    run_id: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    """Mark a job as completed or failed based on exit code."""
    status = "completed" if exit_code == 0 else "failed"
    now = utcnow()
    conn.execute(
        """
        UPDATE jobs
        SET status = ?, finished_at = ?, exit_code = ?, run_id = ?, error_message = ?,
            pid = NULL, pgid = NULL
        WHERE id = ?
        """,
        (status, now, exit_code, run_id, error_message, job_id),
    )


def cancel_job(conn: sqlite3.Connection, job_id: int) -> str:
    """
    Cancel a job. Returns the previous status.

    - If queued: marks as cancelled immediately
    - If running: sets cancel_requested_at (worker will handle termination)
    """
    row = conn.execute(
        "SELECT status FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()

    if row is None:
        raise ValueError(f"Job {job_id} not found")

    old_status = row["status"]
    now = utcnow()

    if old_status == "queued":
        conn.execute(
            "UPDATE jobs SET status = 'cancelled', finished_at = ? WHERE id = ?",
            (now, job_id),
        )
    elif old_status == "running":
        conn.execute(
            "UPDATE jobs SET cancel_requested_at = ? WHERE id = ?",
            (now, job_id),
        )

    return old_status


def _deserialize_job(row: sqlite3.Row) -> dict:
    """Deserialize a job row, converting JSON fields back to Python objects."""
    job = dict(row)

    # Deserialize JSON fields
    if job.get("command_argv"):
        job["command_argv"] = json.loads(job["command_argv"])
    if job.get("config"):
        job["config"] = json.loads(job["config"])
    if job.get("tags"):
        job["tags"] = json.loads(job["tags"])

    return job


def get_job(conn: sqlite3.Connection, job_id: int) -> Optional[dict]:
    """Get a job by ID."""
    row = conn.execute(
        "SELECT * FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()

    if row is None:
        return None

    return _deserialize_job(row)


def get_active_jobs(conn: sqlite3.Connection) -> list[dict]:
    """Get all queued and running jobs."""
    rows = conn.execute(
        """
        SELECT * FROM jobs
        WHERE status IN ('queued', 'running')
        ORDER BY created_at
        """
    ).fetchall()

    return [dict(row) for row in rows]


def cancel_all_queued(conn: sqlite3.Connection) -> int:
    """Cancel all queued jobs. Returns count of cancelled jobs."""
    now = utcnow()
    cursor = conn.execute(
        """
        UPDATE jobs
        SET status = 'cancelled', finished_at = ?
        WHERE status = 'queued'
        """,
        (now,),
    )
    return cursor.rowcount


def get_orphaned_jobs(conn: sqlite3.Connection, timeout_seconds: int = 120) -> list[dict]:
    """
    Find running jobs with stale heartbeats (likely orphaned).

    A job is considered orphaned if:
    - Status is 'running'
    - heartbeat_at is older than timeout_seconds ago
    """
    now = datetime.now(timezone.utc)
    cutoff_dt = now - timedelta(seconds=timeout_seconds)
    cutoff = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    rows = conn.execute(
        """
        SELECT * FROM jobs
        WHERE status = 'running'
          AND heartbeat_at IS NOT NULL
          AND heartbeat_at < ?
        """,
        (cutoff,),
    ).fetchall()

    return [dict(row) for row in rows]


def requeue_orphaned_jobs(conn: sqlite3.Connection, timeout_seconds: int = 120) -> list[dict]:
    """
    Find and requeue orphaned jobs.

    Orphaned jobs are running jobs with stale heartbeats.
    They are reset to 'queued' status with incremented attempt counter.

    Returns list of requeued jobs.
    """
    orphaned = get_orphaned_jobs(conn, timeout_seconds)

    for job in orphaned:
        conn.execute(
            """
            UPDATE jobs
            SET status = 'queued',
                worker_id = NULL,
                started_at = NULL,
                heartbeat_at = NULL,
                cancel_requested_at = NULL,
                pid = NULL,
                pgid = NULL,
                attempt = attempt + 1
            WHERE id = ?
            """,
            (job["id"],),
        )

    return orphaned


def retry_job(conn: sqlite3.Connection, job_id: int) -> int:
    """
    Create a new job as a retry of an existing job.

    The new job has:
    - Same command, workdir, name, tags
    - parent_job_id pointing to original
    - attempt = original.attempt + 1

    Returns the new job ID.
    """
    original = get_job(conn, job_id)
    if original is None:
        raise ValueError(f"Job {job_id} not found")

    if original["status"] not in ("failed", "cancelled"):
        raise ValueError(f"Can only retry failed or cancelled jobs, got {original['status']}")

    now = utcnow()
    cursor = conn.execute(
        """
        INSERT INTO jobs (name, command_argv, workdir, config, tags, parent_job_id, attempt, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            original["name"],
            json.dumps(original["command_argv"]),
            original["workdir"],
            json.dumps(original["config"]) if original["config"] else None,
            json.dumps(original["tags"]) if original["tags"] else None,
            job_id,
            original["attempt"] + 1,
            now,
        ),
    )

    return cursor.lastrowid


# --- Run Operations ---

def create_run(
    conn: sqlite3.Connection,
    run_id: str,
    run_dir: str,
    name: Optional[str] = None,
    config: Optional[dict] = None,
    tags: Optional[list[str]] = None,
    job_id: Optional[int] = None,
) -> None:
    """Create a new run record."""
    import socket

    conn.execute(
        """
        INSERT INTO runs (id, job_id, name, config, tags, run_dir, hostname)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            job_id,
            name,
            json.dumps(config) if config else None,
            json.dumps(tags) if tags else None,
            run_dir,
            socket.gethostname(),
        ),
    )


def complete_run(
    conn: sqlite3.Connection,
    run_id: str,
    status: str,
    summary: Optional[dict] = None,
) -> None:
    """Mark a run as completed or failed."""
    row = conn.execute(
        "SELECT started_at FROM runs WHERE id = ?",
        (run_id,),
    ).fetchone()

    now = utcnow()
    duration = None

    if row and row["started_at"]:
        try:
            started = datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))
            finished = datetime.fromisoformat(now.replace("Z", "+00:00"))
            duration = (finished - started).total_seconds()
        except Exception:
            pass

    conn.execute(
        """
        UPDATE runs
        SET status = ?, finished_at = ?, duration_seconds = ?, summary = ?
        WHERE id = ?
        """,
        (status, now, duration, json.dumps(summary) if summary else None, run_id),
    )


def get_run(conn: sqlite3.Connection, run_id: str) -> Optional[dict]:
    """Get a run by ID."""
    row = conn.execute(
        "SELECT * FROM runs WHERE id = ?",
        (run_id,),
    ).fetchone()

    if row is None:
        return None

    return dict(row)


def get_runs(
    conn: sqlite3.Connection,
    status: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Get runs with optional filtering."""
    query = "SELECT * FROM runs WHERE 1=1"
    params: list[Any] = []

    if status:
        query += " AND status = ?"
        params.append(status)

    if tag:
        query += " AND tags LIKE ?"
        params.append(f'%"{tag}"%')

    query += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def get_run_by_job_id(conn: sqlite3.Connection, job_id: int) -> Optional[dict]:
    """Get a run by its associated job ID."""
    row = conn.execute(
        "SELECT * FROM runs WHERE job_id = ?",
        (job_id,),
    ).fetchone()

    if row is None:
        return None

    return dict(row)


# --- Worker Operations ---

def register_worker(
    conn: sqlite3.Connection,
    worker_id: str,
    pid: int,
    hostname: str,
    gpu_index: Optional[int] = None,
) -> None:
    """Register or update a worker."""
    now = utcnow()
    conn.execute(
        """
        INSERT INTO workers (id, pid, hostname, gpu_index, status, started_at, last_heartbeat)
        VALUES (?, ?, ?, ?, 'idle', ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            pid = excluded.pid,
            status = 'idle',
            started_at = excluded.started_at,
            last_heartbeat = excluded.last_heartbeat
        """,
        (worker_id, pid, hostname, gpu_index, now, now),
    )


def update_worker_status(
    conn: sqlite3.Connection,
    worker_id: str,
    status: str,
    current_job_id: Optional[int] = None,
) -> None:
    """Update worker status and current job."""
    now = utcnow()
    conn.execute(
        """
        UPDATE workers
        SET status = ?, current_job_id = ?, last_heartbeat = ?
        WHERE id = ?
        """,
        (status, current_job_id, now, worker_id),
    )


def unregister_worker(conn: sqlite3.Connection, worker_id: str) -> None:
    """Mark worker as offline on graceful shutdown."""
    conn.execute(
        "UPDATE workers SET status = 'offline', current_job_id = NULL WHERE id = ?",
        (worker_id,),
    )


def get_workers(conn: sqlite3.Connection) -> list[dict]:
    """Get all registered workers."""
    rows = conn.execute("SELECT * FROM workers ORDER BY id").fetchall()
    return [dict(row) for row in rows]
