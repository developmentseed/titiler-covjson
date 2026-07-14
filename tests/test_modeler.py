"""Tests for the modeler conversion functions."""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pytest
import rasterio
from conftest import assert_schema_valid
from covjson_pydantic.coverage import Coverage
from covjson_pydantic.domain import CompactAxis, DomainType, ValuesAxis
from covjson_pydantic.ndarray import NdArrayFloat, NdArrayInt, NdArrayStr
from covjson_pydantic.unit import Symbol

from titiler_covjson.input import (
    BandInfo,
    GridInput,
    PointInput,
    Polygon,
    PolygonInput,
    Position,
)
from titiler_covjson.modeler import to_coverage


def _masked(
    values: Any,
    *,
    mask: Any = False,
    dtype: Any = None,
) -> np.ma.MaskedArray[Any, np.dtype[Any]]:
    """Build a masked array for tests.

    Centralizes masked-array construction so the tests share one spelling of the
    ``values`` / ``mask`` / ``dtype`` call.

    Args:
        values: Array values as a nested sequence or an ndarray.
        mask: Boolean mask, or ``False`` for no masked entries.
        dtype: Optional dtype for the data.

    Returns:
        np.ma.MaskedArray[Any, np.dtype[Any]]: The masked array.
    """
    return np.ma.array(values, mask=mask, dtype=dtype)


def _grid_input(
    data: np.ma.MaskedArray[Any, np.dtype[Any]],
    *,
    bands: tuple[BandInfo, ...] = (),
    crs: rasterio.CRS | None = None,
) -> GridInput:
    """Build a gridded GridInput for modeler tests.

    Args:
        data: The masked data array, shaped ``(bands, height, width)``.
        bands: Per-band metadata; empty to let GridInput synthesize it.
        crs: CRS for the input; defaults to EPSG:4326 (WGS84).

    Returns:
        GridInput: A gridded input over a fixed WGS84 bbox.
    """
    return GridInput(
        data=data,
        bounds=(-10.0, -5.0, 10.0, 5.0),
        crs=crs or rasterio.CRS.from_epsg(4326),
        bands=bands,
    )


