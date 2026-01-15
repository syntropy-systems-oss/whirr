# Copyright (c) Syntropy Systems
"""Pydantic models for database records."""

from __future__ import annotations

from typing import cast

from pydantic import Field, TypeAdapter, field_validator

from .base import WhirrBaseModel
from .run import RunConfig, RunSummary

_LIST_STR_ADAPTER = TypeAdapter(list[str])


class JobRecord(WhirrBaseModel):
    """Database job record."""

    id: int
    name: str | None = None
    command_argv: list[str] = Field(default_factory=list)
    workdir: str
    config: RunConfig | None = None
    status: str
    tags: list[str] | None = None
    attempt: int = 1
    parent_job_id: int | None = None
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    worker_id: str | None = None
    heartbeat_at: str | None = None
    lease_expires_at: str | None = None
    pid: int | None = None
    pgid: int | None = None
    exit_code: int | None = None
    error_message: str | None = None
    cancel_requested_at: str | None = None
    run_id: str | None = None

    @field_validator("command_argv", mode="before")
    @classmethod
    def _parse_command_argv(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return _LIST_STR_ADAPTER.validate_json(value)
        return cast("list[str]", value)

    @field_validator("config", mode="before")
    @classmethod
    def _parse_config(cls, value: object) -> RunConfig | None:
        if value is None:
            return None
        if isinstance(value, str):
            return RunConfig.model_validate_json(value)
        return RunConfig.model_validate(value)

    @field_validator("tags", mode="before")
    @classmethod
    def _parse_tags(cls, value: object) -> list[str] | None:
        if value is None:
            return None
        if isinstance(value, str):
            return _LIST_STR_ADAPTER.validate_json(value)
        return cast("list[str]", value)


class RunRecord(WhirrBaseModel):
    """Database run record."""

    id: str
    job_id: int | None = None
    name: str | None = None
    config: RunConfig | None = None
    tags: list[str] | None = None
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float | None = None
    summary: RunSummary | None = None
    git_hash: str | None = None
    git_dirty: int | None = None
    hostname: str | None = None
    run_dir: str | None = None

    @field_validator("config", mode="before")
    @classmethod
    def _parse_config(cls, value: object) -> RunConfig | None:
        if value is None:
            return None
        if isinstance(value, str):
            return RunConfig.model_validate_json(value)
        return RunConfig.model_validate(value)

    @field_validator("tags", mode="before")
    @classmethod
    def _parse_tags(cls, value: object) -> list[str] | None:
        if value is None:
            return None
        if isinstance(value, str):
            return _LIST_STR_ADAPTER.validate_json(value)
        return cast("list[str]", value)

    @field_validator("summary", mode="before")
    @classmethod
    def _parse_summary(cls, value: object) -> RunSummary | None:
        if value is None:
            return None
        if isinstance(value, str):
            return RunSummary.model_validate_json(value)
        return RunSummary.model_validate(value)


class WorkerRecord(WhirrBaseModel):
    """Database worker record."""

    id: str
    pid: int | None = None
    hostname: str | None = None
    gpu_index: int | None = None
    gpu_id: int | None = None
    status: str
    current_job_id: int | None = None
    started_at: str | None = None
    last_heartbeat: str | None = None
    heartbeat_at: str | None = None
