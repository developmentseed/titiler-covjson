"""Helper utilities for CoverageJSON conversion.

This module provides utilities for converting CRS references, numpy dtypes, and
creating reference system objects for CoverageJSON output.
"""

from __future__ import annotations

import numpy as np
import rasterio
from covjson_pydantic.ndarray import NdArrayFloat, NdArrayInt, NdArrayStr
from covjson_pydantic.reference_system import (
    ReferenceSystem,
    ReferenceSystemConnectionObject,
)
from covjson_pydantic.unit import Symbol, Unit

TYPE_CHECKING = False

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Any

    import numpy.typing as npt

_AUTHORITY_URI_TEMPLATES = {
    "EPSG": "http://www.opengis.net/def/crs/EPSG/9.9.1/{}",
    "OGC": "http://www.opengis.net/def/crs/OGC/1.3/{}",
}


def _ucum_unit(en_label: str, ucum_code: str) -> Unit:
    return Unit(
        label={"en": en_label},
        symbol=Symbol(value=ucum_code, type="http://www.opengis.net/def/uom/UCUM/"),
    )


_UCUM_CODE_TO_UNIT: dict[str, Unit] = {
    # Length / displacement
    "mm": _ucum_unit("millimeters", "mm"),
    "cm": _ucum_unit("centimeters", "cm"),
    "m": _ucum_unit("meters", "m"),
    "km": _ucum_unit("kilometers", "km"),
    # Velocity
    "m/s": _ucum_unit("meters per second", "m/s"),
    # Temperature
    "K": _ucum_unit("Kelvin", "K"),
    "Cel": _ucum_unit("degrees Celsius", "Cel"),
    # Pressure
    "Pa": _ucum_unit("Pascals", "Pa"),
    "hPa": _ucum_unit("hectopascals", "hPa"),
    # Precipitation / accumulation
    "mm/h": _ucum_unit("millimeters per hour", "mm/h"),
    "cm/a": _ucum_unit("centimeters per year", "cm/a"),
    # Radiation / energy flux
    "W/m2": _ucum_unit("watts per square meter", "W/m2"),
    # Surface density (e.g. snow water equivalent, soil moisture)
    "kg/m2": _ucum_unit("kilograms per square meter", "kg/m2"),
    # Dimensionless
    "%": _ucum_unit("percent", "%"),
    "dB": _ucum_unit("decibels", "dB"),
}


def create_spatial_2d_reference(crs_uri: str) -> ReferenceSystemConnectionObject:
    """Create a 2-D spatial reference system connection object.

    Args:
        crs_uri: An OGC CRS URI, e.g. the result of :func:`crs_to_ogc_uri`.

    Returns:
        ReferenceSystemConnectionObject: A reference system connection object
            for the given CRS URI.

    Examples:
        >>> uri = crs_to_ogc_uri(rasterio.CRS.from_string("OGC:CRS84"))
        >>> ref = create_spatial_2d_reference(uri)
        >>> ref.coordinates
        ['x', 'y']
        >>> ref.system.type
        'GeographicCRS'
        >>> ref.system.id
        'http://www.opengis.net/def/crs/OGC/1.3/CRS84'
    """
    return ReferenceSystemConnectionObject(
        coordinates=["x", "y"],
        system=ReferenceSystem(type="GeographicCRS", id=crs_uri),
    )


def create_temporal_reference() -> ReferenceSystemConnectionObject:
    """Create an ISO 8601 temporal reference system.

    Returns:
        ReferenceSystemConnectionObject: A reference system connection object
            for ISO 8601 temporal data.

    Examples:
        >>> ref = create_temporal_reference()
        >>> ref.coordinates
        ['t']
        >>> ref.system.type
        'TemporalRS'
        >>> ref.system.id
        'http://www.opengis.net/def/uom/ISO-8601/0/Rfc3339'
        >>> ref.system.calendar
        'Gregorian'
    """
    return ReferenceSystemConnectionObject(
        coordinates=["t"],
        system=ReferenceSystem(
            type="TemporalRS",
            id="http://www.opengis.net/def/uom/ISO-8601/0/Rfc3339",
            calendar="Gregorian",
        ),
    )


def create_unit(ucum_code: str) -> Unit | None:
    """Return a CoverageJSON Unit for a recognised UCUM code, or None.

    Each Unit carries an English label and a UCUM ``Symbol`` (type
    ``"http://www.opengis.net/def/uom/UCUM/"``).

    Args:
        ucum_code: A UCUM unit code (case-sensitive).

    Returns:
        Unit | None: A fully-specified Unit, or None if the code is
            not in the lookup table.

    Examples:
        >>> u = create_unit("mm")
        >>> u.label
        {'en': 'millimeters'}
        >>> u.symbol.value
        'mm'
        >>> create_unit("furlongs") is None
        True
    """
    return _UCUM_CODE_TO_UNIT.get(ucum_code)