class TestGridCoverage:
    """Conversion of gridded (raster) inputs to a Grid Coverage."""

    def test_single_band_float_grid_is_schema_valid(self) -> None:
        """A single-band float grid converts to a schema-valid Grid Coverage."""
        data = _masked([[[1.0, 2.0], [3.0, 4.0]]], dtype="float32")
        cov = to_coverage(_grid_input(data, bands=(BandInfo("b1", unit="mm"),)))

        assert isinstance(cov, Coverage)
        assert cov.domain.domainType == DomainType.grid

        # Axes carry cell *centers*, inset half a cell from the bounds edges:
        # x runs west..east over `width` columns, y runs north..south over
        # `height` rows (raster row 0 is the north edge). Bounds are
        # -10..10 (x) over 2 cols -> centers -5, 5; 5..-5 (y) over 2 rows ->
        # centers 2.5, -2.5.
        assert isinstance(cov.domain.axes.x, CompactAxis)
        assert cov.domain.axes.x.start == -5.0
        assert cov.domain.axes.x.stop == 5.0
        assert cov.domain.axes.x.num == 2
        assert isinstance(cov.domain.axes.y, CompactAxis)
        assert cov.domain.axes.y.start == 2.5
        assert cov.domain.axes.y.stop == -2.5
        assert cov.domain.axes.y.num == 2

        assert set(cov.ranges) == {"b1"}
        nd = cov.ranges["b1"]
        assert isinstance(nd, NdArrayFloat)
        assert nd.axisNames == ["y", "x"]
        assert nd.shape == [2, 2]
        assert nd.values == [1.0, 2.0, 3.0, 4.0]

        assert_schema_valid(cov)

    @pytest.mark.parametrize(
        ("shape", "axis", "expected"),
        [
            # single column -> x collapses to the west/east midpoint
            ((1, 2, 1), "x", (0.0, 0.0, 1)),
            # single row -> y collapses to the north/south midpoint
            ((1, 1, 2), "y", (0.0, 0.0, 1)),
        ],
        ids=("single-column", "single-row"),
    )
    def test_single_cell_axis_collapses_to_midpoint(
        self,
        shape: tuple[int, ...],
        axis: str,
        expected: tuple[float, float, int],
    ) -> None:
        """A 1-cell axis's single center is the bounds midpoint.

        With one cell the center sits half a cell in from each edge, i.e., at
        the bounds midpoint, so ``start == stop`` (here 0.0, the center of the
        symmetric -10..10 / -5..5 bounds).
        """
        data = _masked(np.zeros(shape, dtype="float32"))
        cov = to_coverage(_grid_input(data))

        compact = getattr(cov.domain.axes, axis)
        assert isinstance(compact, CompactAxis)
        assert (compact.start, compact.stop, compact.num) == expected

    def test_multiple_bands_keep_order_and_names(self) -> None:
        """Each band yields a parameter and range keyed by its name, in order."""
        data = _masked(np.arange(2 * 2 * 3, dtype="float32").reshape(2, 2, 3))
        cov = to_coverage(_grid_input(data, bands=(BandInfo("red"), BandInfo("nir"))))

        assert cov.parameters is not None
        assert list(cov.parameters.root) == ["red", "nir"]
        assert list(cov.ranges) == ["red", "nir"]
        red = cov.ranges["red"]
        assert isinstance(red, NdArrayFloat)
        assert red.shape == [2, 3]
        assert_schema_valid(cov)

    def test_integer_nodata_serializes_as_null(self) -> None:
        """Masked integer entries serialize as JSON null in the range values."""
        data = _masked(
            [[[10, 20], [30, 40]]],
            mask=[[[False, True], [False, False]]],
            dtype="int16",
        )
        cov = to_coverage(_grid_input(data, bands=(BandInfo("b1", dtype="int16"),)))

        nd = cov.ranges["b1"]
        assert isinstance(nd, NdArrayInt)
        assert nd.values == [10, None, 30, 40]
        dumped = json.loads(cov.model_dump_json(exclude_none=True))
        assert dumped["ranges"]["b1"]["values"] == [10, None, 30, 40]
        assert_schema_valid(cov)

    def test_float_nodata_serializes_as_null(self) -> None:
        """Masked float entries serialize as JSON null (NaN in the model)."""
        data = _masked(
            [[[1.0, 2.0], [3.0, 4.0]]],
            mask=[[[False, False], [True, False]]],
            dtype="float32",
        )
        cov = to_coverage(_grid_input(data, bands=(BandInfo("b1"),)))

        dumped = json.loads(cov.model_dump_json(exclude_none=True))
        assert dumped["ranges"]["b1"]["values"] == [1.0, 2.0, None, 4.0]
        assert_schema_valid(cov)

    def test_string_dtype_produces_string_range(self) -> None:
        """A string-dtype band produces an NdArrayStr range."""
        data = _masked([[["a", "b"], ["c", "d"]]], dtype=np.dtype("U1"))
        cov = to_coverage(
            _grid_input(data, bands=(BandInfo("b1", dtype=np.dtype("U1")),))
        )

        nd = cov.ranges["b1"]
        assert isinstance(nd, NdArrayStr)
        assert nd.values == ["a", "b", "c", "d"]
        assert_schema_valid(cov)

    def test_resolved_unit_is_attached(self) -> None:
        """A resolvable UCUM code becomes the parameter's unit."""
        data = _masked([[[1.0]]], dtype="float32")
        cov = to_coverage(
            _grid_input(data, bands=(BandInfo("b1", description="precip", unit="mm"),))
        )

        assert cov.parameters is not None
        param = cov.parameters.root["b1"]
        assert param.observedProperty.label == {"en": "precip"}
        assert param.unit is not None
        assert isinstance(param.unit.symbol, Symbol)
        assert param.unit.symbol.value == "mm"
        assert_schema_valid(cov)

    def test_empty_and_unresolvable_units_omit_unit(self) -> None:
        """No unit code, or an invalid one, leaves the parameter without a unit."""
        data = _masked(np.zeros((2, 1, 1), dtype="float32"))
        cov = to_coverage(
            _grid_input(
                data,
                bands=(BandInfo("plain"), BandInfo("bogus", unit="furlongs")),
            )
        )

        assert cov.parameters is not None
        assert cov.parameters.root["plain"].unit is None
        assert cov.parameters.root["bogus"].unit is None
        assert_schema_valid(cov)

    def test_empty_bands_synthesizes_identities(self) -> None:
        """With no band metadata, generic b1, b2, ... identities are synthesized."""
        data = _masked(np.zeros((3, 2, 2), dtype="float32"))
        cov = to_coverage(_grid_input(data))

        assert cov.parameters is not None
        assert list(cov.parameters.root) == ["b1", "b2", "b3"]
        assert list(cov.ranges) == ["b1", "b2", "b3"]
        assert_schema_valid(cov)

    def test_projected_crs_referencing(self) -> None:
        """A projected CRS yields ProjectedCRS referencing in the domain."""
        data = _masked([[[1.0, 2.0]]], dtype="float32")
        cov = to_coverage(_grid_input(data, crs=rasterio.CRS.from_epsg(32637)))

        assert cov.domain.referencing is not None
        system = cov.domain.referencing[0].system
        assert system.type == "ProjectedCRS"
        assert system.id == "http://www.opengis.net/def/crs/EPSG/0/32637"
        assert_schema_valid(cov)


