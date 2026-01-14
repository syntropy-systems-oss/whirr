"""Process runner with orphan prevention."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


def setup_pdeathsig() -> None:
    """
    Set PDEATHSIG so child dies when parent dies.

    This prevents orphan processes when the worker crashes.
    Only works on Linux.
    """
    if sys.platform == "linux":
        try:
            import ctypes

            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            PR_SET_PDEATHSIG = 1
            libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL)
        except Exception:
            # Can't set PDEATHSIG, continue without it
            pass


class JobRunner:
    """
    Runs a job command with proper process management.

    Features:
    - Uses start_new_session=True for reliable process group
    - Sets PDEATHSIG on Linux to prevent orphans
    - Captures stdout/stderr to output.log
    - Provides graceful and forceful termination
    """

    def __init__(
        self,
        command_argv: list[str],
        workdir: Path,
        run_dir: Path,
        env: Optional[dict[str, str]] = None,
    ):
        """
        Initialize a job runner.

        Args:
            command_argv: Command as list of argv tokens (no shell)
            workdir: Working directory to run the command in
            run_dir: Directory for output log
            env: Additional environment variables
        """
        self.command_argv = command_argv
        self.workdir = workdir
        self.run_dir = run_dir
        self.output_path = run_dir / "output.log"

        # Merge environment
        self.env = os.environ.copy()
        if env:
            self.env.update(env)

        self._process: Optional[subprocess.Popen] = None
        self._exit_code: Optional[int] = None
        self._output_file = None

    def start(self) -> None:
        """Start the job process."""
        self.run_dir.mkdir(parents=True, exist_ok=True)

        # Open output log for writing
        self._output_file = open(self.output_path, "w")

        self._process = subprocess.Popen(
            self.command_argv,
            stdout=self._output_file,
            stderr=subprocess.STDOUT,
            env=self.env,
            cwd=str(self.workdir),
            start_new_session=True,  # Creates new process group
            preexec_fn=setup_pdeathsig if sys.platform == "linux" else None,
        )

    def poll(self) -> Optional[int]:
        """
        Check if process has finished.

        Returns exit code if finished, None if still running.
        """
        if self._process is None:
            return self._exit_code

        code = self._process.poll()
        if code is not None:
            self._exit_code = code
            self._cleanup()

        return code

    def wait(self) -> int:
        """Wait for the process to finish and return exit code."""
        if self._process is None:
            return self._exit_code or 0

        code = self._process.wait()
        self._exit_code = code
        self._cleanup()
        return code

    def kill(self, grace_period: float = 10.0) -> int:
        """
        Kill the job process.

        First sends SIGTERM to the process group, waits for grace_period,
        then sends SIGKILL if still alive.

        Args:
            grace_period: Seconds to wait after SIGTERM before SIGKILL

        Returns:
            Exit code (negative signal number if killed)
        """
        if self._process is None:
            return self._exit_code or 0

        # Already finished?
        if self._process.poll() is not None:
            # returncode is set after poll() returns non-None
            exit_code = self._process.returncode or 0
            self._exit_code = exit_code
            self._cleanup()
            return exit_code

        # Get the process group ID (same as session ID with start_new_session)
        try:
            pgid = os.getpgid(self._process.pid)
        except (OSError, ProcessLookupError):
            # Process already gone
            self._cleanup()
            return self._exit_code or -signal.SIGKILL

        # Send SIGTERM to process group
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass

        # Wait for grace period
        deadline = time.time() + grace_period
        while time.time() < deadline:
            if self._process.poll() is not None:
                exit_code = self._process.returncode or 0
                self._exit_code = exit_code
                self._cleanup()
                return exit_code
            time.sleep(0.1)

        # Still alive - SIGKILL
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass

        # Wait for process to die
        try:
            self._process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            pass

        exit_code = self._process.returncode or -signal.SIGKILL
        self._exit_code = exit_code
        self._cleanup()
        return exit_code

    def _cleanup(self) -> None:
        """Cleanup resources."""
        if self._output_file:
            try:
                self._output_file.close()
            except Exception:
                pass
            self._output_file = None

    @property
    def pid(self) -> Optional[int]:
        """Get the process ID."""
        if self._process is None:
            return None
        return self._process.pid

    @property
    def pgid(self) -> Optional[int]:
        """Get the process group ID."""
        if self._process is None:
            return None
        try:
            return os.getpgid(self._process.pid)
        except (OSError, ProcessLookupError):
            return None

    @property
    def exit_code(self) -> Optional[int]:
        """Get the exit code if finished."""
        return self._exit_code

    @property
    def is_running(self) -> bool:
        """Check if the process is still running."""
        if self._process is None:
            return False
        return self._process.poll() is None
