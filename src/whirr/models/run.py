# Copyright (c) Syntropy Systems
"""Pydantic models for run metadata and metrics."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Optional, Union, cast

from pydantic import Field, model_serializer, model_validator
from typing_extensions import override

from .base import ExtraAllowModel, JSONValue, WhirrBaseModel

if TYPE_CHECKING:
    from collections.abc import Callable, ItemsView, KeysView

    from pydantic.main import IncEx


class KeyValuePayload(WhirrBaseModel):
    """Wrapper for arbitrary JSON object payloads."""

    values: dict[str, JSONValue] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _wrap_values(cls, data: object) -> object:
        if isinstance(data, KeyValuePayload):
            return data
        if isinstance(data, dict) and "values" not in data:
            return {"values": cast("dict[str, JSONValue]", data)}
        return cast("object", data)

    @model_serializer(mode="plain")
    def _serialize(self) -> dict[str, JSONValue]:
        return self.values

    def items(self) -> ItemsView[str, JSONValue]:
        """Return the mapping's items view."""
        return self.values.items()

    def keys(self) -> KeysView[str]:
        """Return the mapping's keys view."""
        return self.values.keys()

    def get(self, key: str, default: Optional[JSONValue] = None) -> Optional[JSONValue]:
        """Return a value by key or the provided default."""
        return self.values.get(key, default)


class RunConfig(KeyValuePayload):
    """Run configuration payload."""


class RunSummary(KeyValuePayload):
    """Run summary metrics payload."""


class GitInfo(WhirrBaseModel):
    """Captured git repository information."""

    commit: str
    short_hash: str
    branch: str
    dirty: bool
    remote: Optional[str] = None


class RunMeta(WhirrBaseModel):
    """Run metadata stored in meta.json."""

    id: str
    name: str
    tags: list[str] = Field(default_factory=list)
    started_at: str
    finished_at: Optional[str] = None
    status: str
    summary: Optional[RunSummary] = None
    config_file: str
    metrics_file: str
    artifacts_dir: str
    git: Optional[GitInfo] = None
    git_file: Optional[str] = None
    requirements_file: Optional[str] = None
    pip_packages_count: Optional[int] = None


class RunMetricRecord(ExtraAllowModel):
    """Metric record from metrics.jsonl."""

    idx: Optional[int] = Field(default=None, alias="_idx")
    timestamp: Optional[str] = Field(default=None, alias="_timestamp")
    step: Optional[int] = None

    @override
    def model_dump(
        self,
        *,
        mode: str = "python",
        include: Optional[IncEx] = None,
        exclude: Optional[IncEx] = None,
        context: Optional[object] = None,
        by_alias: Optional[bool] = None,
        exclude_unset: bool = False,
        exclude_defaults: bool = False,
        exclude_none: Optional[bool] = None,
        exclude_computed_fields: bool = False,
        round_trip: bool = False,
        warnings: Union[bool, Literal["none", "warn", "error"]] = True,
        fallback: Optional[Callable[[object], object]] = None,
        serialize_as_any: bool = False,
    ) -> dict[str, JSONValue]:
        if exclude_none is None:
            exclude_none = True
        return cast(
            "dict[str, JSONValue]",
            super().model_dump(
                mode=mode,
                include=include,
                exclude=exclude,
                context=context,
                by_alias=by_alias,
                exclude_unset=exclude_unset,
                exclude_defaults=exclude_defaults,
                exclude_none=exclude_none,
                exclude_computed_fields=exclude_computed_fields,
                round_trip=round_trip,
                warnings=warnings,
                fallback=fallback,
                serialize_as_any=serialize_as_any,
            ),
        )


class SystemMetricRecord(ExtraAllowModel):
    """Metric record from system.jsonl."""

    timestamp: str = Field(alias="_timestamp")
    cpu_percent: Optional[float] = None
    memory_used_gb: Optional[float] = None
    memory_total_gb: Optional[float] = None
    gpu_util: Optional[float] = None
    gpu_mem_used_gb: Optional[float] = None
    gpu_mem_total_gb: Optional[float] = None
    gpu_name: Optional[str] = None


class ArtifactRecord(WhirrBaseModel):
    """Artifact metadata for a run."""

    path: str
    size: int
    modified: str
