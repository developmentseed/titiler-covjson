"""Tests for the modeler conversion functions."""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pytest
import rasterio
from conftest import assert_schema_valid
from covjson_pydantic.coverage import Coverage
from covjson_pydantic.domain import CompactAxis, DomainType
from covjson_pydantic.ndarray import NdArrayFloat, NdArrayInt, NdArrayStr
from covjson_pydantic.unit import Symbol

from titiler_covjson.input import BandInfo, GridInput
from titiler_covjson.modeler import to_coverage


def _masked(
    values: Any,
    *,
    mask: Any = False,
    dtype: Any = None,
) -> np.ma.MaskedArray[Any, np.dtype[Any]]:
    """Build a masked array for tests.

    Centralizes a numpy-version workaround in one place. ``np.ma.array`` is typed
    only for some argument forms in the oldest supported numpy (2.2.6, the floor
    on Python 3.10); the dtype / ndarray / list-mask forms these tests need fall
    through to an untyped overload, which strict mypy rejects as
    ``no-untyped-call`` on 3.10. The newer numpy resolved on Python 3.11+ types
    the call, so there the ignore would itself be unused -- the paired
    ``unused-ignore`` code keeps the comment valid on both.

    NOTE: when Python 3.10 support is dropped (and the numpy floor rises to a
    version that types ``np.ma.array``), remove the ``# type: ignore`` below and,
    if desired, inline this helper again.

    Args:
        values: Array values as a nested sequence or an ndarray.
        mask: Boolean mask, or ``False`` for no masked entries.
        dtype: Optional dtype for the data.

    Returns:
        np.ma.MaskedArray[Any, np.dtype[Any]]: The masked array.
    """
    # Drop this ignore when Python 3.10 support is dropped (see the NOTE above).
    return np.ma.array(values, mask=mask, dtype=dtype)  # type: ignore[no-untyped-call, no-any-return, unused-ignore]


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
        the bounds midpoint, so ``start == stop`` (here 0.0, the centre of the
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
