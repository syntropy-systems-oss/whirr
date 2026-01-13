"""Pytest fixtures for whirr tests."""

import os
import tempfile
from pathlib import Path

import pytest

# Store original cwd at module load time
_original_cwd = os.getcwd()


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def whirr_project(temp_dir):
    """Create a temporary whirr project directory."""
    from whirr.db import init_db

    whirr_dir = temp_dir / ".whirr"
    whirr_dir.mkdir()
    runs_dir = whirr_dir / "runs"
    runs_dir.mkdir()

    # Initialize database
    db_path = whirr_dir / "whirr.db"
    init_db(db_path)

    # Change to temp directory
    os.chdir(temp_dir)

    yield temp_dir

    # Always return to original cwd
    os.chdir(_original_cwd)


@pytest.fixture
def db_connection(whirr_project):
    """Get a database connection for the test project."""
    from whirr.db import get_connection

    db_path = whirr_project / ".whirr" / "whirr.db"
    conn = get_connection(db_path)
    yield conn
    conn.close()
