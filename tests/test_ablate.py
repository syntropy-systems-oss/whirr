"""Tests for ablation study functionality."""

import json

from typer.testing import CliRunner

from whirr.ablate import (
    AblationSession,
    FileValue,
    generate_session_id,
    load_session_by_name,
)
from whirr.ablate.models import AblationRunResult
from whirr.cli.main import app

runner = CliRunner()


class TestAblationModels:
    """Tests for ablation data models."""

    def test_generate_session_id(self):
        """Test session ID generation."""
        id1 = generate_session_id()
        id2 = generate_session_id()
        assert len(id1) == 6
        assert len(id2) == 6
        assert id1 != id2  # Should be unique

    def test_session_save_load(self, tmp_path):
        """Test session serialization round-trip."""
        session = AblationSession(
            session_id="abc123",
            name="test-session",
            metric="win",
            seed_base=12345,
            deltas={"temp": {"temperature": 0}},
        )
        session._path = tmp_path / "session.json"
        session.save()

        loaded = AblationSession.load(session._path)
        assert loaded.session_id == "abc123"
        assert loaded.name == "test-session"
        assert loaded.metric == "win"
        assert loaded.seed_base == 12345
        assert loaded.deltas == {"temp": {"temperature": 0}}

    def test_session_with_file_value(self, tmp_path):
        """Test session with FileValue in deltas."""
        session = AblationSession(
            session_id="abc123",
            name="test",
            metric="loss",
            seed_base=99999,
            deltas={
                "system": {
                    "prompt": FileValue(path="prompts/v2.txt", text="Hello world")
                }
            },
        )
        session._path = tmp_path / "session.json"
        session.save()

        loaded = AblationSession.load(session._path)
        assert "system" in loaded.deltas
        assert isinstance(loaded.deltas["system"]["prompt"], FileValue)
        assert loaded.deltas["system"]["prompt"].path == "prompts/v2.txt"
        assert loaded.deltas["system"]["prompt"].text == "Hello world"

    def test_session_with_runs(self, tmp_path):
        """Test session with run results."""
        session = AblationSession(
            session_id="abc123",
            name="test",
            metric="loss",
            seed_base=12345,
        )
        session.runs.append(
            AblationRunResult(
                run_id="job-1",
                job_id=1,
                condition="baseline",
                replicate=0,
                seed=12345,
            )
        )
        session._path = tmp_path / "session.json"
        session.save()

        loaded = AblationSession.load(session._path)
        assert len(loaded.runs) == 1
        assert loaded.runs[0].condition == "baseline"
        assert loaded.runs[0].seed == 12345

    def test_get_seed(self):
        """Test deterministic seed derivation."""
        session = AblationSession(
            session_id="abc123",
            name="test",
            metric="win",
            seed_base=1000,
        )
        assert session.get_seed(0) == 1000
        assert session.get_seed(1) == 1001
        assert session.get_seed(10) == 1010

    def test_get_condition_names(self):
        """Test condition names include baseline + deltas."""
        session = AblationSession(
            session_id="abc123",
            name="test",
            metric="win",
            seed_base=1000,
            deltas={"temp": {"t": 0}, "lr": {"lr": 0.1}},
        )
        names = session.get_condition_names()
        assert names == ["baseline", "temp", "lr"]


class TestAblateInitCommand:
    """Tests for whirr ablate init."""

    def test_init_creates_session(self, whirr_project):
        """Test that init creates session file."""
        result = runner.invoke(
            app,
            ["ablate", "init", "test-study", "--metric", "win"],
        )

        assert result.exit_code == 0
        assert "Created ablation session" in result.stdout

        # Check index was created
        index_path = whirr_project / ".whirr" / "ablations" / "index.json"
        assert index_path.exists()

        with open(index_path) as f:
            index = json.load(f)
        assert "test-study" in index

        # Check session file exists
        session_id = index["test-study"]
        session_path = whirr_project / ".whirr" / "ablations" / f"{session_id}.json"
        assert session_path.exists()

    def test_init_duplicate_fails(self, whirr_project):
        """Test that init fails for existing session."""
        runner.invoke(app, ["ablate", "init", "dup", "--metric", "x"])
        result = runner.invoke(app, ["ablate", "init", "dup", "--metric", "x"])

        assert result.exit_code == 1
        assert "already exists" in result.stdout


