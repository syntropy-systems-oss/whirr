# Copyright (c) Syntropy Systems
"""Tests for whirr Run class."""

from pathlib import Path

import pytest

from whirr.models.run import RunConfig
from whirr.run import Run, read_meta, read_metrics


class TestRun:
    """Tests for the Run class."""

    def test_run_creates_directory(self, whirr_project: Path) -> None:
        """Test that Run creates the run directory."""
        _ = whirr_project
        run = Run(name="test-run")

        assert run.run_dir.exists()
        assert (run.run_dir / "meta.json").exists()

        run.finish()

    def test_run_log_metrics(self, whirr_project: Path) -> None:
        """Test logging metrics."""
        _ = whirr_project
        run = Run(name="test-run")

        run.log({"loss": 1.0, "accuracy": 0.5}, step=0)
        run.log({"loss": 0.5, "accuracy": 0.7}, step=1)
        run.log({"loss": 0.2, "accuracy": 0.9}, step=2)

        run.finish()

        # Read back metrics
        metrics = read_metrics(run.run_dir / "metrics.jsonl")

        assert len(metrics) == 3
        first = metrics[0].model_dump(by_alias=True)
        third = metrics[2].model_dump(by_alias=True)
        assert first["loss"] == 1.0
        assert first["step"] == 0
        assert first["_idx"] == 0
        assert third["loss"] == 0.2
        assert third["_idx"] == 2

    def test_run_log_without_step(self, whirr_project: Path) -> None:
        """Test logging without explicit step."""
        _ = whirr_project
        run = Run(name="test-run")

        run.log({"loss": 1.0})
        run.log({"loss": 0.5})

        run.finish()

        metrics = read_metrics(run.run_dir / "metrics.jsonl")

        assert len(metrics) == 2
        record = metrics[0].model_dump(by_alias=True)
        assert "step" not in record
        assert "_idx" in record

    def test_run_summary(self, whirr_project: Path) -> None:
        """Test setting summary metrics."""
        _ = whirr_project
        run = Run(name="test-run", config={"lr": 0.01})

        run.log({"loss": 1.0}, step=0)
        run.summary({"best_loss": 0.1, "best_epoch": 10})
        run.finish()

        meta = read_meta(run.run_dir)
        assert meta is not None
        assert meta.summary is not None
        assert meta.summary.values == {"best_loss": 0.1, "best_epoch": 10}
        assert meta.status == "completed"

    def test_run_context_manager(self, whirr_project: Path) -> None:
        """Test using Run as context manager."""
        _ = whirr_project
        with Run(name="test-run") as run:
            run.log({"loss": 1.0})

        meta = read_meta(run.run_dir)
        assert meta is not None
        assert meta.status == "completed"

    def test_run_context_manager_exception(self, whirr_project: Path) -> None:
        """Test that context manager marks run as failed on exception."""
        _ = whirr_project
        run_holder: list[Run] = []

        def run_and_fail() -> None:
            with Run(name="test-run") as run:
                run_holder.append(run)
                run.log({"loss": 1.0})
                msg = "Test error"
                if run_holder:
                    raise ValueError(msg)

        with pytest.raises(ValueError, match="Test error"):
            run_and_fail()

        assert run_holder
        run_ref = run_holder[0]
        meta = read_meta(run_ref.run_dir)
        assert meta is not None
        assert meta.status == "failed"

    def test_run_cannot_log_after_finish(self, whirr_project: Path) -> None:
        """Test that logging after finish raises error."""
        _ = whirr_project
        run = Run(name="test-run")
        run.finish()

        with pytest.raises(RuntimeError):
            run.log({"loss": 1.0})

    def test_run_double_finish(self, whirr_project: Path) -> None:
        """Test that finish can be called multiple times safely."""
        _ = whirr_project
        run = Run(name="test-run")
        run.finish()
        run.finish()  # Should not raise

    def test_run_with_config_and_tags(self, whirr_project: Path) -> None:
        """Test run with configuration and tags."""
        _ = whirr_project
        run = Run(
            name="test-run",
            config={"lr": 0.01, "batch_size": 32},
            tags=["baseline", "test"],
        )

        run.finish()

        meta = read_meta(run.run_dir)
        assert meta is not None
        assert meta.tags == ["baseline", "test"]
        assert meta.config_file == "config.json"

        # Config is now in separate file
        config_path = run.run_dir / "config.json"
        config = RunConfig.model_validate_json(config_path.read_text())
        assert config.model_dump() == {"lr": 0.01, "batch_size": 32}


    def test_save_artifact(self, whirr_project: Path) -> None:
        """Test saving artifacts."""
        run = Run(name="test-run")

        # Create a temp file to save as artifact
        temp_file = whirr_project / "model.pt"
        _ = temp_file.write_text("fake model data")

        saved_path = run.save_artifact(str(temp_file), "best_model.pt")

        assert saved_path.exists()
        assert saved_path.name == "best_model.pt"
        assert saved_path.read_text() == "fake model data"
        assert saved_path.parent.name == "artifacts"

        run.finish()

    def test_save_artifact_not_found(self, whirr_project: Path) -> None:
        """Test saving nonexistent artifact raises error."""
        _ = whirr_project
        run = Run(name="test-run")

        with pytest.raises(FileNotFoundError):
            _ = run.save_artifact("/nonexistent/file.txt")

        run.finish()


class TestReadMetrics:
    """Tests for reading metrics files."""

    def test_read_empty_file(self, temp_dir: Path) -> None:
        """Test reading empty metrics file."""
        metrics_path = temp_dir / "metrics.jsonl"
        metrics_path.touch()

        metrics = read_metrics(metrics_path)
        assert metrics == []

    def test_read_nonexistent_file(self, temp_dir: Path) -> None:
        """Test reading nonexistent file."""
        metrics_path = temp_dir / "metrics.jsonl"

        metrics = read_metrics(metrics_path)
        assert metrics == []

    def test_read_truncated_line(self, temp_dir: Path) -> None:
        """Test that truncated final line is ignored."""
        metrics_path = temp_dir / "metrics.jsonl"
        with metrics_path.open("w") as f:
            _ = f.write('{"loss": 1.0}\n')
            _ = f.write('{"loss": 0.5}\n')
            _ = f.write('{"loss": 0')  # Truncated

        metrics = read_metrics(metrics_path)

        assert len(metrics) == 2
        first = metrics[0].model_dump(by_alias=True)
        second = metrics[1].model_dump(by_alias=True)
        assert first["loss"] == 1.0
        assert second["loss"] == 0.5