def crs_to_ogc_uri(crs: rasterio.CRS) -> str:
    """Convert a rasterio CRS object to an OGC URI reference.

    Args:
        crs (rasterio.CRS): The coordinate reference system to convert.

    Returns:
        str: OGC URI string.

    Raises:
        ValueError: If the CRS has no recognised authority code.

    Note:
        Supported authorities and their URI patterns:

        - EPSG: ``http://www.opengis.net/def/crs/EPSG/9.9.1/{code}``
        - OGC:  ``http://www.opengis.net/def/crs/OGC/1.3/{code}``

    Examples:
        >>> crs_to_ogc_uri(rasterio.CRS.from_epsg(4326))
        'http://www.opengis.net/def/crs/EPSG/9.9.1/4326'
        >>> crs_to_ogc_uri(rasterio.CRS.from_string("OGC:CRS84"))
        'http://www.opengis.net/def/crs/OGC/1.3/CRS84'
    """

    if (authority_info := crs.to_authority()) is not None:
        authority, code = authority_info

        if template := _AUTHORITY_URI_TEMPLATES.get(authority.upper()):
            return template.format(code)

    msg = f"Cannot convert CRS {crs} to an OGC URI: no recognised authority code"
    raise ValueError(msg)


def numpy_dtype_to_ndarray(
    data: np.ma.MaskedArray[Any, np.dtype[Any]],
    dtype: npt.DTypeLike,
    axis_names: Sequence[str],
) -> NdArrayFloat | NdArrayInt | NdArrayStr:
    """Convert a masked numpy array to the appropriate CoverageJSON NdArray type.

    Selects ``NdArrayFloat``, ``NdArrayInt``, or ``NdArrayStr`` based on `dtype`
    (the band's declared dtype, not necessarily the array's own dtype). Masked
    values are serialised as ``NaN`` (float) or ``None`` (integer / string) per
    the CoverageJSON spec.

    Args:
        data: A 1-D or 2-D masked numpy array for a single band.
        dtype: The declared band dtype, used to select the NdArray subclass.
        axis_names: Ordered axis labels, e.g. ``["y", "x"]`` for a 2-D grid or
            ``["values"]`` for a 1-D profile.

    Returns:
        NdArrayFloat | NdArrayInt | NdArrayStr: A CoverageJSON range object with
            ``shape`` derived from ``data.shape``.

    Examples:
        >>> import numpy as np
        >>> arr = np.ma.array([[1.5, 2.5], [3.5, 4.5]], mask=False)
        >>> nd = numpy_dtype_to_ndarray(arr, np.float32, ["y", "x"])
        >>> nd.shape
        [2, 2]
        >>> nd.values
        [1.5, 2.5, 3.5, 4.5]
    """
    covjson_dtype = numpy_to_covjson_dtype(dtype)
    shape = list(data.shape)

    if covjson_dtype == "float":
        floats = data.filled(np.nan).flatten().tolist()  # type: ignore[no-untyped-call]
        return NdArrayFloat(values=floats, axisNames=list(axis_names), shape=shape)

    # For int/str, build a boolean mask array (never scalar).
    mask: list[bool] = np.ma.getmaskarray(data).flatten().tolist()  # type: ignore[no-untyped-call]

    if covjson_dtype == "integer":
        unmasked_ints = data.filled(0).flatten().tolist()  # type: ignore[no-untyped-call]
        ints = [None if m else int(v) for v, m in zip(unmasked_ints, mask, strict=True)]
        return NdArrayInt(values=ints, axisNames=list(axis_names), shape=shape)

    unmasked_strs: list[str] = data.filled("").flatten().tolist()  # type: ignore[no-untyped-call]
    strs = [None if m else v for v, m in zip(unmasked_strs, mask, strict=True)]
    return NdArrayStr(values=strs, axisNames=list(axis_names), shape=shape)


def numpy_to_covjson_dtype(dtype: npt.DTypeLike) -> str:
    """Convert a numpy dtype to a CoverageJSON dtype string.

    Args:
        dtype (np.dtype): The numpy dtype to convert.

    Returns:
        str: CoverageJSON dtype string ("float", "integer", or "string").

    Raises:
        ValueError: If the dtype is not supported by CoverageJSON.

    Note:
        - Float types (float16, float32, float64) map to "float"
        - Integer types (int8, int16, int32, int64, uint8, uint16, uint32,
          uint64) map to "integer"
        - String/object types map to "string"

    Examples:
        >>> import numpy as np
        >>> numpy_to_covjson_dtype(np.dtype("float16"))
        'float'
        >>> numpy_to_covjson_dtype(np.float32)
        'float'
        >>> numpy_to_covjson_dtype(np.int16)
        'integer'
        >>> numpy_to_covjson_dtype(np.uint8)
        'integer'
        >>> numpy_to_covjson_dtype(np.dtype("O"))
        'string'
    """
    dtype = np.dtype(dtype)

    if dtype.kind == "f":
        return "float"
    if dtype.kind in {"i", "u"}:
        # signed or unsigned integer
        return "integer"
    if dtype.kind in {"O", "S", "T", "U"}:
        # object, byte-string, or unicode-string (variable-width or fixed-width)
        return "string"

    msg = f"Unsupported dtype for CoverageJSON: {dtype}"
    raise ValueError(msg)
