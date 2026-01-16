# Copyright (c) Syntropy Systems
"""Pydantic models for database records."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Union, cast

from pydantic import Field, TypeAdapter, field_validator

from .base import WhirrBaseModel
from .run import RunConfig, RunSummary

_LIST_STR_ADAPTER = TypeAdapter(list[str])


class JobRecord(WhirrBaseModel):
    """Database job record."""

    id: int
    name: Optional[str] = None
    command_argv: list[str] = Field(default_factory=list)
    workdir: str
    config: Optional[RunConfig] = None
    status: str
    tags: Optional[list[str]] = None
    attempt: int = 1
    parent_job_id: Optional[int] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    worker_id: Optional[str] = None
    heartbeat_at: Optional[str] = None
    lease_expires_at: Optional[str] = None
    pid: Optional[int] = None
    pgid: Optional[int] = None
    exit_code: Optional[int] = None
    error_message: Optional[str] = None
    cancel_requested_at: Optional[str] = None
    run_id: Optional[str] = None

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
    def _parse_config(cls, value: object) -> Optional[RunConfig]:
        if value is None:
            return None
        if isinstance(value, str):
            return RunConfig.model_validate_json(value)
        return RunConfig.model_validate(value)

    @field_validator("tags", mode="before")
    @classmethod
    def _parse_tags(cls, value: object) -> Optional[list[str]]:
        if value is None:
            return None
        if isinstance(value, str):
            return _LIST_STR_ADAPTER.validate_json(value)
        return cast("list[str]", value)


class RunRecord(WhirrBaseModel):
    """Database run record."""

    id: str
    job_id: Optional[int] = None
    name: Optional[str] = None
    config: Optional[RunConfig] = None
    tags: Optional[list[str]] = None
    status: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    summary: Optional[RunSummary] = None
    git_hash: Optional[str] = None
    git_dirty: Optional[int] = None
    hostname: Optional[str] = None
    run_dir: Optional[str] = None

    @field_validator("config", mode="before")
    @classmethod
    def _parse_config(cls, value: object) -> Optional[RunConfig]:
        if value is None:
            return None
        if isinstance(value, str):
            return RunConfig.model_validate_json(value)
        return RunConfig.model_validate(value)

    @field_validator("tags", mode="before")
    @classmethod
    def _parse_tags(cls, value: object) -> Optional[list[str]]:
        if value is None:
            return None
        if isinstance(value, str):
            return _LIST_STR_ADAPTER.validate_json(value)
        return cast("list[str]", value)

    @field_validator("summary", mode="before")
    @classmethod
    def _parse_summary(cls, value: object) -> Optional[RunSummary]:
        if value is None:
            return None
        if isinstance(value, str):
            return RunSummary.model_validate_json(value)
        return RunSummary.model_validate(value)


class WorkerRecord(WhirrBaseModel):
    """Database worker record."""

    id: str
    pid: Optional[int] = None
    hostname: Optional[str] = None
    gpu_index: Optional[int] = None
    gpu_id: Optional[int] = None
    status: str
    current_job_id: Optional[int] = None
    started_at: Optional[str] = None
    last_heartbeat: Optional[str] = None
    heartbeat_at: Optional[str] = None

    @field_validator("started_at", "last_heartbeat", "heartbeat_at", mode="before")
    @classmethod
    def _parse_datetime(cls, value: object) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        return cast(str, value)
