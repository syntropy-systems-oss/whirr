"""Database layer with support for SQLite (local) and PostgreSQL (server mode)."""

import json
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# SQL schema for whirr database (shared between SQLite and Postgres)
SQLITE_SCHEMA = """
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

    -- Lease for server mode (NULL for local mode)
    lease_expires_at TEXT,

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
    gpu_id INTEGER,  -- Alias for gpu_index for clarity
    status TEXT DEFAULT 'idle',  -- idle, busy, offline
    current_job_id INTEGER,
    started_at TEXT DEFAULT (datetime('now')),
    last_heartbeat TEXT,
    heartbeat_at TEXT  -- Alias for last_heartbeat
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_heartbeat ON jobs(heartbeat_at);
CREATE INDEX IF NOT EXISTS idx_jobs_lease ON jobs(lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_job_id ON runs(job_id);
"""

POSTGRES_SCHEMA = """
-- Jobs table (scheduling layer)
CREATE TABLE IF NOT EXISTS jobs (
    id SERIAL PRIMARY KEY,
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
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    started_at TIMESTAMP WITH TIME ZONE,
    finished_at TIMESTAMP WITH TIME ZONE,

    -- Worker assignment
    worker_id TEXT,
    heartbeat_at TIMESTAMP WITH TIME ZONE,

    -- Lease for server mode
    lease_expires_at TIMESTAMP WITH TIME ZONE,

    -- Process tracking (for diagnostics and cancellation)
    pid INTEGER,
    pgid INTEGER,

    -- Result
    exit_code INTEGER,
    error_message TEXT,

    -- Cancellation
    cancel_requested_at TIMESTAMP WITH TIME ZONE,

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
    started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    finished_at TIMESTAMP WITH TIME ZONE,
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
    gpu_id INTEGER,  -- Alias for gpu_index for clarity
    status TEXT DEFAULT 'idle',  -- idle, busy, offline
    current_job_id INTEGER,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_heartbeat TIMESTAMP WITH TIME ZONE,
    heartbeat_at TIMESTAMP WITH TIME ZONE  -- Alias for last_heartbeat
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_heartbeat ON jobs(heartbeat_at);
CREATE INDEX IF NOT EXISTS idx_jobs_lease ON jobs(lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_job_id ON runs(job_id);
"""


