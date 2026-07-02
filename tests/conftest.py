"""Shared test fixtures and helpers for titiler-covjson."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

import jsonschema
import numpy as np
import pyproj
import pytest
import rasterio
import rasterio.transform
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from titiler.core.errors import DEFAULT_STATUS_CODES, add_exception_handlers

from titiler_covjson.factory import DEFAULT_MAX_SIZE, CovJSONFactory

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


@pytest.fixture(scope="session")
def cog_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Write a 16x16 2-band EPSG:4326 COG with a nodata sentinel on band 2.

    Band 1 is a value ramp; band 2 copies it but sets the top-left pixel to the
    nodata sentinel, so masked (``null``) output can be exercised. Session-scoped:
    written once and read by the endpoint integration tests.

    Returns:
        str: Filesystem path to the written COG.
    """
    path = str(tmp_path_factory.mktemp("data") / "sample.tif")
    _write_cog(path, width=16, height=16)

    return path


@pytest.fixture(scope="session")
def tiny_cog_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Write a 2x2 2-band EPSG:4326 raster with a nodata sentinel on band 2.

    Deliberately tiny so a full-extent read yields a coverage small enough to
    assert against a complete expected document. Same layout as ``cog_path`` (a
    band-1 ramp with band-2 top-left nodata), just 2x2. Session-scoped.

    Returns:
        str: Filesystem path to the written raster.
    """
    path = str(tmp_path_factory.mktemp("data") / "tiny.tif")
    _write_cog(path, width=2, height=2)

    return path


@pytest.fixture
def client() -> TestClient:
    """Return a TestClient over an app mounting a default CovJSONFactory.

    Returns:
        TestClient: Client bound to the mounted app.
    """
    return _make_client()


@pytest.fixture
def small_default_client() -> TestClient:
    """Return a TestClient over a factory with a tiny downsampling default.

    Returns:
        TestClient: Client whose factory uses ``default_max_size=4``.
    """
    return _make_client(default_max_size=4)


def validate_covjson(instance: dict[str, Any], definition: str | None = None) -> None:
    """Validate an instance against the CoverageJSON schema or a named definition.

    When ``definition`` is given (e.g., ``"domain"``, ``"ndArray"``, ``"parameter"``),
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


def _make_client(
    *,
    default_max_size: int = DEFAULT_MAX_SIZE,
    max_cells: int = DEFAULT_MAX_SIZE**2,
) -> TestClient:
    """Build a TestClient over an app mounting a CovJSONFactory.

    Installs titiler's exception handlers so reader/dataset failures and
    ``BadRequestError`` map to JSON responses with the correct status codes
    (without them such errors surface as an unhandled 500).

    Args:
        default_max_size: The factory's downsampling default.
        max_cells: The factory's hard cell-count ceiling.

    Returns:
        TestClient: Client bound to the mounted app.
    """
    factory = CovJSONFactory(default_max_size=default_max_size, max_cells=max_cells)
    app = FastAPI()
    app.include_router(factory.router)
    add_exception_handlers(app, DEFAULT_STATUS_CODES)

    return TestClient(app)


def _write_cog(path: str, *, width: int, height: int) -> None:
    """Write a 2-band EPSG:4326 GeoTIFF: a band-1 ramp and a band-2 nodata copy.

    Band 1 is ``0 .. width*height-1`` reshaped row-major; band 2 copies it and
    sets the top-left pixel to the nodata sentinel. The extent is fixed at
    ``(-10, -5, 10, 5)``, so pixel size scales with the requested dimensions.

    Args:
        path: Destination filesystem path.
        width: Raster width in pixels.
        height: Raster height in pixels.
    """
    bounds = (-10.0, -5.0, 10.0, 5.0)
    nodata = -9999.0
    transform = rasterio.transform.from_bounds(*bounds, width, height)
    band1 = np.arange(width * height, dtype="float32").reshape(height, width)
    band2 = band1.copy()
    band2[0, 0] = nodata
    profile = {
        "driver": "GTiff",
        "dtype": "float32",
        "count": 2,
        "width": width,
        "height": height,
        "crs": pyproj.CRS.from_epsg(4326),
        "transform": transform,
        "nodata": nodata,
    }

    # GeoTIFF tiling requires block sizes that are multiples of 16; tile when the
    # dimensions allow it, else fall back to a striped layout (fine for tests).
    if width % 16 == 0 and height % 16 == 0:
        profile |= {"tiled": True, "blockxsize": width, "blockysize": height}

    with rasterio.open(path, "w", **profile) as dst:
        dst.write(band1, 1)
        dst.write(band2, 2)
        dst.set_band_description(1, "red")
        dst.set_band_description(2, "nir")
