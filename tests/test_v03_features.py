"""Tests for v0.3 features: dashboard, compare, export, git capture, pip freeze."""

import json
import os
import re

import pytest

from whirr.cli.main import app


from whirr.db import init_db
from whirr.run import Run, _capture_git_info, _capture_pip_freeze


def strip_ansi(text: str) -> str:
    """Strip ANSI escape codes from text."""
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


@pytest.fixture
def whirr_dir(tmp_path):
    """Create a whirr project directory."""
    whirr_path = tmp_path / ".whirr"
    whirr_path.mkdir()
    (whirr_path / "runs").mkdir()

    db_path = whirr_path / "whirr.db"
    init_db(db_path)

    # Change to tmp_path so whirr can find .whirr
    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(old_cwd)


class TestGitCapture:
    """Tests for git info capture."""

    def test_capture_git_info_in_repo(self):
        """Test git capture returns info when in a git repo."""
        # This test runs in the whirr repo itself
        git_info = _capture_git_info()

        # We're running in the whirr repo, so this should work
        if git_info:
            assert "commit" in git_info
            assert "short_hash" in git_info
            assert "branch" in git_info
            assert "dirty" in git_info
            assert isinstance(git_info["dirty"], bool)

    def test_capture_git_info_not_in_repo(self, tmp_path):
        """Test git capture returns None when not in a git repo."""
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            git_info = _capture_git_info()
            assert git_info is None
        finally:
            os.chdir(old_cwd)


class TestPipCapture:
    """Tests for pip freeze capture."""

    def test_capture_pip_freeze(self):
        """Test pip freeze capture returns list or None."""
        packages = _capture_pip_freeze()
        # May return None if pip isn't available in this env
        assert packages is None or isinstance(packages, list)


class TestRunWithCapture:
    """Tests for Run class with git/pip capture."""

    def test_run_captures_git_info(self, whirr_dir):
        """Test that Run captures git info when capture_git=True."""
        run = Run(
            name="test-git",
            config={"lr": 0.01},
            capture_git=True,
            capture_pip=False,
            system_metrics=False,
        )

        # Check git.json was created if we're in a git repo
        git_path = run.run_dir / "git.json"
        if run.git_info:
            assert git_path.exists()
            with open(git_path) as f:
                data = json.load(f)
            assert "commit" in data

        run.finish()

    def test_run_skips_git_capture(self, whirr_dir):
        """Test that Run skips git capture when capture_git=False."""
        run = Run(
            name="test-no-git",
            config={},
            capture_git=False,
            capture_pip=False,
            system_metrics=False,
        )

        git_path = run.run_dir / "git.json"
        assert not git_path.exists()

        run.finish()

    def test_run_captures_pip_freeze(self, whirr_dir):
        """Test that Run creates requirements.txt when capture_pip=True."""
        run = Run(
            name="test-pip",
            config={},
            capture_git=False,
            capture_pip=True,
            system_metrics=False,
        )

        req_path = run.run_dir / "requirements.txt"
        # May or may not exist depending on pip availability
        if run.pip_packages:
            assert req_path.exists()

        run.finish()

    def test_meta_includes_git_info(self, whirr_dir):
        """Test that meta.json includes git info when captured."""
        run = Run(
            name="test-meta-git",
            config={},
            capture_git=True,
            capture_pip=False,
            system_metrics=False,
        )

        meta_path = run.run_dir / "meta.json"
        with open(meta_path) as f:
            meta = json.load(f)

        if run.git_info:
            assert "git" in meta
            assert "git_file" in meta

        run.finish()


class TestCompareCommand:
    """Tests for whirr compare command."""

    def test_compare_help(self):
        """Test compare --help works."""
        from typer.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(app, ["compare", "--help"])
        assert result.exit_code == 0
        assert "Compare multiple runs" in result.stdout

    def test_compare_needs_two_runs(self, whirr_dir):
        """Test compare requires at least 2 runs."""
        from typer.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(app, ["compare", "abc123"])
        assert result.exit_code == 1
        assert "Need at least 2 runs" in result.stdout


class TestExportCommand:
    """Tests for whirr export command."""

    def test_export_help(self):
        """Test export --help works."""
        from typer.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(app, ["export", "--help"])
        assert result.exit_code == 0
        assert "Export runs" in result.stdout

    def test_export_requires_valid_extension(self, whirr_dir):
        """Test export requires .csv or .json extension."""
        from typer.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(app, ["export", "output.txt"])
        assert result.exit_code == 1
        assert ".csv or .json" in result.stdout

    def test_export_json_empty(self, whirr_dir):
        """Test export to JSON with no runs."""
        from typer.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(app, ["export", "runs.json"])
        assert result.exit_code == 0
        assert "No runs to export" in result.stdout

    def test_export_json_with_runs(self, whirr_dir):
        """Test export to JSON with runs."""
        from typer.testing import CliRunner

        # Create a test run
        run = Run(
            name="export-test",
            config={"lr": 0.01},
            capture_git=False,
            capture_pip=False,
            system_metrics=False,
        )
        run.log({"loss": 0.5}, step=0)
        run.summary({"final_loss": 0.1})
        run.finish()

        runner = CliRunner()
        output_path = whirr_dir / "runs.json"
        result = runner.invoke(app, ["export", str(output_path)])
        assert result.exit_code == 0
        assert "Exported 1 run" in result.stdout

        # Verify JSON content
        with open(output_path) as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["name"] == "export-test"

    def test_export_csv_with_runs(self, whirr_dir):
        """Test export to CSV with runs."""
        from typer.testing import CliRunner

        # Create a test run
        run = Run(
            name="csv-test",
            config={"lr": 0.01, "epochs": 10},
            capture_git=False,
            capture_pip=False,
            system_metrics=False,
        )
        run.summary({"accuracy": 0.95})
        run.finish()

        runner = CliRunner()
        output_path = whirr_dir / "runs.csv"
        result = runner.invoke(app, ["export", str(output_path)])
        assert result.exit_code == 0

        # Verify CSV exists
        assert output_path.exists()
        content = output_path.read_text()
        assert "csv-test" in content
        assert "config.lr" in content


class TestDashboardCommand:
    """Tests for whirr dashboard command."""

    def test_dashboard_help(self):
        """Test dashboard --help works."""
        from typer.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(app, ["dashboard", "--help"])
        assert result.exit_code == 0
        output = strip_ansi(result.stdout)
        assert "whirr dashboard" in output
        assert "--port" in output

    def test_dashboard_module_imports(self):
        """Test dashboard module can be imported."""
        from whirr.dashboard import app as dashboard_app

        assert dashboard_app is not None


class TestDashboardServer:
    """Tests for dashboard FastAPI server."""

    def test_dashboard_routes_exist(self):
        """Test that expected routes are registered."""
        from whirr.dashboard import app as dashboard_app

        routes = [r.path for r in dashboard_app.routes]
        assert "/" in routes
        assert "/queue" in routes
        assert "/runs" in routes
        assert "/runs/{run_id}" in routes
        assert "/partials/workers" in routes
        assert "/partials/jobs" in routes
        assert "/partials/stats" in routes