class TestAblateAddCommand:
    """Tests for whirr ablate add."""

    def test_add_delta(self, whirr_project):
        """Test adding a delta."""
        runner.invoke(app, ["ablate", "init", "study", "--metric", "win"])
        result = runner.invoke(app, ["ablate", "add", "study", "temperature=0"])

        assert result.exit_code == 0
        assert "Added delta" in result.stdout

        # Verify delta was added
        whirr_dir = whirr_project / ".whirr"
        session = load_session_by_name("study", whirr_dir)
        assert "temperature" in session.deltas
        assert session.deltas["temperature"]["temperature"] == 0

    def test_add_multiple_params(self, whirr_project):
        """Test adding delta with multiple params."""
        runner.invoke(app, ["ablate", "init", "study", "--metric", "win"])
        result = runner.invoke(
            app,
            ["ablate", "add", "study", "lr=0.001", "batch_size=64", "--name", "high-lr"],
        )

        assert result.exit_code == 0

        whirr_dir = whirr_project / ".whirr"
        session = load_session_by_name("study", whirr_dir)
        assert "high-lr" in session.deltas
        assert session.deltas["high-lr"]["lr"] == 0.001
        assert session.deltas["high-lr"]["batch_size"] == 64

    def test_add_file_value(self, whirr_project):
        """Test adding delta with @file syntax."""
        # Create a test file
        prompt_file = whirr_project / "prompt.txt"
        prompt_file.write_text("You are a helpful assistant.\n")

        runner.invoke(app, ["ablate", "init", "study", "--metric", "win"])
        result = runner.invoke(
            app,
            ["ablate", "add", "study", "system=@prompt.txt"],
        )

        assert result.exit_code == 0

        whirr_dir = whirr_project / ".whirr"
        session = load_session_by_name("study", whirr_dir)
        assert "system" in session.deltas
        file_val = session.deltas["system"]["system"]
        assert isinstance(file_val, FileValue)
        assert file_val.path == "prompt.txt"
        assert file_val.text == "You are a helpful assistant.\n"

    def test_add_file_preserves_whitespace(self, whirr_project):
        """Test that @file preserves leading/trailing whitespace."""
        prompt_file = whirr_project / "prompt.txt"
        prompt_file.write_text("\n\nHello\n\n")

        runner.invoke(app, ["ablate", "init", "study", "--metric", "win"])
        runner.invoke(app, ["ablate", "add", "study", "system=@prompt.txt"])

        whirr_dir = whirr_project / ".whirr"
        session = load_session_by_name("study", whirr_dir)
        assert session.deltas["system"]["system"].text == "\n\nHello\n\n"


class TestAblateRunCommand:
    """Tests for whirr ablate run."""

    def test_run_dry_run(self, whirr_project):
        """Test dry run shows job preview."""
        runner.invoke(app, ["ablate", "init", "study", "--metric", "win"])
        runner.invoke(app, ["ablate", "add", "study", "temperature=0"])

        # Note: options must come before positional args due to allow_interspersed_args=False
        result = runner.invoke(
            app,
            [
                "ablate",
                "run",
                "--dry-run",
                "--replicates",
                "3",
                "study",
                "--",
                "python",
                "eval.py",
                "--seed",
                "{{seed}}",
                "--cfg",
                "{{cfg_path}}",
            ],
        )

        assert result.exit_code == 0
        assert "Dry run" in result.stdout
        assert "6 jobs" in result.stdout  # 2 conditions x 3 replicates

    def test_run_no_command_fails(self, whirr_project):
        """Test run without command fails."""
        runner.invoke(app, ["ablate", "init", "study", "--metric", "win"])
        runner.invoke(app, ["ablate", "add", "study", "temperature=0"])

        result = runner.invoke(app, ["ablate", "run", "study"])

        assert result.exit_code == 1
        assert "No command provided" in result.stdout

    def test_run_no_deltas_fails(self, whirr_project):
        """Test run without deltas fails."""
        runner.invoke(app, ["ablate", "init", "study", "--metric", "win"])

        result = runner.invoke(
            app,
            ["ablate", "run", "study", "--", "echo", "test"],
        )

        assert result.exit_code == 1
        assert "No deltas added" in result.stdout

    def test_run_generates_configs(self, whirr_project):
        """Test that run generates config files."""
        runner.invoke(app, ["ablate", "init", "study", "--metric", "win"])
        runner.invoke(app, ["ablate", "add", "study", "temperature=0"])

        # Run with actual submission (will create configs)
        result = runner.invoke(
            app,
            [
                "ablate",
                "run",
                "study",
                "--replicates",
                "2",
                "--",
                "echo",
                "{{seed}}",
                "{{cfg_path}}",
            ],
        )

        assert result.exit_code == 0

        # Check configs were created
        whirr_dir = whirr_project / ".whirr"
        session = load_session_by_name("study", whirr_dir)
        configs_dir = whirr_dir / "ablations" / session.session_id / "configs"

        assert (configs_dir / "baseline-0.json").exists()
        assert (configs_dir / "baseline-1.json").exists()
        assert (configs_dir / "temperature-0.json").exists()
        assert (configs_dir / "temperature-1.json").exists()

        # Check config content
        with open(configs_dir / "temperature-0.json") as f:
            cfg = json.load(f)
        assert "__ablate__" in cfg
        assert cfg["__ablate__"]["condition"] == "temperature"
        assert cfg["temperature"] == 0


class TestAblateRankCommand:
    """Tests for whirr ablate rank."""

    def test_rank_no_runs(self, whirr_project):
        """Test rank with no runs fails gracefully."""
        runner.invoke(app, ["ablate", "init", "study", "--metric", "win"])
        runner.invoke(app, ["ablate", "add", "study", "temperature=0"])

        result = runner.invoke(app, ["ablate", "rank", "study"])

        assert result.exit_code == 1
        assert "No runs recorded" in result.stdout
