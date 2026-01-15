# Copyright (c) Syntropy Systems
"""Database layer with support for SQLite (local) and PostgreSQL (server mode)."""

from __future__ import annotations

import json
import socket
import sqlite3
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional, Protocol, cast

from typing_extensions import Self, override

from whirr.models.base import JSONValue
from whirr.models.db import JobRecord, RunRecord, WorkerRecord

if TYPE_CHECKING:
    from pathlib import Path

JSONDict = dict[str, JSONValue]
RowData = Mapping[str, object]


def _row_to_dict(row: sqlite3.Row | RowData) -> dict[str, object]:
    return dict(row)


def _fetchone(cursor: sqlite3.Cursor) -> sqlite3.Row | None:
    return cast("Optional[sqlite3.Row]", cursor.fetchone())


def _fetchall(cursor: sqlite3.Cursor) -> list[sqlite3.Row]:
    return cast("list[sqlite3.Row]", cursor.fetchall())


class PostgresCursor(Protocol):
    """Protocol for cursor implementations used by PostgreSQL connections."""

    rowcount: int

    def execute(self, query: str, params: Sequence[object] | None = None) -> None:
        """Execute a query with optional parameters."""
        ...

    def fetchone(self) -> RowData | None:
        """Return a single row, if any."""
        ...

    def fetchall(self) -> list[RowData]:
        """Return all rows from the cursor."""
        ...

    def close(self) -> None:
        """Close the cursor."""
        ...

    def __enter__(self) -> Self:
        """Enter a context manager."""
        ...

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Exit a context manager."""
        ...


class PostgresConnection(Protocol):
    """Protocol for PostgreSQL connections used by server mode."""

    autocommit: bool

    def cursor(self, cursor_factory: object | None = None) -> PostgresCursor:
        """Create a new cursor instance."""
        ...

    def commit(self) -> None:
        """Commit the current transaction."""
        ...

    def rollback(self) -> None:
        """Rollback the current transaction."""
        ...

    def close(self) -> None:
        """Close the database connection."""
        ...

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


def _parse_utc_timestamp(value: str) -> datetime:
    """Parse a timestamp string into an aware UTC datetime."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class Database(ABC):
    """Abstract base class for database operations."""

    @abstractmethod
    def close(self) -> None:
        """Close database connection."""

    # --- Job Operations ---

    @abstractmethod
    def create_job(  # noqa: PLR0913
        self,
        command_argv: list[str],
        workdir: str,
        name: str | None = None,
        config: JSONDict | None = None,
        tags: list[str] | None = None,
        parent_job_id: int | None = None,
    ) -> int:
        """Create a new job and return its ID."""

    @abstractmethod
    def claim_job(self, worker_id: str, lease_seconds: int = 60) -> JobRecord | None:
        """Atomically claim the next queued job for a worker."""

    @abstractmethod
    def renew_lease(self, job_id: int, worker_id: str, lease_seconds: int = 60) -> bool:
        """Renew the lease for a job. Returns True if successful."""

    @abstractmethod
    def update_job_heartbeat(self, job_id: int) -> str | None:
        """Update the heartbeat timestamp. Returns cancel_requested_at if set."""

    @abstractmethod
    def update_job_process_info(self, job_id: int, pid: int, pgid: int) -> None:
        """Store process info for a running job."""

    @abstractmethod
    def complete_job(
        self,
        job_id: int,
        exit_code: int,
        run_id: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Mark a job as completed or failed based on exit code."""

    @abstractmethod
    def cancel_job(self, job_id: int) -> str:
        """Cancel a job. Returns the previous status."""

    @abstractmethod
    def get_job(self, job_id: int) -> JobRecord | None:
        """Get a job by ID."""

    @abstractmethod
    def get_active_jobs(self) -> list[JobRecord]:
        """Get all queued and running jobs."""

    @abstractmethod
    def cancel_all_queued(self) -> int:
        """Cancel all queued jobs. Returns count of cancelled jobs."""

    @abstractmethod
    def get_expired_leases(self) -> list[JobRecord]:
        """Find running jobs with expired leases."""

    @abstractmethod
    def requeue_expired_jobs(self) -> list[JobRecord]:
        """Requeue jobs with expired leases."""

    # --- Run Operations ---

    @abstractmethod
    def create_run(  # noqa: PLR0913
        self,
        run_id: str,
        run_dir: str,
        name: str | None = None,
        config: JSONDict | None = None,
        tags: list[str] | None = None,
        job_id: int | None = None,
    ) -> None:
        """Create a new run record."""

    @abstractmethod
    def complete_run(
        self,
        run_id: str,
        status: str,
        summary: JSONDict | None = None,
    ) -> None:
        """Mark a run as completed or failed."""

    @abstractmethod
    def get_run(self, run_id: str) -> RunRecord | None:
        """Get a run by ID."""

    @abstractmethod
    def get_runs(
        self,
        status: str | None = None,
        tag: str | None = None,
        limit: int = 50,
    ) -> list[RunRecord]:
        """Get runs with optional filtering."""

    @abstractmethod
    def get_run_by_job_id(self, job_id: int) -> RunRecord | None:
        """Get a run by its associated job ID."""

    # --- Worker Operations ---

    @abstractmethod
    def register_worker(
        self,
        worker_id: str,
        pid: int,
        hostname: str,
        gpu_index: int | None = None,
    ) -> None:
        """Register or update a worker."""

    @abstractmethod
    def update_worker_status(
        self,
        worker_id: str,
        status: str,
        current_job_id: int | None = None,
    ) -> None:
        """Update worker status and current job."""

    @abstractmethod
    def unregister_worker(self, worker_id: str) -> None:
        """Mark worker as offline on graceful shutdown."""

    @abstractmethod
    def get_workers(self) -> list[WorkerRecord]:
        """Get all registered workers."""


class SQLiteDatabase(Database):
    """SQLite database implementation for local mode."""

    db_path: Path
    conn: sqlite3.Connection

    def __init__(self, db_path: Path) -> None:
        """Initialize a SQLite database connection."""
        self.db_path = db_path
        self.conn = sqlite3.connect(
            str(db_path),
            timeout=5.0,
            isolation_level=None,
            check_same_thread=False,  # Allow use across threads (for FastAPI)
        )
        _ = self.conn.execute("PRAGMA journal_mode=WAL")
        _ = self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.row_factory = sqlite3.Row

    @override
    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        """Initialize database schema."""
        _ = self.conn.executescript(SQLITE_SCHEMA)

    def _deserialize_job(self, row: sqlite3.Row) -> JobRecord:
        """Deserialize a job row."""
        return JobRecord.model_validate(_row_to_dict(row))

    # --- Job Operations ---

    @override
    def create_job(
        self,
        command_argv: list[str],
        workdir: str,
        name: str | None = None,
        config: JSONDict | None = None,
        tags: list[str] | None = None,
        parent_job_id: int | None = None,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO jobs (
                name,
                command_argv,
                workdir,
                config,
                tags,
                parent_job_id,
                attempt
            )
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
        if cursor.lastrowid is None:
            msg = "Failed to create job"
            raise RuntimeError(msg)
        return cursor.lastrowid

    @override
    def claim_job(self, worker_id: str, lease_seconds: int = 60) -> JobRecord | None:
        try:
            _ = self.conn.execute("BEGIN IMMEDIATE")
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
            row = _fetchone(cursor)
            _ = self.conn.execute("COMMIT")

            if row is None:
                return None

            job_data = _row_to_dict(row)
            job_data["status"] = "running"
            job_data["worker_id"] = worker_id
            return JobRecord.model_validate(job_data)
        except sqlite3.Error:
            _ = self.conn.execute("ROLLBACK")
            raise

    @override
    def renew_lease(self, job_id: int, worker_id: str, lease_seconds: int = 60) -> bool:
        """For SQLite, this just updates heartbeat (no real lease)."""
        now = utcnow()
        cursor = self.conn.execute(
            "UPDATE jobs SET heartbeat_at = ? WHERE id = ? AND worker_id = ?",
            (now, job_id, worker_id),
        )
        return cursor.rowcount > 0

    @override
    def update_job_heartbeat(self, job_id: int) -> str | None:
        now = utcnow()
        _ = self.conn.execute(
            "UPDATE jobs SET heartbeat_at = ? WHERE id = ?",
            (now, job_id),
        )
        cursor = self.conn.execute(
            "SELECT cancel_requested_at FROM jobs WHERE id = ?",
            (job_id,),
        )
        row = _fetchone(cursor)
        if row is None:
            return None
        return cast("Optional[str]", row["cancel_requested_at"])

    @override
    def update_job_process_info(self, job_id: int, pid: int, pgid: int) -> None:
        _ = self.conn.execute(
            "UPDATE jobs SET pid = ?, pgid = ? WHERE id = ?",
            (pid, pgid, job_id),
        )

    @override
    def complete_job(
        self,
        job_id: int,
        exit_code: int,
        run_id: str | None = None,
        error_message: str | None = None,
    ) -> None:
        status = "completed" if exit_code == 0 else "failed"
        now = utcnow()
        _ = self.conn.execute(
            """
            UPDATE jobs
            SET status = ?,
                finished_at = ?,
                exit_code = ?,
                run_id = ?,
                error_message = ?,
                pid = NULL,
                pgid = NULL
            WHERE id = ?
            """,
            (status, now, exit_code, run_id, error_message, job_id),
        )

    @override
    def cancel_job(self, job_id: int) -> str:
        cursor = self.conn.execute(
            "SELECT status FROM jobs WHERE id = ?",
            (job_id,),
        )
        row = _fetchone(cursor)

        if row is None:
            msg = f"Job {job_id} not found"
            raise ValueError(msg)

        old_status = cast("str", row["status"])
        now = utcnow()

        if old_status == "queued":
            _ = self.conn.execute(
                "UPDATE jobs SET status = 'cancelled', finished_at = ? WHERE id = ?",
                (now, job_id),
            )
        elif old_status == "running":
            _ = self.conn.execute(
                "UPDATE jobs SET cancel_requested_at = ? WHERE id = ?",
                (now, job_id),
            )

        return old_status

    @override
    def get_job(self, job_id: int) -> JobRecord | None:
        cursor = self.conn.execute(
            "SELECT * FROM jobs WHERE id = ?",
            (job_id,),
        )
        row = _fetchone(cursor)
        if row is None:
            return None
        return self._deserialize_job(row)

    @override
    def get_active_jobs(self) -> list[JobRecord]:
        cursor = self.conn.execute(
            """
            SELECT * FROM jobs
            WHERE status IN ('queued', 'running')
            ORDER BY created_at
            """
        )
        rows = _fetchall(cursor)
        return [JobRecord.model_validate(_row_to_dict(row)) for row in rows]

    @override
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

    @override
    def get_expired_leases(self) -> list[JobRecord]:
        """For SQLite, check heartbeat timeout instead of lease."""
        now = datetime.now(timezone.utc)
        cutoff_dt = now - timedelta(seconds=120)
        cutoff = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        cursor = self.conn.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'running'
              AND heartbeat_at IS NOT NULL
              AND heartbeat_at < ?
            """,
            (cutoff,),
        )
        rows = _fetchall(cursor)
        return [JobRecord.model_validate(_row_to_dict(row)) for row in rows]

    @override
    def requeue_expired_jobs(self) -> list[JobRecord]:
        expired = self.get_expired_leases()
        for job in expired:
            _ = self.conn.execute(
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
                (job.id,),
            )
        return expired

    # --- Run Operations ---

    @override
    def create_run(
        self,
        run_id: str,
        run_dir: str,
        name: str | None = None,
        config: JSONDict | None = None,
        tags: list[str] | None = None,
        job_id: int | None = None,
    ) -> None:
        _ = self.conn.execute(
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

    @override
    def complete_run(
        self,
        run_id: str,
        status: str,
        summary: JSONDict | None = None,
    ) -> None:
        cursor = self.conn.execute(
            "SELECT started_at FROM runs WHERE id = ?",
            (run_id,),
        )
        row = _fetchone(cursor)

        now_dt = utcnow_dt()
        now = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        duration = None

        if row is not None and row["started_at"]:
            with suppress(ValueError):
                started = _parse_utc_timestamp(cast("str", row["started_at"]))
                duration = (now_dt - started).total_seconds()

        _ = self.conn.execute(
            """
            UPDATE runs
            SET status = ?, finished_at = ?, duration_seconds = ?, summary = ?
            WHERE id = ?
            """,
            (status, now, duration, json.dumps(summary) if summary else None, run_id),
        )

    @override
    def get_run(self, run_id: str) -> RunRecord | None:
        cursor = self.conn.execute(
            "SELECT * FROM runs WHERE id = ?",
            (run_id,),
        )
        row = _fetchone(cursor)
        if row is None:
            return None
        return RunRecord.model_validate(_row_to_dict(row))

    @override
    def get_runs(
        self,
        status: str | None = None,
        tag: str | None = None,
        limit: int = 50,
    ) -> list[RunRecord]:
        query = "SELECT * FROM runs WHERE 1=1"
        params: list[object] = []

        if status:
            query += " AND status = ?"
            params.append(status)

        if tag:
            query += " AND tags LIKE ?"
            params.append(f'%"{tag}"%')

        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)

        cursor = self.conn.execute(query, params)
        rows = _fetchall(cursor)
        return [
            RunRecord.model_validate(_row_to_dict(row))
            for row in rows
        ]

    @override
    def get_run_by_job_id(self, job_id: int) -> RunRecord | None:
        cursor = self.conn.execute(
            "SELECT * FROM runs WHERE job_id = ?",
            (job_id,),
        )
        row = _fetchone(cursor)
        if row is None:
            return None
        return RunRecord.model_validate(_row_to_dict(row))

    # --- Worker Operations ---

    @override
    def register_worker(
        self,
        worker_id: str,
        pid: int,
        hostname: str,
        gpu_index: int | None = None,
    ) -> None:
        now = utcnow()
        _ = self.conn.execute(
            """
            INSERT INTO workers (
                id,
                pid,
                hostname,
                gpu_index,
                gpu_id,
                status,
                started_at,
                last_heartbeat,
                heartbeat_at
            )
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

    @override
    def update_worker_status(
        self,
        worker_id: str,
        status: str,
        current_job_id: int | None = None,
    ) -> None:
        now = utcnow()
        _ = self.conn.execute(
            """
            UPDATE workers
            SET status = ?,
                current_job_id = ?,
                last_heartbeat = ?,
                heartbeat_at = ?
            WHERE id = ?
            """,
            (status, current_job_id, now, now, worker_id),
        )

    @override
    def unregister_worker(self, worker_id: str) -> None:
        _ = self.conn.execute(
            "UPDATE workers SET status = 'offline', current_job_id = NULL WHERE id = ?",
            (worker_id,),
        )

    @override
    def get_workers(self) -> list[WorkerRecord]:
        cursor = self.conn.execute("SELECT * FROM workers ORDER BY id")
        rows = _fetchall(cursor)
        return [WorkerRecord.model_validate(_row_to_dict(row)) for row in rows]


class PostgresDatabase(Database):
    """PostgreSQL database implementation for server mode."""

    conn: PostgresConnection
    _cursor_factory: object

    def __init__(self, connection_url: str) -> None:
        """Initialize a PostgreSQL database connection."""
        try:
            import psycopg2  # noqa: PLC0415
            import psycopg2.extras  # noqa: PLC0415
        except ImportError:
            msg = (
                "psycopg2 is required for PostgreSQL support. "
                "Install with: pip install whirr[server]"
            )
            raise ImportError(msg) from None

        connection = cast("object", psycopg2.connect(connection_url))
        self.conn = cast("PostgresConnection", connection)
        self.conn.autocommit = False
        # Use RealDictCursor for dict-like row access
        self._cursor_factory = cast("object", psycopg2.extras.RealDictCursor)

    @override
    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        """Initialize database schema."""
        with self.conn.cursor() as cur:
            cur.execute(POSTGRES_SCHEMA)
        self.conn.commit()

    def _execute(
        self,
        query: str,
        params: Sequence[object] | None = None,
    ) -> PostgresCursor:
        """Execute a query and return cursor."""
        cur = self.conn.cursor(cursor_factory=self._cursor_factory)
        cur.execute(query, params or ())
        return cur

    # --- Job Operations ---

    @override
    def create_job(
        self,
        command_argv: list[str],
        workdir: str,
        name: str | None = None,
        config: JSONDict | None = None,
        tags: list[str] | None = None,
        parent_job_id: int | None = None,
    ) -> int:
        cur = self._execute(
            """
            INSERT INTO jobs (
                name,
                command_argv,
                workdir,
                config,
                tags,
                parent_job_id,
                attempt
            )
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
        row = cur.fetchone()
        if row is None:
            self.conn.rollback()
            msg = "Failed to create job"
            raise RuntimeError(msg)
        job_id = cast("int", row["id"])
        self.conn.commit()
        return job_id

    @override
    def claim_job(self, worker_id: str, lease_seconds: int = 60) -> JobRecord | None:
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

            job_data = _row_to_dict(row)
            job_data["status"] = "running"
            job_data["worker_id"] = worker_id
            return JobRecord.model_validate(job_data)
        except Exception:
            self.conn.rollback()
            raise

    @override
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

    @override
    def update_job_heartbeat(self, job_id: int) -> str | None:
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
            cancel_requested_at = cast("datetime", row["cancel_requested_at"])
            return cancel_requested_at.isoformat()
        return None

    @override
    def update_job_process_info(self, job_id: int, pid: int, pgid: int) -> None:
        _ = self._execute(
            "UPDATE jobs SET pid = %s, pgid = %s WHERE id = %s",
            (pid, pgid, job_id),
        )
        self.conn.commit()

    @override
    def complete_job(
        self,
        job_id: int,
        exit_code: int,
        run_id: str | None = None,
        error_message: str | None = None,
    ) -> None:
        status = "completed" if exit_code == 0 else "failed"
        _ = self._execute(
            """
            UPDATE jobs
            SET status = %s,
                finished_at = NOW(),
                exit_code = %s,
                run_id = %s,
                error_message = %s,
                pid = NULL,
                pgid = NULL,
                lease_expires_at = NULL
            WHERE id = %s
            """,
            (status, exit_code, run_id, error_message, job_id),
        )
        self.conn.commit()

    @override
    def cancel_job(self, job_id: int) -> str:
        cur = self._execute(
            "SELECT status FROM jobs WHERE id = %s",
            (job_id,),
        )
        row = cur.fetchone()

        if row is None:
            self.conn.rollback()
            msg = f"Job {job_id} not found"
            raise ValueError(msg)

        old_status = cast("str", row["status"])

        if old_status == "queued":
            _ = self._execute(
                """
                UPDATE jobs
                SET status = 'cancelled', finished_at = NOW()
                WHERE id = %s
                """,
                (job_id,),
            )
        elif old_status == "running":
            _ = self._execute(
                "UPDATE jobs SET cancel_requested_at = NOW() WHERE id = %s",
                (job_id,),
            )

        self.conn.commit()
        return old_status

    @override
    def get_job(self, job_id: int) -> JobRecord | None:
        cur = self._execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return JobRecord.model_validate(_row_to_dict(row))

    @override
    def get_active_jobs(self) -> list[JobRecord]:
        cur = self._execute(
            """
            SELECT * FROM jobs
            WHERE status IN ('queued', 'running')
            ORDER BY created_at
            """
        )
        return [JobRecord.model_validate(_row_to_dict(row)) for row in cur.fetchall()]

    @override
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

    @override
    def get_expired_leases(self) -> list[JobRecord]:
        """Find running jobs with expired leases."""
        cur = self._execute(
            """
            SELECT * FROM jobs
            WHERE status = 'running'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at < NOW()
            """
        )
        return [JobRecord.model_validate(_row_to_dict(row)) for row in cur.fetchall()]

    @override
    def requeue_expired_jobs(self) -> list[JobRecord]:
        """Requeue jobs with expired leases."""
        expired = self.get_expired_leases()
        for job in expired:
            _ = self._execute(
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
                (job.id,),
            )
        self.conn.commit()
        return expired

    # --- Run Operations ---

    @override
    def create_run(
        self,
        run_id: str,
        run_dir: str,
        name: str | None = None,
        config: JSONDict | None = None,
        tags: list[str] | None = None,
        job_id: int | None = None,
    ) -> None:
        _ = self._execute(
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

    @override
    def complete_run(
        self,
        run_id: str,
        status: str,
        summary: JSONDict | None = None,
    ) -> None:
        cur = self._execute(
            "SELECT started_at FROM runs WHERE id = %s",
            (run_id,),
        )
        row = cur.fetchone()

        duration = None
        if row and row["started_at"]:
            with suppress(TypeError):
                started = cast("datetime", row["started_at"])
                finished = datetime.now(timezone.utc)
                duration = (finished - started).total_seconds()

        _ = self._execute(
            """
            UPDATE runs
            SET status = %s,
                finished_at = NOW(),
                duration_seconds = %s,
                summary = %s
            WHERE id = %s
            """,
            (status, duration, json.dumps(summary) if summary else None, run_id),
        )
        self.conn.commit()

    @override
    def get_run(self, run_id: str) -> RunRecord | None:
        cur = self._execute("SELECT * FROM runs WHERE id = %s", (run_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return RunRecord.model_validate(_row_to_dict(row))

    @override
    def get_runs(
        self,
        status: str | None = None,
        tag: str | None = None,
        limit: int = 50,
    ) -> list[RunRecord]:
        query = "SELECT * FROM runs WHERE 1=1"
        params: list[object] = []

        if status:
            query += " AND status = %s"
            params.append(status)

        if tag:
            query += " AND tags LIKE %s"
            params.append(f'%"{tag}"%')

        query += " ORDER BY started_at DESC LIMIT %s"
        params.append(limit)

        cur = self._execute(query, tuple(params))
        return [
            RunRecord.model_validate(_row_to_dict(row))
            for row in cur.fetchall()
        ]

    @override
    def get_run_by_job_id(self, job_id: int) -> RunRecord | None:
        cur = self._execute("SELECT * FROM runs WHERE job_id = %s", (job_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return RunRecord.model_validate(_row_to_dict(row))

    # --- Worker Operations ---

    @override
    def register_worker(
        self,
        worker_id: str,
        pid: int,
        hostname: str,
        gpu_index: int | None = None,
    ) -> None:
        _ = self._execute(
            """
            INSERT INTO workers (
                id,
                pid,
                hostname,
                gpu_index,
                gpu_id,
                status,
                started_at,
                last_heartbeat,
                heartbeat_at
            )
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

    @override
    def update_worker_status(
        self,
        worker_id: str,
        status: str,
        current_job_id: int | None = None,
    ) -> None:
        _ = self._execute(
            """
            UPDATE workers
            SET status = %s,
                current_job_id = %s,
                last_heartbeat = NOW(),
                heartbeat_at = NOW()
            WHERE id = %s
            """,
            (status, current_job_id, worker_id),
        )
        self.conn.commit()

    @override
    def unregister_worker(self, worker_id: str) -> None:
        _ = self._execute(
            """
            UPDATE workers
            SET status = 'offline', current_job_id = NULL
            WHERE id = %s
            """,
            (worker_id,),
        )
        self.conn.commit()

    @override
    def get_workers(self) -> list[WorkerRecord]:
        cur = self._execute("SELECT * FROM workers ORDER BY id")
        return [
            WorkerRecord.model_validate(_row_to_dict(row))
            for row in cur.fetchall()
        ]


# --- Factory function ---

def get_database(
    db_path: Path | None = None,
    connection_url: str | None = None,
) -> Database:
    """Get a database instance.

    Args:
        db_path: Path to SQLite database (for local mode)
        connection_url: PostgreSQL connection URL (for server mode)

    Returns:
        Database instance (SQLiteDatabase or PostgresDatabase)

    """
    if connection_url:
        return PostgresDatabase(connection_url)
    if db_path:
        return SQLiteDatabase(db_path)
    msg = "Either db_path or connection_url must be provided"
    raise ValueError(msg)


# --- Backward compatibility layer ---
# These functions maintain compatibility with existing code that uses the old API

# Keep old schema name for compatibility
SCHEMA = SQLITE_SCHEMA


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Get a database connection with proper settings for concurrent access.

    DEPRECATED: Use SQLiteDatabase class instead.
    """
    conn = sqlite3.connect(str(db_path), timeout=5.0, isolation_level=None)
    _ = conn.execute("PRAGMA journal_mode=WAL")
    _ = conn.execute("PRAGMA busy_timeout=5000")
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
def create_job(  # noqa: PLR0913
    conn: sqlite3.Connection,
    command_argv: list[str],
    workdir: str,
    name: str | None = None,
    config: JSONDict | None = None,
    tags: list[str] | None = None,
    parent_job_id: int | None = None,
) -> int:
    """Create a new job. DEPRECATED: Use Database.create_job() instead."""
    cursor = conn.execute(
        """
        INSERT INTO jobs (
            name,
            command_argv,
            workdir,
            config,
            tags,
            parent_job_id,
            attempt
        )
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
    if cursor.lastrowid is None:
        msg = "Failed to create job"
        raise RuntimeError(msg)
    return cursor.lastrowid


def claim_job(conn: sqlite3.Connection, worker_id: str) -> JobRecord | None:
    """Atomically claim the next queued job.

    DEPRECATED: Use Database.claim_job() instead.
    """
    try:
        _ = conn.execute("BEGIN IMMEDIATE")
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
        row = _fetchone(cursor)
        _ = conn.execute("COMMIT")

        if row is None:
            return None

        job_data = _row_to_dict(row)
        job_data["status"] = "running"
        job_data["worker_id"] = worker_id
        return JobRecord.model_validate(job_data)
    except sqlite3.Error:
        _ = conn.execute("ROLLBACK")
        raise


def update_job_process_info(
    conn: sqlite3.Connection,
    job_id: int,
    pid: int,
    pgid: int,
) -> None:
    """Store process info.

    DEPRECATED: Use Database.update_job_process_info() instead.
    """
    _ = conn.execute(
        "UPDATE jobs SET pid = ?, pgid = ? WHERE id = ?",
        (pid, pgid, job_id),
    )


def update_job_heartbeat(conn: sqlite3.Connection, job_id: int) -> str | None:
    """Update heartbeat. DEPRECATED: Use Database.update_job_heartbeat() instead."""
    now = utcnow()
    _ = conn.execute("UPDATE jobs SET heartbeat_at = ? WHERE id = ?", (now, job_id))
    cursor = conn.execute(
        "SELECT cancel_requested_at FROM jobs WHERE id = ?", (job_id,)
    )
    row = _fetchone(cursor)
    if row is None:
        return None
    return cast("Optional[str]", row["cancel_requested_at"])


def complete_job(
    conn: sqlite3.Connection,
    job_id: int,
    exit_code: int,
    run_id: str | None = None,
    error_message: str | None = None,
) -> None:
    """Mark job complete. DEPRECATED: Use Database.complete_job() instead."""
    status = "completed" if exit_code == 0 else "failed"
    now = utcnow()
    _ = conn.execute(
        """
        UPDATE jobs
        SET status = ?,
            finished_at = ?,
            exit_code = ?,
            run_id = ?,
            error_message = ?,
            pid = NULL,
            pgid = NULL
        WHERE id = ?
        """,
        (status, now, exit_code, run_id, error_message, job_id),
    )


def cancel_job(conn: sqlite3.Connection, job_id: int) -> str:
    """Cancel a job. DEPRECATED: Use Database.cancel_job() instead."""
    cursor = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,))
    row = _fetchone(cursor)
    if row is None:
        msg = f"Job {job_id} not found"
        raise ValueError(msg)

    old_status = cast("str", row["status"])
    now = utcnow()

    if old_status == "queued":
        _ = conn.execute(
            """
            UPDATE jobs
            SET status = 'cancelled', finished_at = ?
            WHERE id = ?
            """,
            (now, job_id),
        )
    elif old_status == "running":
        _ = conn.execute(
            "UPDATE jobs SET cancel_requested_at = ? WHERE id = ?",
            (now, job_id),
        )
    return old_status


def _deserialize_job(row: sqlite3.Row) -> JobRecord:
    """Deserialize a job row."""
    return JobRecord.model_validate(_row_to_dict(row))


def get_job(conn: sqlite3.Connection, job_id: int) -> JobRecord | None:
    """Get a job by ID. DEPRECATED: Use Database.get_job() instead."""
    cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    row = _fetchone(cursor)
    if row is None:
        return None
    return _deserialize_job(row)


def get_active_jobs(conn: sqlite3.Connection) -> list[JobRecord]:
    """Get active jobs. DEPRECATED: Use Database.get_active_jobs() instead."""
    cursor = conn.execute(
        "SELECT * FROM jobs WHERE status IN ('queued', 'running') ORDER BY created_at"
    )
    rows = _fetchall(cursor)
    return [JobRecord.model_validate(_row_to_dict(row)) for row in rows]


def cancel_all_queued(conn: sqlite3.Connection) -> int:
    """Cancel all queued jobs.

    DEPRECATED: Use Database.cancel_all_queued() instead.
    """
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


def get_orphaned_jobs(
    conn: sqlite3.Connection,
    timeout_seconds: int = 120,
) -> list[JobRecord]:
    """Find orphaned jobs.

    DEPRECATED: Use Database.get_expired_leases() instead.
    """
    now = datetime.now(timezone.utc)
    cutoff_dt = now - timedelta(seconds=timeout_seconds)
    cutoff = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    cursor = conn.execute(
        """
        SELECT * FROM jobs
        WHERE status = 'running'
          AND heartbeat_at IS NOT NULL
          AND heartbeat_at < ?
        """,
        (cutoff,),
    )
    rows = _fetchall(cursor)
    return [
        JobRecord.model_validate(_row_to_dict(row))
        for row in rows
    ]


def requeue_orphaned_jobs(
    conn: sqlite3.Connection,
    timeout_seconds: int = 120,
) -> list[JobRecord]:
    """Requeue orphaned jobs.

    DEPRECATED: Use Database.requeue_expired_jobs() instead.
    """
    orphaned = get_orphaned_jobs(conn, timeout_seconds)
    for job in orphaned:
        _ = conn.execute(
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
            (job.id,),
        )
    return orphaned


def retry_job(conn: sqlite3.Connection, job_id: int) -> int:
    """Retry a job. DEPRECATED."""
    original = get_job(conn, job_id)
    if original is None:
        msg = f"Job {job_id} not found"
        raise ValueError(msg)

    if original.status not in ("failed", "cancelled"):
        msg = f"Can only retry failed or cancelled jobs, got {original.status}"
        raise ValueError(msg)

    now = utcnow()
    cursor = conn.execute(
        """
        INSERT INTO jobs (
            name,
            command_argv,
            workdir,
            config,
            tags,
            parent_job_id,
            attempt,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            original.name,
            json.dumps(original.command_argv),
            original.workdir,
            json.dumps(original.config.values) if original.config else None,
            json.dumps(original.tags) if original.tags else None,
            job_id,
            original.attempt + 1,
            now,
        ),
    )
    if cursor.lastrowid is None:
        msg = "Failed to retry job"
        raise RuntimeError(msg)
    return cursor.lastrowid


# Run operations (legacy)
def create_run(  # noqa: PLR0913
    conn: sqlite3.Connection,
    run_id: str,
    run_dir: str,
    name: str | None = None,
    config: JSONDict | None = None,
    tags: list[str] | None = None,
    job_id: int | None = None,
) -> None:
    """Create a run.

    DEPRECATED: Use Database.create_run() instead.
    """
    _ = conn.execute(
        """
        INSERT INTO runs (
            id,
            job_id,
            name,
            config,
            tags,
            run_dir,
            hostname
        )
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
    summary: JSONDict | None = None,
) -> None:
    """Complete a run. DEPRECATED: Use Database.complete_run() instead."""
    cursor = conn.execute("SELECT started_at FROM runs WHERE id = ?", (run_id,))
    row = _fetchone(cursor)
    now_dt = utcnow_dt()
    now = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    duration = None

    if row is not None and row["started_at"]:
        with suppress(ValueError):
            started = _parse_utc_timestamp(cast("str", row["started_at"]))
            duration = (now_dt - started).total_seconds()

    _ = conn.execute(
        """
        UPDATE runs
        SET status = ?, finished_at = ?, duration_seconds = ?, summary = ?
        WHERE id = ?
        """,
        (status, now, duration, json.dumps(summary) if summary else None, run_id),
    )


def get_run(conn: sqlite3.Connection, run_id: str) -> RunRecord | None:
    """Get a run. DEPRECATED: Use Database.get_run() instead."""
    cursor = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
    row = _fetchone(cursor)
    if row is None:
        return None
    return RunRecord.model_validate(_row_to_dict(row))


def get_runs(
    conn: sqlite3.Connection,
    status: str | None = None,
    tag: str | None = None,
    limit: int = 50,
) -> list[RunRecord]:
    """Get runs. DEPRECATED: Use Database.get_runs() instead."""
    query = "SELECT * FROM runs WHERE 1=1"
    params: list[object] = []

    if status:
        query += " AND status = ?"
        params.append(status)

    if tag:
        query += " AND tags LIKE ?"
        params.append(f'%"{tag}"%')

    query += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)

    cursor = conn.execute(query, params)
    rows = _fetchall(cursor)
    return [
        RunRecord.model_validate(_row_to_dict(row))
        for row in rows
    ]


def get_run_by_job_id(conn: sqlite3.Connection, job_id: int) -> RunRecord | None:
    """Get run by job ID.

    DEPRECATED: Use Database.get_run_by_job_id() instead.
    """
    cursor = conn.execute("SELECT * FROM runs WHERE job_id = ?", (job_id,))
    row = _fetchone(cursor)
    if row is None:
        return None
    return RunRecord.model_validate(_row_to_dict(row))


# Worker operations (legacy)
def register_worker(
    conn: sqlite3.Connection,
    worker_id: str,
    pid: int,
    hostname: str,
    gpu_index: int | None = None,
) -> None:
    """Register worker.

    DEPRECATED: Use Database.register_worker() instead.
    """
    now = utcnow()
    _ = conn.execute(
        """
        INSERT INTO workers (
            id,
            pid,
            hostname,
            gpu_index,
            status,
            started_at,
            last_heartbeat
        )
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
    current_job_id: int | None = None,
) -> None:
    """Update worker status. DEPRECATED: Use Database.update_worker_status() instead."""
    now = utcnow()
    _ = conn.execute(
        """
        UPDATE workers
        SET status = ?, current_job_id = ?, last_heartbeat = ?
        WHERE id = ?
        """,
        (status, current_job_id, now, worker_id),
    )


def unregister_worker(conn: sqlite3.Connection, worker_id: str) -> None:
    """Unregister worker. DEPRECATED: Use Database.unregister_worker() instead."""
    _ = conn.execute(
        "UPDATE workers SET status = 'offline', current_job_id = NULL WHERE id = ?",
        (worker_id,),
    )


def get_workers(conn: sqlite3.Connection) -> list[WorkerRecord]:
    """Get workers. DEPRECATED: Use Database.get_workers() instead."""
    cursor = conn.execute("SELECT * FROM workers ORDER BY id")
    rows = _fetchall(cursor)
    return [WorkerRecord.model_validate(_row_to_dict(row)) for row in rows]
