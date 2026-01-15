# Copyright (c) Syntropy Systems
"""Shared Pydantic model helpers for whirr."""

from __future__ import annotations

from typing import ClassVar, Union

from pydantic import BaseModel, ConfigDict, JsonValue
from typing_extensions import TypeAlias

JSONPrimitive: TypeAlias = Union[str, int, float, bool, None]
JSONValue: TypeAlias = JsonValue
JSONObject: TypeAlias = dict[str, JSONValue]


class WhirrBaseModel(BaseModel):
    """Base model with shared config for whirr schemas."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )


class ExtraAllowModel(BaseModel):
    """Base model that preserves extra fields for flexible schemas."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="allow",
        populate_by_name=True,
    )
