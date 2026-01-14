"""Tests for whirr CLI commands."""

import os

from typer.testing import CliRunner

from whirr.cli.main import app

runner = CliRunner()


class TestInitCommand:
    """Tests for whirr init command."""

    def test_init_creates_directory(self, temp_dir):
        """Test that init creates .whirr directory."""
        os.chdir(temp_dir)

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 0
        assert (temp_dir / ".whirr").exists()
        assert (temp_dir / ".whirr" / "whirr.db").exists()
        assert (temp_dir / ".whirr" / "config.yaml").exists()
        assert (temp_dir / ".whirr" / "runs").exists()

    def test_init_already_initialized(self, whirr_project):
        """Test init when already initialized."""
        result = runner.invoke(app, ["init"])

        assert result.exit_code == 0
        assert "Already initialized" in result.stdout


class TestSubmitCommand:
    """Tests for whirr submit command."""

    def test_submit_basic(self, whirr_project):
        """Test basic job submission."""
        result = runner.invoke(
            app,
            ["submit", "--", "python", "train.py"],
        )

        assert result.exit_code == 0
        assert "Submitted job #1" in result.stdout

    def test_submit_with_name(self, whirr_project):
        """Test submission with name."""
        result = runner.invoke(
            app,
            ["submit", "--name", "my-job", "--", "python", "train.py"],
        )

        assert result.exit_code == 0
        assert "my-job" in result.stdout

    def test_submit_with_tags(self, whirr_project):
        """Test submission with tags."""
        result = runner.invoke(
            app,
            ["submit", "--tags", "test,baseline", "--", "python", "train.py"],
        )

        assert result.exit_code == 0
        assert "test, baseline" in result.stdout

    def test_submit_no_command(self, whirr_project):
        """Test submit without command fails."""
        result = runner.invoke(app, ["submit"])

        assert result.exit_code == 1
        assert "No command provided" in result.stdout

    def test_submit_complex_command(self, whirr_project):
        """Test submit with complex command."""
        result = runner.invoke(
            app,
            ["submit", "--", "python", "train.py", "--lr", "0.01", "--epochs", "100"],
        )

        assert result.exit_code == 0
        assert "python train.py --lr 0.01 --epochs 100" in result.stdout

    def test_submit_stores_workdir(self, whirr_project):
        """Test that submit stores the working directory."""
        result = runner.invoke(
            app,
            ["submit", "--", "echo", "hello"],
        )

        assert result.exit_code == 0
        assert "workdir:" in result.stdout


class TestStatusCommand:
    """Tests for whirr status command."""

    def test_status_empty(self, whirr_project):
        """Test status with no jobs."""
        result = runner.invoke(app, ["status"])

        assert result.exit_code == 0
        assert "No active jobs" in result.stdout

    def test_status_with_jobs(self, whirr_project):
        """Test status with queued jobs."""
        # Submit some jobs
        runner.invoke(app, ["submit", "--name", "job1", "--", "echo", "1"])
        runner.invoke(app, ["submit", "--name", "job2", "--", "echo", "2"])

        result = runner.invoke(app, ["status"])

        assert result.exit_code == 0
        assert "job1" in result.stdout
        assert "job2" in result.stdout
        assert "queued" in result.stdout

    def test_status_specific_job(self, whirr_project):
        """Test status for specific job."""
        runner.invoke(app, ["submit", "--name", "test-job", "--", "echo", "hello"])

        result = runner.invoke(app, ["status", "1"])

        assert result.exit_code == 0
        assert "Job #1" in result.stdout
        assert "test-job" in result.stdout


class TestCancelCommand:
    """Tests for whirr cancel command."""

    def test_cancel_queued(self, whirr_project):
        """Test cancelling a queued job."""
        runner.invoke(app, ["submit", "--", "echo", "hello"])

        result = runner.invoke(app, ["cancel", "1"])

        assert result.exit_code == 0
        assert "Cancelled job #1" in result.stdout

    def test_cancel_not_found(self, whirr_project):
        """Test cancelling nonexistent job."""
        result = runner.invoke(app, ["cancel", "999"])

        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_cancel_all_queued(self, whirr_project):
        """Test cancelling all queued jobs."""
        runner.invoke(app, ["submit", "--", "echo", "1"])
        runner.invoke(app, ["submit", "--", "echo", "2"])
        runner.invoke(app, ["submit", "--", "echo", "3"])

        result = runner.invoke(app, ["cancel", "--all-queued"])

        assert result.exit_code == 0
        assert "Cancelled 3 queued job(s)" in result.stdout


class TestDoctorCommand:
    """Tests for whirr doctor command."""

    def test_doctor_initialized(self, whirr_project):
        """Test doctor on initialized project."""
        result = runner.invoke(app, ["doctor"])

        assert result.exit_code == 0
        assert "whirr directory" in result.stdout
        assert "SQLite: WAL mode enabled" in result.stdout

    def test_doctor_not_initialized(self, temp_dir):
        """Test doctor when not initialized."""
        os.chdir(temp_dir)

        result = runner.invoke(app, ["doctor"])

        assert result.exit_code == 0
        assert "No .whirr directory found" in result.stdout


class TestRunsCommand:
    """Tests for whirr runs command."""

    def test_runs_empty(self, whirr_project):
        """Test runs with no runs."""
        result = runner.invoke(app, ["runs"])

        assert result.exit_code == 0
        assert "No runs found" in result.stdout
