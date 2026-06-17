"""Modeler: converts raster data to CovJSON Coverage objects.

Constructs covjson-pydantic model instances from :class:`CoverageInput` data,
handling domain and axis construction, parameter mapping, and range
serialization. The conversion is stateless, so it is exposed as plain module
functions (:func:`to_coverage`) rather than a class; the logic depends only on
the neutral :class:`CoverageInput`, never on rio-tiler types, so every path can
be tested from plain numpy arrays.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from covjson_pydantic.coverage import Coverage
from covjson_pydantic.domain import Axes, CompactAxis, Domain, DomainType
from covjson_pydantic.observed_property import ObservedProperty
from covjson_pydantic.parameter import Parameter, Parameters

from titiler_covjson.helpers import (
    create_spatial_2d_reference,
    create_unit,
    numpy_dtype_to_ndarray,
)

if TYPE_CHECKING:
    from covjson_pydantic.ndarray import (
        NdArrayFloat,
        NdArrayInt,
        NdArrayStr,
        TiledNdArray,
    )
    from pydantic import AnyUrl

    from titiler_covjson.input import BandInfo, CoverageInput

    # The value type of ``Coverage.ranges``. A dict is invariant in its value
    # type, so ``_create_grid_ranges`` must be annotated with the full union
    # covjson-pydantic accepts -- not just the NdArray subtypes it produces.
    RangeValue = NdArrayFloat | NdArrayInt | NdArrayStr | TiledNdArray | AnyUrl

# Axis labels for a gridded range, in row-major order: rows (y) then columns (x).
_GRID_AXIS_NAMES = ("y", "x")


def to_coverage(coverage_input: CoverageInput) -> Coverage:
    """Convert a :class:`CoverageInput` to a CovJSON ``Coverage``.

    Currently handles the Grid domain (gridded rasters, ``geometry is None``).
    Other domain types are added in later stories.

    Args:
        coverage_input: The intermediate representation to convert.

    Returns:
        Coverage: A covjson-pydantic ``Coverage`` model.

    Raises:
        NotImplementedError: If the input describes a non-grid domain, or holds
            data that is not 3-D ``(bands, height, width)``.

    Examples:
        >>> import numpy as np
        >>> import rasterio
        >>> from titiler_covjson.input import BandInfo, CoverageInput
        >>> cov = to_coverage(
        ...     CoverageInput(
        ...         data=np.ma.MaskedArray(
        ...             np.array([[[1.0, 2.0], [3.0, 4.0]]], dtype="float32")
        ...         ),
        ...         bounds=(-10.0, -5.0, 10.0, 5.0),
        ...         crs=rasterio.CRS.from_epsg(4326),
        ...         bands=(BandInfo("temp", unit="Cel"),),
        ...     )
        ... )
        >>> cov.domain.domainType.value
        'Grid'

        ``x`` runs west->east and ``y`` north->south (raster row 0 is north):

        >>> cov.domain.axes.x.start, cov.domain.axes.x.stop, cov.domain.axes.x.num
        (-10.0, 10.0, 2)
        >>> cov.domain.axes.y.start, cov.domain.axes.y.stop, cov.domain.axes.y.num
        (5.0, -5.0, 2)

        Each band becomes a parameter (here with its UCUM unit resolved) and a
        matching range whose axes line up with the grid:

        >>> list(cov.parameters.root)
        ['temp']
        >>> cov.parameters.root["temp"].unit.symbol.value
        'Cel'
        >>> cov.ranges["temp"].axisNames, cov.ranges["temp"].shape
        (['y', 'x'], [2, 2])
        >>> cov.ranges["temp"].values
        [1.0, 2.0, 3.0, 4.0]
    """
    if coverage_input.geometry is not None:
        msg = (
            "Only gridded inputs (geometry=None) are supported so far; "
            f"got geometry {coverage_input.geometry.geom_type!r}"
        )
        raise NotImplementedError(msg)

    # CoverageInput also permits 2-D data (the future point/profile path); the
    # grid conversion below assumes 3-D (bands, height, width), so reject 2-D
    # here rather than emit a domain/range shape mismatch.
    if coverage_input.data.ndim != 3:
        msg = (
            "Grid coverages require 3-D data with shape (bands, height, width); "
            f"got {coverage_input.data.ndim} dimension(s)"
        )
        raise NotImplementedError(msg)

    return Coverage(
        domain=_create_grid_domain(coverage_input),
        parameters=_create_parameters(coverage_input),
        ranges=_create_grid_ranges(coverage_input),
    )


def _compact_axis(first: float, last: float, num: int) -> CompactAxis:
    """Build a CompactAxis spanning ``first``..``last`` over ``num`` cells.

    Args:
        first: Coordinate of the first cell (e.g., the west or north edge).
        last: Coordinate of the last cell (e.g., the east or south edge).
        num: Number of cells along the axis.

    Returns:
        CompactAxis: The axis. When ``num == 1`` the endpoints collapse to the
            midpoint, since CompactAxis requires ``start == stop`` for a single cell.
    """
    if num == 1:
        midpoint = (first + last) / 2
        return CompactAxis(start=midpoint, stop=midpoint, num=1)

    return CompactAxis(start=first, stop=last, num=num)


def _create_grid_domain(coverage_input: CoverageInput) -> Domain:
    """Build the Grid ``Domain`` (x/y CompactAxes plus spatial referencing).

    Args:
        coverage_input: The gridded input being converted.

    Returns:
        Domain: A Grid domain with ``x``/``y`` compact axes and referencing.
    """
    west, south, east, north = coverage_input.bounds
    height, width = coverage_input.data.shape[-2:]

    # CompactAxis describes a regular axis by its endpoints and cell count.
    # x runs west->east; y runs north->south to match raster row order
    # (row 0 is the north edge). Endpoints use the bounds edges; pixel-center
    # offsets are a possible later refinement.

    return Domain(
        domainType=DomainType.grid,
        axes=Axes(
            x=_compact_axis(west, east, width),
            y=_compact_axis(north, south, height),
        ),
        referencing=[create_spatial_2d_reference(coverage_input.crs)],
    )


def _create_parameters(coverage_input: CoverageInput) -> Parameters:
    """Build one CovJSON ``Parameter`` per band, keyed by band name.

    Args:
        coverage_input: The input whose bands become parameters.

    Returns:
        Parameters: Parameters mapping, one entry per band.
    """
    return Parameters(
        root={band.name: _create_parameter(band) for band in coverage_input.bands}
    )


def _create_parameter(band: BandInfo) -> Parameter:
    """Build a single ``Parameter`` from band metadata.

    Args:
        band: Band metadata (name, description, UCUM unit code).

    Returns:
        Parameter: A parameter whose observed-property label is the band
            description (falling back to its name) and whose unit is
            resolved from the UCUM code when one is present.
    """
    label = {"en": band.description or band.name}

    # An empty unit (the common "no unit" case) skips the UCUM parser; an
    # unresolvable code makes create_unit return None. Either way unit is None
    # and is dropped on serialization via model_dump_json(exclude_none=True).
    return Parameter(
        observedProperty=ObservedProperty(label=label),
        unit=create_unit(band.unit) if band.unit else None,
    )


def _create_grid_ranges(coverage_input: CoverageInput) -> dict[str, RangeValue]:
    """Build one range ``NdArray`` per band, keyed to match the parameters.

    Args:
        coverage_input: The gridded input whose data becomes ranges.

    Returns:
        dict[str, RangeValue]: Range arrays keyed by band name, each shaped
            ``[height, width]``.
    """
    # The i-th band describes data[i]: band order matches the data's leading
    # (band) axis. CoverageInput resolves `bands` at construction and guarantees
    # the counts match; this ordering is the contract the input converters build on.
    return {
        band.name: numpy_dtype_to_ndarray(
            coverage_input.data[i], band.dtype, _GRID_AXIS_NAMES
        )
        for i, band in enumerate(coverage_input.bands)
    }