def utcnow() -> str:
    """Get current UTC time as ISO format string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utcnow_dt() -> datetime:
    """Get current UTC time as datetime object."""
    return datetime.now(timezone.utc)


class Database(ABC):
    """Abstract base class for database operations."""

    @abstractmethod
    def close(self) -> None:
        """Close database connection."""
        pass

    # --- Job Operations ---

    @abstractmethod
    def create_job(
        self,
        command_argv: list[str],
        workdir: str,
        name: Optional[str] = None,
        config: Optional[dict] = None,
        tags: Optional[list[str]] = None,
        parent_job_id: Optional[int] = None,
    ) -> int:
        """Create a new job and return its ID."""
        pass

    @abstractmethod
    def claim_job(self, worker_id: str, lease_seconds: int = 60) -> Optional[dict]:
        """Atomically claim the next queued job for a worker."""
        pass

    @abstractmethod
    def renew_lease(self, job_id: int, worker_id: str, lease_seconds: int = 60) -> bool:
        """Renew the lease for a job. Returns True if successful."""
        pass

    @abstractmethod
    def update_job_heartbeat(self, job_id: int) -> Optional[str]:
        """Update the heartbeat timestamp. Returns cancel_requested_at if set."""
        pass

    @abstractmethod
    def update_job_process_info(self, job_id: int, pid: int, pgid: int) -> None:
        """Store process info for a running job."""
        pass

    @abstractmethod
    def complete_job(
        self,
        job_id: int,
        exit_code: int,
        run_id: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Mark a job as completed or failed based on exit code."""
        pass

    @abstractmethod
    def cancel_job(self, job_id: int) -> str:
        """Cancel a job. Returns the previous status."""
        pass

    @abstractmethod
    def get_job(self, job_id: int) -> Optional[dict]:
        """Get a job by ID."""
        pass

    @abstractmethod
    def get_active_jobs(self) -> list[dict]:
        """Get all queued and running jobs."""
        pass

    @abstractmethod
    def cancel_all_queued(self) -> int:
        """Cancel all queued jobs. Returns count of cancelled jobs."""
        pass

    @abstractmethod
    def get_expired_leases(self) -> list[dict]:
        """Find running jobs with expired leases."""
        pass

    @abstractmethod
    def requeue_expired_jobs(self) -> list[dict]:
        """Requeue jobs with expired leases."""
        pass

    # --- Run Operations ---

    @abstractmethod
    def create_run(
        self,
        run_id: str,
        run_dir: str,
        name: Optional[str] = None,
        config: Optional[dict] = None,
        tags: Optional[list[str]] = None,
        job_id: Optional[int] = None,
    ) -> None:
        """Create a new run record."""
        pass

    @abstractmethod
    def complete_run(
        self,
        run_id: str,
        status: str,
        summary: Optional[dict] = None,
    ) -> None:
        """Mark a run as completed or failed."""
        pass

    @abstractmethod
    def get_run(self, run_id: str) -> Optional[dict]:
        """Get a run by ID."""
        pass

    @abstractmethod
    def get_runs(
        self,
        status: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Get runs with optional filtering."""
        pass

    @abstractmethod
    def get_run_by_job_id(self, job_id: int) -> Optional[dict]:
        """Get a run by its associated job ID."""
        pass

    # --- Worker Operations ---

    @abstractmethod
    def register_worker(
        self,
        worker_id: str,
        pid: int,
        hostname: str,
        gpu_index: Optional[int] = None,
    ) -> None:
        """Register or update a worker."""
        pass

    @abstractmethod
    def update_worker_status(
        self,
        worker_id: str,
        status: str,
        current_job_id: Optional[int] = None,
    ) -> None:
        """Update worker status and current job."""
        pass

    @abstractmethod
    def unregister_worker(self, worker_id: str) -> None:
        """Mark worker as offline on graceful shutdown."""
        pass

    @abstractmethod
    def get_workers(self) -> list[dict]:
        """Get all registered workers."""
        pass


class SQLiteDatabase(Database):
    """SQLite database implementation for local mode."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(
            str(db_path),
            timeout=5.0,
            isolation_level=None,
            check_same_thread=False,  # Allow use across threads (for FastAPI)
        )
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        """Initialize database schema."""
        self.conn.executescript(SQLITE_SCHEMA)

    def _deserialize_job(self, row: sqlite3.Row) -> dict:
        """Deserialize a job row."""
        job = dict(row)
        if job.get("command_argv"):
            job["command_argv"] = json.loads(job["command_argv"])
        if job.get("config"):
            job["config"] = json.loads(job["config"])
        if job.get("tags"):
            job["tags"] = json.loads(job["tags"])
        return job

    # --- Job Operations ---

    def create_job(
        self,
        command_argv: list[str],
        workdir: str,
        name: Optional[str] = None,
        config: Optional[dict] = None,
        tags: Optional[list[str]] = None,
        parent_job_id: Optional[int] = None,
    ) -> int:
        cursor = self.conn.execute(
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
        assert cursor.lastrowid is not None  # INSERT always sets lastrowid
        return cursor.lastrowid

    def claim_job(self, worker_id: str, lease_seconds: int = 60) -> Optional[dict]:
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            now = utcnow()
            cursor = self.conn.execute(
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
            self.conn.execute("COMMIT")

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
            self.conn.execute("ROLLBACK")
            raise

    def renew_lease(self, job_id: int, worker_id: str, lease_seconds: int = 60) -> bool:
        """For SQLite, this just updates heartbeat (no real lease)."""
        now = utcnow()
        cursor = self.conn.execute(
            "UPDATE jobs SET heartbeat_at = ? WHERE id = ? AND worker_id = ?",
            (now, job_id, worker_id),
        )
        return cursor.rowcount > 0

    def update_job_heartbeat(self, job_id: int) -> Optional[str]:
        now = utcnow()
        self.conn.execute(
            "UPDATE jobs SET heartbeat_at = ? WHERE id = ?",
            (now, job_id),
        )
        row = self.conn.execute(
            "SELECT cancel_requested_at FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        return row["cancel_requested_at"] if row else None

    def update_job_process_info(self, job_id: int, pid: int, pgid: int) -> None:
        self.conn.execute(
            "UPDATE jobs SET pid = ?, pgid = ? WHERE id = ?",
            (pid, pgid, job_id),
        )

    def complete_job(
        self,
        job_id: int,
        exit_code: int,
        run_id: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        status = "completed" if exit_code == 0 else "failed"
        now = utcnow()
        self.conn.execute(
            """
            UPDATE jobs
            SET status = ?, finished_at = ?, exit_code = ?, run_id = ?, error_message = ?,
                pid = NULL, pgid = NULL
            WHERE id = ?
            """,
            (status, now, exit_code, run_id, error_message, job_id),
        )

    def cancel_job(self, job_id: int) -> str:
        row = self.conn.execute(
            "SELECT status FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()

        if row is None:
            raise ValueError(f"Job {job_id} not found")

        old_status = row["status"]
        now = utcnow()

        if old_status == "queued":
            self.conn.execute(
                "UPDATE jobs SET status = 'cancelled', finished_at = ? WHERE id = ?",
                (now, job_id),
            )
        elif old_status == "running":
            self.conn.execute(
                "UPDATE jobs SET cancel_requested_at = ? WHERE id = ?",
                (now, job_id),
            )

        return old_status

    def get_job(self, job_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return self._deserialize_job(row)

    def get_active_jobs(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT * FROM jobs
            WHERE status IN ('queued', 'running')
            ORDER BY created_at
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def cancel_all_queued(self) -> int:
        now = utcnow()
        cursor = self.conn.execute(
            """
            UPDATE jobs
            SET status = 'cancelled', finished_at = ?
            WHERE status = 'queued'
            """,
            (now,),
        )
        return cursor.rowcount

    def get_expired_leases(self) -> list[dict]:
        """For SQLite, check heartbeat timeout instead of lease."""
        now = datetime.now(timezone.utc)
        cutoff_dt = now - timedelta(seconds=120)
        cutoff = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        rows = self.conn.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'running'
              AND heartbeat_at IS NOT NULL
              AND heartbeat_at < ?
            """,
            (cutoff,),
        ).fetchall()
        return [dict(row) for row in rows]

    def requeue_expired_jobs(self) -> list[dict]:
        expired = self.get_expired_leases()
        for job in expired:
            self.conn.execute(
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
        return expired

    # --- Run Operations ---

    def create_run(
        self,
        run_id: str,
        run_dir: str,
        name: Optional[str] = None,
        config: Optional[dict] = None,
        tags: Optional[list[str]] = None,
        job_id: Optional[int] = None,
    ) -> None:
        import socket

        self.conn.execute(
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
        self,
        run_id: str,
        status: str,
        summary: Optional[dict] = None,
    ) -> None:
        row = self.conn.execute(
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

        self.conn.execute(
            """
            UPDATE runs
            SET status = ?, finished_at = ?, duration_seconds = ?, summary = ?
            WHERE id = ?
            """,
            (status, now, duration, json.dumps(summary) if summary else None, run_id),
        )

    def get_run(self, run_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def get_runs(
        self,
        status: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
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

        rows = self.conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_run_by_job_id(self, job_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM runs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    # --- Worker Operations ---

    def register_worker(
        self,
        worker_id: str,
        pid: int,
        hostname: str,
        gpu_index: Optional[int] = None,
    ) -> None:
        now = utcnow()
        self.conn.execute(
            """
            INSERT INTO workers (id, pid, hostname, gpu_index, gpu_id, status, started_at, last_heartbeat, heartbeat_at)
            VALUES (?, ?, ?, ?, ?, 'idle', ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                pid = excluded.pid,
                status = 'idle',
                started_at = excluded.started_at,
                last_heartbeat = excluded.last_heartbeat,
                heartbeat_at = excluded.heartbeat_at
            """,
            (worker_id, pid, hostname, gpu_index, gpu_index, now, now, now),
        )

    def update_worker_status(
        self,
        worker_id: str,
        status: str,
        current_job_id: Optional[int] = None,
    ) -> None:
        now = utcnow()
        self.conn.execute(
            """
            UPDATE workers
            SET status = ?, current_job_id = ?, last_heartbeat = ?, heartbeat_at = ?
            WHERE id = ?
            """,
            (status, current_job_id, now, now, worker_id),
        )

    def unregister_worker(self, worker_id: str) -> None:
        self.conn.execute(
            "UPDATE workers SET status = 'offline', current_job_id = NULL WHERE id = ?",
            (worker_id,),
        )

    def get_workers(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM workers ORDER BY id").fetchall()
        return [dict(row) for row in rows]


class PostgresDatabase(Database):
    """PostgreSQL database implementation for server mode."""

    def __init__(self, connection_url: str):
        try:
            import psycopg2
            import psycopg2.extras
        except ImportError:
            raise ImportError(
                "psycopg2 is required for PostgreSQL support. "
                "Install with: pip install whirr[server]"
            )

        self.conn = psycopg2.connect(connection_url)
        self.conn.autocommit = False
        # Use RealDictCursor for dict-like row access
        self._cursor_factory = psycopg2.extras.RealDictCursor

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        """Initialize database schema."""
        with self.conn.cursor() as cur:
            cur.execute(POSTGRES_SCHEMA)
        self.conn.commit()

    def _execute(self, query: str, params: tuple = ()) -> Any:
        """Execute a query and return cursor."""
        cur = self.conn.cursor(cursor_factory=self._cursor_factory)
        cur.execute(query, params)
        return cur

    # --- Job Operations ---

    def create_job(
        self,
        command_argv: list[str],
        workdir: str,
        name: Optional[str] = None,
        config: Optional[dict] = None,
        tags: Optional[list[str]] = None,
        parent_job_id: Optional[int] = None,
    ) -> int:
        cur = self._execute(
            """
            INSERT INTO jobs (name, command_argv, workdir, config, tags, parent_job_id, attempt)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
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
        job_id = cur.fetchone()["id"]
        self.conn.commit()
        return job_id

    def claim_job(self, worker_id: str, lease_seconds: int = 60) -> Optional[dict]:
        """Atomically claim a job using FOR UPDATE SKIP LOCKED."""
        try:
            cur = self._execute(
                """
                UPDATE jobs
                SET status = 'running',
                    worker_id = %s,
                    started_at = NOW(),
                    heartbeat_at = NOW(),
                    lease_expires_at = NOW() + INTERVAL '%s seconds'
                WHERE id = (
                    SELECT id FROM jobs
                    WHERE status = 'queued'
                    ORDER BY created_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING id, name, command_argv, workdir, config, tags, attempt
                """,
                (worker_id, lease_seconds),
            )
            row = cur.fetchone()
            self.conn.commit()

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
            self.conn.rollback()
            raise

    def renew_lease(self, job_id: int, worker_id: str, lease_seconds: int = 60) -> bool:
        """Renew the lease for a job."""
        cur = self._execute(
            """
            UPDATE jobs
            SET heartbeat_at = NOW(),
                lease_expires_at = NOW() + INTERVAL '%s seconds'
            WHERE id = %s AND worker_id = %s AND status = 'running'
            """,
            (lease_seconds, job_id, worker_id),
        )
        affected = cur.rowcount
        self.conn.commit()
        return affected > 0

    def update_job_heartbeat(self, job_id: int) -> Optional[str]:
        cur = self._execute(
            """
            UPDATE jobs SET heartbeat_at = NOW() WHERE id = %s
            RETURNING cancel_requested_at
            """,
            (job_id,),
        )
        row = cur.fetchone()
        self.conn.commit()
        if row and row["cancel_requested_at"]:
            return row["cancel_requested_at"].isoformat()
        return None

    def update_job_process_info(self, job_id: int, pid: int, pgid: int) -> None:
        self._execute(
            "UPDATE jobs SET pid = %s, pgid = %s WHERE id = %s",
            (pid, pgid, job_id),
        )
        self.conn.commit()

    def complete_job(
        self,
        job_id: int,
        exit_code: int,
        run_id: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        status = "completed" if exit_code == 0 else "failed"
        self._execute(
            """
            UPDATE jobs
            SET status = %s, finished_at = NOW(), exit_code = %s, run_id = %s, error_message = %s,
                pid = NULL, pgid = NULL, lease_expires_at = NULL
            WHERE id = %s
            """,
            (status, exit_code, run_id, error_message, job_id),
        )
        self.conn.commit()

    def cancel_job(self, job_id: int) -> str:
        cur = self._execute(
            "SELECT status FROM jobs WHERE id = %s",
            (job_id,),
        )
        row = cur.fetchone()

        if row is None:
            self.conn.rollback()
            raise ValueError(f"Job {job_id} not found")

        old_status = row["status"]

        if old_status == "queued":
            self._execute(
                "UPDATE jobs SET status = 'cancelled', finished_at = NOW() WHERE id = %s",
                (job_id,),
            )
        elif old_status == "running":
            self._execute(
                "UPDATE jobs SET cancel_requested_at = NOW() WHERE id = %s",
                (job_id,),
            )

        self.conn.commit()
        return old_status

    def get_job(self, job_id: int) -> Optional[dict]:
        cur = self._execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
        if row is None:
            return None
        job = dict(row)
        if job.get("command_argv"):
            job["command_argv"] = json.loads(job["command_argv"])
        if job.get("config"):
            job["config"] = json.loads(job["config"])
        if job.get("tags"):
            job["tags"] = json.loads(job["tags"])
        return job

    def get_active_jobs(self) -> list[dict]:
        cur = self._execute(
            """
            SELECT * FROM jobs
            WHERE status IN ('queued', 'running')
            ORDER BY created_at
            """
        )
        return [dict(row) for row in cur.fetchall()]

    def cancel_all_queued(self) -> int:
        cur = self._execute(
            """
            UPDATE jobs
            SET status = 'cancelled', finished_at = NOW()
            WHERE status = 'queued'
            """
        )
        count = cur.rowcount
        self.conn.commit()
        return count

    def get_expired_leases(self) -> list[dict]:
        """Find running jobs with expired leases."""
        cur = self._execute(
            """
            SELECT * FROM jobs
            WHERE status = 'running'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at < NOW()
            """
        )
        return [dict(row) for row in cur.fetchall()]

    def requeue_expired_jobs(self) -> list[dict]:
        """Requeue jobs with expired leases."""
        expired = self.get_expired_leases()
        for job in expired:
            self._execute(
                """
                UPDATE jobs
                SET status = 'queued',
                    worker_id = NULL,
                    started_at = NULL,
                    heartbeat_at = NULL,
                    lease_expires_at = NULL,
                    cancel_requested_at = NULL,
                    pid = NULL,
                    pgid = NULL,
                    attempt = attempt + 1
                WHERE id = %s
                """,
                (job["id"],),
            )
        self.conn.commit()
        return expired

    # --- Run Operations ---

    def create_run(
        self,
        run_id: str,
        run_dir: str,
        name: Optional[str] = None,
        config: Optional[dict] = None,
        tags: Optional[list[str]] = None,
        job_id: Optional[int] = None,
    ) -> None:
        import socket

        self._execute(
            """
            INSERT INTO runs (id, job_id, name, config, tags, run_dir, hostname)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
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
        self.conn.commit()

    def complete_run(
        self,
        run_id: str,
        status: str,
        summary: Optional[dict] = None,
    ) -> None:
        cur = self._execute(
            "SELECT started_at FROM runs WHERE id = %s",
            (run_id,),
        )
        row = cur.fetchone()

        duration = None
        if row and row["started_at"]:
            try:
                started = row["started_at"]
                finished = datetime.now(timezone.utc)
                duration = (finished - started).total_seconds()
            except Exception:
                pass

        self._execute(
            """
            UPDATE runs
            SET status = %s, finished_at = NOW(), duration_seconds = %s, summary = %s
            WHERE id = %s
            """,
            (status, duration, json.dumps(summary) if summary else None, run_id),
        )
        self.conn.commit()

    def get_run(self, run_id: str) -> Optional[dict]:
        cur = self._execute("SELECT * FROM runs WHERE id = %s", (run_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return dict(row)

    def get_runs(
        self,
        status: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        query = "SELECT * FROM runs WHERE 1=1"
        params: list[Any] = []

        if status:
            query += " AND status = %s"
            params.append(status)

        if tag:
            query += " AND tags LIKE %s"
            params.append(f'%"{tag}"%')

        query += " ORDER BY started_at DESC LIMIT %s"
        params.append(limit)

        cur = self._execute(query, tuple(params))
        return [dict(row) for row in cur.fetchall()]

    def get_run_by_job_id(self, job_id: int) -> Optional[dict]:
        cur = self._execute("SELECT * FROM runs WHERE job_id = %s", (job_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return dict(row)

    # --- Worker Operations ---

    def register_worker(
        self,
        worker_id: str,
        pid: int,
        hostname: str,
        gpu_index: Optional[int] = None,
    ) -> None:
        self._execute(
            """
            INSERT INTO workers (id, pid, hostname, gpu_index, gpu_id, status, started_at, last_heartbeat, heartbeat_at)
            VALUES (%s, %s, %s, %s, %s, 'idle', NOW(), NOW(), NOW())
            ON CONFLICT(id) DO UPDATE SET
                pid = EXCLUDED.pid,
                status = 'idle',
                started_at = NOW(),
                last_heartbeat = NOW(),
                heartbeat_at = NOW()
            """,
            (worker_id, pid, hostname, gpu_index, gpu_index),
        )
        self.conn.commit()

    def update_worker_status(
        self,
        worker_id: str,
        status: str,
        current_job_id: Optional[int] = None,
    ) -> None:
        self._execute(
            """
            UPDATE workers
            SET status = %s, current_job_id = %s, last_heartbeat = NOW(), heartbeat_at = NOW()
            WHERE id = %s
            """,
            (status, current_job_id, worker_id),
        )
        self.conn.commit()

    def unregister_worker(self, worker_id: str) -> None:
        self._execute(
            "UPDATE workers SET status = 'offline', current_job_id = NULL WHERE id = %s",
            (worker_id,),
        )
        self.conn.commit()

    def get_workers(self) -> list[dict]:
        cur = self._execute("SELECT * FROM workers ORDER BY id")
        return [dict(row) for row in cur.fetchall()]


# --- Factory function ---

def get_database(db_path: Optional[Path] = None, connection_url: Optional[str] = None) -> Database:
    """
    Get a database instance.

    Args:
        db_path: Path to SQLite database (for local mode)
        connection_url: PostgreSQL connection URL (for server mode)

    Returns:
        Database instance (SQLiteDatabase or PostgresDatabase)
    """
    if connection_url:
        return PostgresDatabase(connection_url)
    elif db_path:
        return SQLiteDatabase(db_path)
    else:
        raise ValueError("Either db_path or connection_url must be provided")


# --- Backward compatibility layer ---
# These functions maintain compatibility with existing code that uses the old API

# Keep old schema name for compatibility
SCHEMA = SQLITE_SCHEMA


def get_connection(db_path: Path) -> sqlite3.Connection:
    """
    Get a database connection with proper settings for concurrent access.
    DEPRECATED: Use SQLiteDatabase class instead.
    """
    conn = sqlite3.connect(str(db_path), timeout=5.0, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path) -> None:
    """Initialize the database with the schema."""
    db = SQLiteDatabase(db_path)
    try:
        db.init_schema()
    finally:
        db.close()


# Legacy function wrappers for backward compatibility
def create_job(
    conn: sqlite3.Connection,
    command_argv: list[str],
    workdir: str,
    name: Optional[str] = None,
    config: Optional[dict] = None,
    tags: Optional[list[str]] = None,
    parent_job_id: Optional[int] = None,
) -> int:
    """Create a new job. DEPRECATED: Use Database.create_job() instead."""
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
    assert cursor.lastrowid is not None  # INSERT always sets lastrowid
    return cursor.lastrowid


def claim_job(conn: sqlite3.Connection, worker_id: str) -> Optional[dict]:
    """Atomically claim the next queued job. DEPRECATED: Use Database.claim_job() instead."""
    try:
        conn.execute("BEGIN IMMEDIATE")
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


def update_job_process_info(conn: sqlite3.Connection, job_id: int, pid: int, pgid: int) -> None:
    """Store process info. DEPRECATED: Use Database.update_job_process_info() instead."""
    conn.execute("UPDATE jobs SET pid = ?, pgid = ? WHERE id = ?", (pid, pgid, job_id))


def update_job_heartbeat(conn: sqlite3.Connection, job_id: int) -> Optional[str]:
    """Update heartbeat. DEPRECATED: Use Database.update_job_heartbeat() instead."""
    now = utcnow()
    conn.execute("UPDATE jobs SET heartbeat_at = ? WHERE id = ?", (now, job_id))
    row = conn.execute(
        "SELECT cancel_requested_at FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    return row["cancel_requested_at"] if row else None


def complete_job(
    conn: sqlite3.Connection,
    job_id: int,
    exit_code: int,
    run_id: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    """Mark job complete. DEPRECATED: Use Database.complete_job() instead."""
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
    """Cancel a job. DEPRECATED: Use Database.cancel_job() instead."""
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
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
    """Deserialize a job row."""
    job = dict(row)
    if job.get("command_argv"):
        job["command_argv"] = json.loads(job["command_argv"])
    if job.get("config"):
        job["config"] = json.loads(job["config"])
    if job.get("tags"):
        job["tags"] = json.loads(job["tags"])
    return job


def get_job(conn: sqlite3.Connection, job_id: int) -> Optional[dict]:
    """Get a job by ID. DEPRECATED: Use Database.get_job() instead."""
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    return _deserialize_job(row)


def get_active_jobs(conn: sqlite3.Connection) -> list[dict]:
    """Get active jobs. DEPRECATED: Use Database.get_active_jobs() instead."""
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status IN ('queued', 'running') ORDER BY created_at"
    ).fetchall()
    return [dict(row) for row in rows]


def cancel_all_queued(conn: sqlite3.Connection) -> int:
    """Cancel all queued jobs. DEPRECATED: Use Database.cancel_all_queued() instead."""
    now = utcnow()
    cursor = conn.execute(
        "UPDATE jobs SET status = 'cancelled', finished_at = ? WHERE status = 'queued'",
        (now,),
    )
    return cursor.rowcount


def get_orphaned_jobs(conn: sqlite3.Connection, timeout_seconds: int = 120) -> list[dict]:
    """Find orphaned jobs. DEPRECATED: Use Database.get_expired_leases() instead."""
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
    """Requeue orphaned jobs. DEPRECATED: Use Database.requeue_expired_jobs() instead."""
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
    """Retry a job. DEPRECATED."""
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
    assert cursor.lastrowid is not None  # INSERT always sets lastrowid
    return cursor.lastrowid


# Run operations (legacy)
def create_run(
    conn: sqlite3.Connection,
    run_id: str,
    run_dir: str,
    name: Optional[str] = None,
    config: Optional[dict] = None,
    tags: Optional[list[str]] = None,
    job_id: Optional[int] = None,
) -> None:
    """Create a run. DEPRECATED: Use Database.create_run() instead."""
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
    """Complete a run. DEPRECATED: Use Database.complete_run() instead."""
    row = conn.execute("SELECT started_at FROM runs WHERE id = ?", (run_id,)).fetchone()
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
    """Get a run. DEPRECATED: Use Database.get_run() instead."""
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    return dict(row)


def get_runs(
    conn: sqlite3.Connection,
    status: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Get runs. DEPRECATED: Use Database.get_runs() instead."""
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
    """Get run by job ID. DEPRECATED: Use Database.get_run_by_job_id() instead."""
    row = conn.execute("SELECT * FROM runs WHERE job_id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    return dict(row)


# Worker operations (legacy)
def register_worker(
    conn: sqlite3.Connection,
    worker_id: str,
    pid: int,
    hostname: str,
    gpu_index: Optional[int] = None,
) -> None:
    """Register worker. DEPRECATED: Use Database.register_worker() instead."""
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
    """Update worker status. DEPRECATED: Use Database.update_worker_status() instead."""
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
    """Unregister worker. DEPRECATED: Use Database.unregister_worker() instead."""
    conn.execute(
        "UPDATE workers SET status = 'offline', current_job_id = NULL WHERE id = ?",
        (worker_id,),
    )


def get_workers(conn: sqlite3.Connection) -> list[dict]:
    """Get workers. DEPRECATED: Use Database.get_workers() instead."""
    rows = conn.execute("SELECT * FROM workers ORDER BY id").fetchall()
    return [dict(row) for row in rows]
