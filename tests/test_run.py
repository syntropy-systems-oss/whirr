"""Tests for whirr Run class."""

import json
from pathlib import Path

import pytest

from whirr.run import Run, read_metrics, read_meta


class TestRun:
    """Tests for the Run class."""

    def test_run_creates_directory(self, whirr_project):
        """Test that Run creates the run directory."""
        run = Run(name="test-run")

        assert run.run_dir.exists()
        assert (run.run_dir / "meta.json").exists()

        run.finish()

    def test_run_log_metrics(self, whirr_project):
        """Test logging metrics."""
        run = Run(name="test-run")

        run.log({"loss": 1.0, "accuracy": 0.5}, step=0)
        run.log({"loss": 0.5, "accuracy": 0.7}, step=1)
        run.log({"loss": 0.2, "accuracy": 0.9}, step=2)

        run.finish()

        # Read back metrics
        metrics = read_metrics(run.run_dir / "metrics.jsonl")

        assert len(metrics) == 3
        assert metrics[0]["loss"] == 1.0
        assert metrics[0]["step"] == 0
        assert metrics[0]["_idx"] == 0
        assert metrics[2]["loss"] == 0.2
        assert metrics[2]["_idx"] == 2

    def test_run_log_without_step(self, whirr_project):
        """Test logging without explicit step."""
        run = Run(name="test-run")

        run.log({"loss": 1.0})
        run.log({"loss": 0.5})

        run.finish()

        metrics = read_metrics(run.run_dir / "metrics.jsonl")

        assert len(metrics) == 2
        assert "step" not in metrics[0]
        assert "_idx" in metrics[0]

    def test_run_summary(self, whirr_project):
        """Test setting summary metrics."""
        run = Run(name="test-run", config={"lr": 0.01})

        run.log({"loss": 1.0}, step=0)
        run.summary({"best_loss": 0.1, "best_epoch": 10})
        run.finish()

        meta = read_meta(run.run_dir)

        assert meta["summary"] == {"best_loss": 0.1, "best_epoch": 10}
        assert meta["status"] == "completed"

    def test_run_context_manager(self, whirr_project):
        """Test using Run as context manager."""
        with Run(name="test-run") as run:
            run.log({"loss": 1.0})

        meta = read_meta(run.run_dir)
        assert meta["status"] == "completed"

    def test_run_context_manager_exception(self, whirr_project):
        """Test that context manager marks run as failed on exception."""
        try:
            with Run(name="test-run") as run:
                run.log({"loss": 1.0})
                raise ValueError("Test error")
        except ValueError:
            pass

        meta = read_meta(run.run_dir)
        assert meta["status"] == "failed"

    def test_run_cannot_log_after_finish(self, whirr_project):
        """Test that logging after finish raises error."""
        run = Run(name="test-run")
        run.finish()

        with pytest.raises(RuntimeError):
            run.log({"loss": 1.0})

    def test_run_double_finish(self, whirr_project):
        """Test that finish can be called multiple times safely."""
        run = Run(name="test-run")
        run.finish()
        run.finish()  # Should not raise

    def test_run_with_config_and_tags(self, whirr_project):
        """Test run with configuration and tags."""
        import json

        run = Run(
            name="test-run",
            config={"lr": 0.01, "batch_size": 32},
            tags=["baseline", "test"],
        )

        run.finish()

        meta = read_meta(run.run_dir)
        assert meta["tags"] == ["baseline", "test"]
        assert meta["config_file"] == "config.json"

        # Config is now in separate file
        config_path = run.run_dir / "config.json"
        with open(config_path) as f:
            config = json.load(f)
        assert config == {"lr": 0.01, "batch_size": 32}


    def test_save_artifact(self, whirr_project):
        """Test saving artifacts."""
        run = Run(name="test-run")

        # Create a temp file to save as artifact
        temp_file = whirr_project / "model.pt"
        temp_file.write_text("fake model data")

        saved_path = run.save_artifact(str(temp_file), "best_model.pt")

        assert saved_path.exists()
        assert saved_path.name == "best_model.pt"
        assert saved_path.read_text() == "fake model data"
        assert saved_path.parent.name == "artifacts"

        run.finish()

    def test_save_artifact_not_found(self, whirr_project):
        """Test saving nonexistent artifact raises error."""
        run = Run(name="test-run")

        with pytest.raises(FileNotFoundError):
            run.save_artifact("/nonexistent/file.txt")

        run.finish()


class TestReadMetrics:
    """Tests for reading metrics files."""

    def test_read_empty_file(self, temp_dir):
        """Test reading empty metrics file."""
        metrics_path = temp_dir / "metrics.jsonl"
        metrics_path.touch()

        metrics = read_metrics(metrics_path)
        assert metrics == []

    def test_read_nonexistent_file(self, temp_dir):
        """Test reading nonexistent file."""
        metrics_path = temp_dir / "metrics.jsonl"

        metrics = read_metrics(metrics_path)
        assert metrics == []

    def test_read_truncated_line(self, temp_dir):
        """Test that truncated final line is ignored."""
        metrics_path = temp_dir / "metrics.jsonl"
        with open(metrics_path, "w") as f:
            f.write('{"loss": 1.0}\n')
            f.write('{"loss": 0.5}\n')
            f.write('{"loss": 0')  # Truncated

        metrics = read_metrics(metrics_path)

        assert len(metrics) == 2
        assert metrics[0]["loss"] == 1.0
        assert metrics[1]["loss"] == 0.5
