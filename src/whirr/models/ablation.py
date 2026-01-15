# Copyright (c) Syntropy Systems
"""Pydantic models for ablation session data."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

from pydantic import (
    Field,
    PrivateAttr,
    TypeAdapter,
    field_validator,
    model_serializer,
    model_validator,
)
from typing_extensions import TypeAlias, override

from .base import JSONValue, WhirrBaseModel

if TYPE_CHECKING:
    from pathlib import Path

_ENTRIES_ADAPTER = TypeAdapter(dict[str, str])

class FileValue(WhirrBaseModel):
    """File reference with inlined content."""

    path: str
    text: str


ConfigValue: TypeAlias = JSONValue | FileValue


class AblationRunResult(WhirrBaseModel):
    """Result from a single replicate run."""

    run_id: str
    job_id: int
    condition: str
    replicate: int
    seed: int
    metric_value: float | None = None
    status: str = "queued"
    outcome: str | None = None


class AblationSession(WhirrBaseModel):
    """Ablation study session."""

    session_id: str
    name: str
    metric: str
    seed_base: int
    baseline: dict[str, ConfigValue] = Field(default_factory=dict)
    deltas: dict[str, dict[str, ConfigValue]] = Field(default_factory=dict)
    replicates: int = 20
    runs: list[AblationRunResult] = Field(default_factory=list)
    created_at: str = ""

    _path: Path | None = PrivateAttr(default=None)

    @field_validator("baseline", mode="before")
    @classmethod
    def _coerce_baseline_values(cls, value: object) -> dict[str, ConfigValue]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            return cast("dict[str, ConfigValue]", value)
        value_dict = cast("dict[str, object]", value)
        converted: dict[str, ConfigValue] = {}
        for key, item in value_dict.items():
            if isinstance(item, dict):
                item_dict = cast("dict[str, object]", item)
                if {"path", "text"}.issubset(item_dict.keys()):
                    converted[key] = FileValue.model_validate(item_dict)
                    continue
            converted[key] = cast("ConfigValue", item)
        return converted

    @field_validator("deltas", mode="before")
    @classmethod
    def _coerce_delta_values(cls, value: object) -> dict[str, dict[str, ConfigValue]]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            return cast("dict[str, dict[str, ConfigValue]]", value)
        value_dict = cast("dict[str, object]", value)
        converted: dict[str, dict[str, ConfigValue]] = {}
        for delta_name, delta_values in value_dict.items():
            if not isinstance(delta_values, dict):
                converted[delta_name] = cast("dict[str, ConfigValue]", delta_values)
                continue
            delta_values_dict = cast("dict[str, object]", delta_values)
            delta_converted: dict[str, ConfigValue] = {}
            for key, item in delta_values_dict.items():
                if isinstance(item, dict):
                    item_dict = cast("dict[str, object]", item)
                    if {"path", "text"}.issubset(item_dict.keys()):
                        delta_converted[key] = FileValue.model_validate(item_dict)
                        continue
                delta_converted[key] = cast("ConfigValue", item)
            converted[delta_name] = delta_converted
        return converted

    @override
    def model_post_init(self, __context: object, /) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    @classmethod
    def load(cls, path: Path) -> AblationSession:
        """Load a session from JSON file."""
        session = cls.model_validate_json(path.read_text())
        session.set_path(path)
        return session

    def save(self) -> None:
        """Save session to JSON file."""
        if self._path is None:
            msg = "Session path not set"
            raise ValueError(msg)

        self._path.parent.mkdir(parents=True, exist_ok=True)
        _ = self._path.write_text(self.model_dump_json(indent=2))

    def set_path(self, path: Path) -> None:
        """Set the session file path for persistence."""
        self._path = path

    def get_condition_names(self) -> list[str]:
        """Return all condition names: baseline + delta names."""
        return ["baseline", *list(self.deltas.keys())]

    def get_seed(self, replicate: int) -> int:
        """Get deterministic seed for a replicate."""
        return self.seed_base + replicate


class AblationIndex(WhirrBaseModel):
    """Index mapping session name to session_id."""

    entries: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coerce_root(cls, value: object) -> object:
        if value is None:
            return {"entries": {}}
        if isinstance(value, dict):
            value_dict = cast("dict[str, object]", value)
            if "entries" not in value_dict:
                return {"entries": cast("dict[str, str]", value_dict)}
            return {"entries": cast("dict[str, str]", value_dict["entries"])}
        return value

    @field_validator("entries", mode="before")
    @classmethod
    def _wrap_entries(cls, value: object) -> dict[str, str]:
        if value is None:
            return {}
        if isinstance(value, dict) and "entries" in value:
            return cast("dict[str, str]", value["entries"])
        if isinstance(value, dict):
            return cast("dict[str, str]", value)
        if isinstance(value, str):
            return _ENTRIES_ADAPTER.validate_json(value)
        return cast("dict[str, str]", value)

    @model_serializer(mode="plain")
    def _serialize(self) -> dict[str, str]:
        return self.entries
