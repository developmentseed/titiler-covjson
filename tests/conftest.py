"""Shared test fixtures and helpers for titiler-covjson."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

import jsonschema
import pytest
from pydantic import BaseModel

_M = TypeVar("_M", bound=BaseModel)

# Vendored CoverageJSON JSON Schema (OGC 21-069r2), loaded once.
SCHEMA: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "schemas" / "coveragejson.json").read_text()
)


@pytest.fixture(scope="session")
def covjson_schema() -> dict[str, Any]:
    """Return the vendored CoverageJSON JSON Schema as a dict.

    Returns:
        dict[str, Any]: The parsed CoverageJSON JSON Schema (OGC 21-069r2).
    """
    return SCHEMA


def validate_covjson(instance: dict[str, Any], definition: str | None = None) -> None:
    """Validate an instance against the CoverageJSON schema or a named definition.

    When ``definition`` is given (e.g. ``"domain"``, ``"ndArray"``, ``"parameter"``),
    the instance is validated against ``#/definitions/<definition>`` while retaining the
    full ``definitions`` block for ``$ref`` resolution. Otherwise the instance is
    validated against the root schema (a full ``Coverage`` or ``CoverageCollection``
    document).
    """
    schema = (
        SCHEMA
        if definition is None
        else {
            "$ref": f"#/definitions/{definition}",
            "definitions": SCHEMA["definitions"],
        }
    )
    jsonschema.validate(instance, schema)


def assert_schema_valid(model: BaseModel, definition: str | None = None) -> None:
    """Serialise a model and validate it against the CoverageJSON schema.

    ``model_dump_json(exclude_none=True)`` is used so that optional members serialised
    as ``null`` are omitted: the schema types those members as arrays/objects/strings
    and rejects an explicit ``null``. Null *elements* inside ``values`` arrays (i.e.,
    missing data) are list items, not members, so they are preserved and remain
    schema-valid.
    """
    validate_covjson(json.loads(model.model_dump_json(exclude_none=True)), definition)


def parse(cls: type[_M], data: dict[str, Any]) -> _M:
    return cls.model_validate_json(json.dumps(data), strict=True)


def roundtrip(cls: type[BaseModel], data: dict[str, Any]) -> dict[str, Any]:
    assert isinstance(obj := json.loads(parse(cls, data).model_dump_json()), dict)
    return obj


def roundtrip_is_stable(cls: type[BaseModel], data: dict[str, Any]) -> bool:
    first = roundtrip(cls, data)
    second = roundtrip(cls, first)
    return first == second