def _point_input(
    data: np.ma.MaskedArray[Any, np.dtype[Any]],
    *,
    position: Position | None = None,
    bands: tuple[BandInfo, ...] = (),
    crs: rasterio.CRS | None = None,
) -> PointInput:
    """Build a PointInput for modeler tests.

    Args:
        data: The masked data array, shaped ``(bands,)`` (one value per band).
        position: The sampled location; defaults to ``Position(-5.0, 2.5)``.
        bands: Per-band metadata; empty to let PointInput synthesize it.
        crs: CRS for the input; defaults to EPSG:4326 (WGS84).

    Returns:
        PointInput: A point input at a fixed WGS84 location.
    """
    return PointInput(
        data=data,
        position=position or Position(-5.0, 2.5),
        crs=crs or rasterio.CRS.from_epsg(4326),
        bands=bands,
    )


class TestPointCoverage:
    """Conversion of point (single-position) inputs to a Point Coverage."""

    def test_single_band_float_point_is_schema_valid(self) -> None:
        """A single-band float point converts to a schema-valid Point Coverage."""
        data = _masked([1.5], dtype="float32")
        cov = to_coverage(_point_input(data, bands=(BandInfo("b1", unit="mm"),)))

        assert isinstance(cov, Coverage)
        assert cov.domain.domainType == DomainType.point

        # A Point domain carries single-value x/y axes at the sampled location
        # (no z when the position is purely horizontal).
        assert isinstance(cov.domain.axes.x, ValuesAxis)
        assert cov.domain.axes.x.values == [-5.0]
        assert isinstance(cov.domain.axes.y, ValuesAxis)
        assert cov.domain.axes.y.values == [2.5]
        assert cov.domain.axes.z is None

        assert set(cov.ranges) == {"b1"}
        nd = cov.ranges["b1"]
        assert isinstance(nd, NdArrayFloat)
        assert nd.axisNames == []
        assert nd.shape == []
        assert nd.values == [1.5]

        assert_schema_valid(cov)

    def test_masked_float_value_serializes_as_null(self) -> None:
        """A masked float sample serializes as JSON null in the range values."""
        data = _masked([1.5], mask=[True], dtype="float32")
        cov = to_coverage(_point_input(data, bands=(BandInfo("b1"),)))

        dumped = json.loads(cov.model_dump_json(exclude_none=True))
        assert dumped["ranges"]["b1"]["values"] == [None]
        assert_schema_valid(cov)

    def test_multiple_bands_keep_order_and_names(self) -> None:
        """Each band yields a scalar parameter and range keyed by its name."""
        data = _masked([10.0, 20.0], dtype="float32")
        cov = to_coverage(_point_input(data, bands=(BandInfo("red"), BandInfo("nir"))))

        assert cov.parameters is not None
        assert list(cov.parameters.root) == ["red", "nir"]
        assert list(cov.ranges) == ["red", "nir"]
        red = cov.ranges["red"]
        assert isinstance(red, NdArrayFloat)
        assert red.shape == []
        assert red.values == [10.0]
        assert_schema_valid(cov)

    def test_integer_nodata_serializes_as_null(self) -> None:
        """A masked integer sample serializes as JSON null."""
        data = _masked([10, 20], mask=[False, True], dtype="int16")
        cov = to_coverage(
            _point_input(
                data,
                bands=(
                    BandInfo("b1", dtype="int16"),
                    BandInfo("b2", dtype="int16"),
                ),
            )
        )

        nd = cov.ranges["b1"]
        assert isinstance(nd, NdArrayInt)
        assert nd.values == [10]
        dumped = json.loads(cov.model_dump_json(exclude_none=True))
        assert dumped["ranges"]["b2"]["values"] == [None]
        assert_schema_valid(cov)

    def test_string_dtype_produces_string_range(self) -> None:
        """A string-dtype band produces an NdArrayStr scalar range."""
        data = _masked(["a"], dtype=np.dtype("U1"))
        cov = to_coverage(
            _point_input(data, bands=(BandInfo("b1", dtype=np.dtype("U1")),))
        )

        nd = cov.ranges["b1"]
        assert isinstance(nd, NdArrayStr)
        assert nd.values == ["a"]
        assert_schema_valid(cov)

    def test_resolved_unit_is_attached(self) -> None:
        """A resolvable UCUM code becomes the parameter's unit."""
        data = _masked([1.5], dtype="float32")
        cov = to_coverage(
            _point_input(data, bands=(BandInfo("b1", description="precip", unit="mm"),))
        )

        assert cov.parameters is not None
        param = cov.parameters.root["b1"]
        assert param.observedProperty.label == {"en": "precip"}
        assert param.unit is not None
        assert isinstance(param.unit.symbol, Symbol)
        assert param.unit.symbol.value == "mm"
        assert_schema_valid(cov)

    def test_vertical_position_adds_z_axis(self) -> None:
        """A 3-D position adds a single-value z axis, still schema-valid.

        The modeler models an optional vertical coordinate even though the
        endpoint does not yet expose 3-D sampling: the model layer is ready for
        a 3-D backing behind the reader seam.
        """
        data = _masked([1.5], dtype="float32")
        cov = to_coverage(
            _point_input(
                data,
                position=Position(-5.0, 2.5, z=850.0),
                bands=(BandInfo("b1"),),
            )
        )

        assert isinstance(cov.domain.axes.z, ValuesAxis)
        assert cov.domain.axes.z.values == [850.0]
        assert_schema_valid(cov)

    def test_projected_crs_referencing(self) -> None:
        """A projected CRS yields ProjectedCRS referencing in the domain."""
        data = _masked([1.0], dtype="float32")
        cov = to_coverage(_point_input(data, crs=rasterio.CRS.from_epsg(32637)))

        assert cov.domain.referencing is not None
        system = cov.domain.referencing[0].system
        assert system.type == "ProjectedCRS"
        assert system.id == "http://www.opengis.net/def/crs/EPSG/0/32637"
        assert_schema_valid(cov)


