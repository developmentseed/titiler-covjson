"""Tests for helper utilities."""

from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt
import pytest
import rasterio
from covjson_pydantic.ndarray import NdArrayFloat, NdArrayInt, NdArrayStr
from covjson_pydantic.unit import Symbol, Unit

from titiler_covjson.helpers import (
    create_unit,
    crs_to_ogc_uri,
    numpy_dtype_to_ndarray,
    numpy_to_covjson_dtype,
)


class TestCrsToOgcUri:
    """Test CRS to OGC URI conversion."""

    # Success cases are tested in doctests for crs_to_ogc_uri.

    def test_no_authority_raises(self) -> None:
        """Test that a CRS with no recognised authority raises ValueError."""
        crs = rasterio.CRS.from_proj4(
            "+proj=tmerc +lat_0=0 +lon_0=100 +k=0.9996 +x_0=500000 +y_0=0 +datum=WGS84"
        )
        with pytest.raises(ValueError, match="no recognised authority code"):
            crs_to_ogc_uri(crs)


class TestNumpyDtypeToNdarray:
    """Test numpy masked array to CoverageJSON NdArray conversion."""

    def test_float_unmasked(self) -> None:
        """Float array with no mask produces NdArrayFloat with correct values."""
        arr: np.ma.MaskedArray[Any, np.dtype[Any]] = np.ma.array(
            [[1.0, 2.0], [3.0, 4.0]], mask=False
        )
        nd = numpy_dtype_to_ndarray(arr, np.float32, ["y", "x"])
        assert isinstance(nd, NdArrayFloat)
        assert nd.shape == [2, 2]
        assert nd.values == [1.0, 2.0, 3.0, 4.0]

    def test_float_masked_values_become_nan(self) -> None:
        """Masked float entries become NaN in the output values."""
        import math

        arr: np.ma.MaskedArray[Any, np.dtype[Any]] = np.ma.array(
            [1.0, 2.0, 3.0], mask=[False, True, False]
        )
        nd = numpy_dtype_to_ndarray(arr, np.float32, ["values"])
        assert isinstance(nd, NdArrayFloat)
        assert nd.values is not None
        assert nd.values[0] == 1.0
        assert math.isnan(nd.values[1])  # type: ignore[arg-type]
        assert nd.values[2] == 3.0

    def test_integer_unmasked(self) -> None:
        """Integer array with no mask produces NdArrayInt with int values."""
        arr: np.ma.MaskedArray[Any, np.dtype[Any]] = np.ma.array(
            [10, 20, 30], mask=False
        )
        nd = numpy_dtype_to_ndarray(arr, np.int32, ["values"])
        assert isinstance(nd, NdArrayInt)
        assert nd.shape == [3]
        assert nd.values == [10, 20, 30]

    def test_integer_masked_values_become_none(self) -> None:
        """Masked integer entries become None in the output values."""
        arr: np.ma.MaskedArray[Any, np.dtype[Any]] = np.ma.array(
            [10, 20, 30], mask=[False, True, False]
        )
        nd = numpy_dtype_to_ndarray(arr, np.int32, ["values"])
        assert isinstance(nd, NdArrayInt)
        assert nd.values is not None
        assert nd.values == [10, None, 30]

    @pytest.mark.parametrize(
        "dtype",
        [np.float32, np.float64, np.int16, np.int32, np.uint8],
        ids=["float32", "float64", "int16", "int32", "uint8"],
    )
    def test_axis_names_and_shape_2d(self, dtype: npt.DTypeLike) -> None:
        """2-D array produces the correct shape and axisNames."""
        arr: np.ma.MaskedArray[Any, np.dtype[Any]] = np.ma.array(
            np.zeros((4, 8)), mask=False
        )
        nd = numpy_dtype_to_ndarray(arr, dtype, ["y", "x"])
        assert nd.shape == [4, 8]
        assert nd.axisNames == ["y", "x"]

    def test_string_dtype_produces_ndarray_str(self) -> None:
        """Object dtype produces NdArrayStr."""
        arr: np.ma.MaskedArray[Any, np.dtype[Any]] = np.ma.array(
            ["a", "b", "c"], mask=[False, True, False]
        )
        nd = numpy_dtype_to_ndarray(arr, np.dtype("O"), ["values"])
        assert isinstance(nd, NdArrayStr)
        assert nd.values is not None
        assert nd.values == ["a", None, "c"]


class TestNumpyToCovjsonDtype:
    """Test numpy dtype to CoverageJSON dtype conversion."""

    @pytest.mark.parametrize(
        "dtype, expected",
        [
            (np.float16, "float"),
            (np.float32, "float"),
            (np.float64, "float"),
            (np.int8, "integer"),
            (np.int16, "integer"),
            (np.int32, "integer"),
            (np.int64, "integer"),
            (np.uint8, "integer"),
            (np.uint16, "integer"),
            (np.uint32, "integer"),
            (np.uint64, "integer"),
            (np.dtype("U10"), "string"),
            (np.dtype("S10"), "string"),
            (np.dtype("O"), "string"),
        ],
    )
    def test_dtype_mapping(self, dtype: npt.DTypeLike, expected: str) -> None:
        """Test numpy dtype maps to the correct CoverageJSON dtype string."""
        assert numpy_to_covjson_dtype(dtype) == expected

    def test_unsupported_dtype(self) -> None:
        """Test handling of unsupported dtypes."""
        with pytest.raises(ValueError, match="Unsupported dtype"):
            numpy_to_covjson_dtype(np.dtype("complex64"))


class TestCreateUnit:
    """Test UCUM unit code to CoverageJSON Unit conversion."""

    @pytest.mark.parametrize(
        "ucum_code, expected_label",
        [
            ("mm", "millimeters"),
            ("cm", "centimeters"),
            ("m", "meters"),
            ("km", "kilometers"),
            ("m/s", "meters per second"),
            ("K", "Kelvin"),
            ("Cel", "degrees Celsius"),
            ("Pa", "Pascals"),
            ("hPa", "hectopascals"),
            ("mm/h", "millimeters per hour"),
            ("cm/a", "centimeters per year"),
            ("W/m2", "watts per square meter"),
            ("kg/m2", "kilograms per square meter"),
            ("%", "percent"),
            ("dB", "decibels"),
        ],
    )
    def test_known_units(self, ucum_code: str, expected_label: str) -> None:
        """Test that known UCUM codes return a Unit with label and UCUM symbol."""
        unit = create_unit(ucum_code)

        assert isinstance(unit, Unit)
        assert unit.label == {"en": expected_label}
        assert unit.symbol == Symbol(
            value=ucum_code, type="http://www.opengis.net/def/uom/UCUM/"
        )

    def test_unknown_unit(self) -> None:
        """Test that an unrecognised code returns None."""
        assert create_unit("furlongs") is None
        assert create_unit("") is None
