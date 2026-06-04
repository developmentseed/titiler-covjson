"""Shared test fixtures for titiler-covjson."""

from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel

_M = TypeVar("_M", bound=BaseModel)


def parse(cls: type[_M], data: dict[str, Any]) -> _M:
    return cls.model_validate_json(json.dumps(data), strict=True)


def roundtrip(cls: type[BaseModel], data: dict[str, Any]) -> dict[str, Any]:
    assert isinstance(obj := json.loads(parse(cls, data).model_dump_json()), dict)
    return obj


def roundtrip_is_stable(cls: type[BaseModel], data: dict[str, Any]) -> bool:
    first = roundtrip(cls, data)
    second = roundtrip(cls, first)
    return first == second