_SQUARE = Polygon(
    rings=(((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)),)
)


def _polygon_input(
    data: np.ma.MaskedArray[Any, np.dtype[Any]],
    *,
    geometry: Polygon | None = None,
    bands: tuple[BandInfo, ...] = (),
    crs: rasterio.CRS | None = None,
) -> PolygonInput:
    """Build a PolygonInput for modeler tests.

    Args:
        data: The masked data array, shaped ``(bands,)`` (one reduced scalar per
            band).
        geometry: The polygon; defaults to a 10x10 square with no holes.
        bands: Per-band metadata; empty to let PolygonInput synthesize it.
        crs: CRS for the input; defaults to EPSG:4326 (WGS84).

    Returns:
        PolygonInput: A polygon input over a fixed WGS84 geometry.
    """
    return PolygonInput(
        data=data,
        geometry=geometry or _SQUARE,
        crs=crs or rasterio.CRS.from_epsg(4326),
        bands=bands,
    )


class TestPolygonCoverage:
    """Conversion of polygon (zonal-reduction) inputs to a Polygon Coverage."""

    def test_single_band_polygon_is_schema_valid(self) -> None:
        """A single-band polygon converts to a schema-valid Polygon Coverage."""
        data = _masked([7.5], dtype="float32")
        cov = to_coverage(_polygon_input(data, bands=(BandInfo("b1", unit="mm"),)))

        assert isinstance(cov, Coverage)
        assert cov.domain.domainType == DomainType.polygon

        # A Polygon domain carries a single `composite` axis holding one polygon
        # (the exterior ring plus any holes), tagged dataType "polygon".
        composite = cov.domain.axes.composite
        assert isinstance(composite, ValuesAxis)
        assert composite.dataType == "polygon"
        assert composite.coordinates == ["x", "y"]
        assert len(composite.values) == 1

        assert set(cov.ranges) == {"b1"}
        nd = cov.ranges["b1"]
        assert isinstance(nd, NdArrayFloat)
        assert nd.axisNames == []
        assert nd.shape == []
        assert nd.values == [7.5]

        # The composite axis serializes to the nested values -> polygon -> ring
        # -> vertex structure (one polygon, one closed exterior ring).
        dumped = json.loads(cov.model_dump_json(exclude_none=True))
        assert dumped["domain"]["axes"]["composite"]["values"] == [
            [[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]]]
        ]

        assert_schema_valid(cov)

    def test_polygon_with_hole_is_schema_valid(self) -> None:
        """A polygon with an interior ring (hole) keeps both rings, schema-valid."""
        exterior = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0))
        hole = ((2.0, 2.0), (4.0, 2.0), (4.0, 4.0), (2.0, 4.0), (2.0, 2.0))
        data = _masked([1.0], dtype="float32")
        cov = to_coverage(
            _polygon_input(data, geometry=Polygon(rings=(exterior, hole)))
        )

        dumped = json.loads(cov.model_dump_json(exclude_none=True))
        polygon = dumped["domain"]["axes"]["composite"]["values"][0]
        assert len(polygon) == 2  # exterior + one hole
        assert_schema_valid(cov)

    def test_masked_value_serializes_as_null(self) -> None:
        """A band with no valid pixels (masked scalar) serializes as JSON null."""
        data = _masked([1.5], mask=[True], dtype="float32")
        cov = to_coverage(_polygon_input(data, bands=(BandInfo("b1"),)))

        dumped = json.loads(cov.model_dump_json(exclude_none=True))
        assert dumped["ranges"]["b1"]["values"] == [None]
        assert_schema_valid(cov)

    def test_integer_reduction_produces_integer_range(self) -> None:
        """An integer-typed reduction (e.g. a count) yields an NdArrayInt range."""
        data = _masked([16], dtype="int64")
        cov = to_coverage(_polygon_input(data, bands=(BandInfo("b1", dtype="int64"),)))

        nd = cov.ranges["b1"]
        assert isinstance(nd, NdArrayInt)
        assert nd.values == [16]
        assert_schema_valid(cov)

    def test_multiple_bands_keep_order_and_names(self) -> None:
        """Each band yields a scalar parameter and range keyed by its name."""
        data = _masked([10.0, 20.0], dtype="float32")
        cov = to_coverage(
            _polygon_input(data, bands=(BandInfo("red"), BandInfo("nir")))
        )

        assert cov.parameters is not None
        assert list(cov.parameters.root) == ["red", "nir"]
        assert list(cov.ranges) == ["red", "nir"]
        assert_schema_valid(cov)

    def test_projected_crs_referencing(self) -> None:
        """A projected CRS yields ProjectedCRS referencing in the domain."""
        data = _masked([1.0], dtype="float32")
        cov = to_coverage(_polygon_input(data, crs=rasterio.CRS.from_epsg(32637)))

        assert cov.domain.referencing is not None
        system = cov.domain.referencing[0].system
        assert system.type == "ProjectedCRS"
        assert system.id == "http://www.opengis.net/def/crs/EPSG/0/32637"
        assert_schema_valid(cov)

    def test_lat_first_crs_axis_order_divergence_is_intentional(self) -> None:
        """A latitude-first CRS diverges the composite and referencing orders.

        The ``composite`` axis always lists ``["x", "y"]`` (the vertex storage
        order: longitude/easting then latitude/northing), while ``referencing``
        lists the CRS's declared axis order, which is ``["y", "x"]`` for a
        latitude-first CRS such as EPSG:4326. The two arrays describe different
        things (vertex layout vs CRS axis order) and must stay divergent:
        unifying them would make a strict consumer read a stored ``[lon, lat]``
        vertex as ``[lat, lon]``. This locks that intent so the divergence is not
        mistaken for a bug and "fixed".
        """
        data = _masked([1.0], dtype="float32")
        cov = to_coverage(_polygon_input(data, crs=rasterio.CRS.from_epsg(4326)))

        composite = cov.domain.axes.composite
        assert isinstance(composite, ValuesAxis)
        assert composite.coordinates == ["x", "y"]

        assert cov.domain.referencing is not None
        assert cov.domain.referencing[0].coordinates == ["y", "x"]

        assert_schema_valid(cov)
