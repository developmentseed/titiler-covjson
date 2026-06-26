"""Modeler: converts raster data to CovJSON Coverage objects.

Constructs covjson-pydantic model instances from :class:`CoverageInput` data,
handling domain and axis construction, parameter mapping, and range
serialization. The conversion is stateless, so it is exposed as plain module
functions (:func:`to_coverage`) rather than a class; the logic depends only on
the neutral :class:`CoverageInput`, never on rio-tiler types, so every path can
be tested from plain numpy arrays.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if sys.version_info >= (3, 11):  # pragma: no cover
    from typing import assert_never
else:  # pragma: no cover
    from typing_extensions import assert_never

from covjson_pydantic.coverage import Coverage
from covjson_pydantic.domain import Axes, CompactAxis, Domain, DomainType
from covjson_pydantic.observed_property import ObservedProperty
from covjson_pydantic.parameter import Parameter, Parameters

from titiler_covjson.helpers import (
    create_spatial_2d_reference,
    create_unit,
    numpy_dtype_to_ndarray,
)
from titiler_covjson.input import GridInput

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

    Dispatches on the concrete input variant via ``match``; currently only
    :class:`~titiler_covjson.input.GridInput` is handled. Point and
    PointSeries variants are added in later stories.

    Args:
        coverage_input: The intermediate representation to convert.

    Returns:
        Coverage: A covjson-pydantic ``Coverage`` model.

    Examples:
        >>> import numpy as np
        >>> import rasterio
        >>> from titiler_covjson.input import BandInfo, GridInput
        >>> cov = to_coverage(
        ...     GridInput(
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

        Axes carry cell *centers* (inset half a cell from the bounds edges):
        ``x`` runs west->east and ``y`` north->south (raster row 0 is north):

        >>> cov.domain.axes.x.start, cov.domain.axes.x.stop, cov.domain.axes.x.num
        (-5.0, 5.0, 2)
        >>> cov.domain.axes.y.start, cov.domain.axes.y.stop, cov.domain.axes.y.num
        (2.5, -2.5, 2)

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
    match coverage_input:
        case GridInput():
            return Coverage(
                domain=_create_grid_domain(coverage_input),
                parameters=_create_parameters(coverage_input),
                ranges=_create_grid_ranges(coverage_input),
            )
        case _:  # pragma: no cover
            assert_never(coverage_input)


def _compact_axis(first: float, last: float, num: int) -> CompactAxis:
    """Build a CompactAxis of cell centers spanning the ``first``..``last`` edges.

    A CompactAxis describes the coordinates at which cells are defined -- the cell
    *centers* -- whereas ``first``/``last`` are the outer bounds *edges*. The
    centers are inset from the edges by half a cell, so for ``num`` cells spanning
    ``first``..``last`` the axis runs ``first + dx/2`` .. ``last - dx/2`` where
    ``dx = (last - first) / num``.

    Args:
        first: Outer edge of the first cell (e.g., the west or north bound).
        last: Outer edge of the last cell (e.g., the east or south bound).
        num: Number of cells along the axis.

    Returns:
        CompactAxis: The axis of cell centers. When ``num == 1`` both centers
            coincide at the bounds midpoint, satisfying ``start == stop``.
    """
    half_cell = (last - first) / (2 * num)
    return CompactAxis(start=first + half_cell, stop=last - half_cell, num=num)


def _create_grid_domain(coverage_input: GridInput) -> Domain:
    """Build the Grid ``Domain`` (x/y CompactAxes plus spatial referencing).

    Args:
        coverage_input: The gridded input being converted.

    Returns:
        Domain: A Grid domain with ``x``/``y`` compact axes and referencing.
    """
    west, south, east, north = coverage_input.bounds
    height, width = coverage_input.data.shape[-2:]

    # CompactAxis describes a regular axis by its cell-center endpoints and cell
    # count. x runs west->east; y runs north->south to match raster row order
    # (row 0 is the north edge). _compact_axis insets the bounds edges by half a
    # cell to yield the centers.

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


def _create_grid_ranges(coverage_input: GridInput) -> dict[str, RangeValue]:
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
