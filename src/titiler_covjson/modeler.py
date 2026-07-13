"""Modeler: converts raster data to CovJSON Coverage objects.

Constructs covjson-pydantic model instances from :class:`CoverageInput` data,
handling domain and axis construction, parameter mapping, and range
serialization. The conversion is stateless, so it is exposed as plain module
functions (:func:`to_coverage`) rather than a class; the logic depends only on
the neutral :class:`CoverageInput`, never on rio-tiler types, so every path can
be tested from plain numpy arrays.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

from covjson_pydantic.coverage import Coverage
from covjson_pydantic.domain import Axes, CompactAxis, Domain, DomainType, ValuesAxis
from covjson_pydantic.observed_property import ObservedProperty
from covjson_pydantic.parameter import Parameter, Parameters

from titiler_covjson.helpers import (
    create_spatial_2d_reference,
    create_unit,
    numpy_dtype_to_ndarray,
)
from titiler_covjson.input import GridInput, PointInput

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
    # covjson-pydantic accepts, not just the NdArray subtypes it produces.
    RangeValue = NdArrayFloat | NdArrayInt | NdArrayStr | TiledNdArray | AnyUrl

# Axis labels for a gridded range, in row-major order: rows (y) then columns (x).
_GRID_AXIS_NAMES = ("y", "x")


def to_coverage(coverage_input: CoverageInput) -> Coverage:
    """Convert a :class:`CoverageInput` to a CovJSON ``Coverage``.

    Dispatches on the concrete input variant via ``match``:
    :class:`~titiler_covjson.input.GridInput` becomes a Grid coverage and
    :class:`~titiler_covjson.input.PointInput` a Point coverage. Other domain
    variants (such as PointSeries) are not yet supported.

    Args:
        coverage_input: The intermediate representation to convert.

    Returns:
        Coverage: A covjson-pydantic ``Coverage`` model.

    Examples:
        A gridded input becomes a Grid coverage whose axes carry cell *centers*
        (inset half a cell from the bounds edges): ``x`` runs west->east and
        ``y`` north->south (raster row 0 is north). Each band becomes a
        parameter (here with its UCUM unit resolved) and a matching range whose
        axes line up with the grid:

        >>> import numpy as np
        >>> import rasterio
        >>> from titiler_covjson.input import BandInfo, GridInput, PointInput
        >>> from titiler_covjson.input import Position
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
        >>> cov.domain.axes.x.start, cov.domain.axes.x.stop, cov.domain.axes.x.num
        (-5.0, 5.0, 2)
        >>> cov.domain.axes.y.start, cov.domain.axes.y.stop, cov.domain.axes.y.num
        (2.5, -2.5, 2)
        >>> list(cov.parameters.root)
        ['temp']
        >>> cov.parameters.root["temp"].unit.symbol.value
        'Cel'
        >>> cov.ranges["temp"].axisNames, cov.ranges["temp"].shape
        (['y', 'x'], [2, 2])
        >>> cov.ranges["temp"].values
        [1.0, 2.0, 3.0, 4.0]

        A point input becomes a Point coverage: single-value ``x``/``y`` axes at
        the sampled location and one scalar (0-D) range per band:

        >>> cov = to_coverage(
        ...     PointInput(
        ...         data=np.ma.MaskedArray(np.array([21.5], dtype="float32")),
        ...         position=Position(-5.0, 2.5),
        ...         crs=rasterio.CRS.from_epsg(4326),
        ...         bands=(BandInfo("temp", unit="Cel"),),
        ...     )
        ... )
        >>> cov.domain.domainType.value
        'Point'
        >>> cov.domain.axes.x.values, cov.domain.axes.y.values
        ([-5.0], [2.5])
        >>> cov.ranges["temp"].axisNames, cov.ranges["temp"].shape
        ([], [])
        >>> cov.ranges["temp"].values
        [21.5]
    """
    match coverage_input:
        case GridInput():
            return Coverage(
                domain=_create_grid_domain(coverage_input),
                parameters=_create_parameters(coverage_input),
                ranges=_create_grid_ranges(coverage_input),
            )
        case PointInput():
            return Coverage(
                domain=_create_point_domain(coverage_input),
                parameters=_create_parameters(coverage_input),
                ranges=_create_point_ranges(coverage_input),
            )
        case _:  # pragma: no cover
            assert_never(coverage_input)


def _compact_axis(first: float, last: float, num: int) -> CompactAxis:
    """Build a CompactAxis of cell centers spanning the ``first``..``last`` edges.

    A CompactAxis describes the coordinates at which cells are defined (the cell
    *centers*), whereas ``first``/``last`` are the outer bounds *edges*. The
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


def _create_point_domain(coverage_input: PointInput) -> Domain:
    """Build the Point ``Domain`` (single-value x/y/z axes plus referencing).

    Args:
        coverage_input: The point input being converted.

    Returns:
        Domain: A Point domain whose ``x``/``y`` axes each hold the single
            sampled coordinate, with a ``z`` axis only when the position carries
            a vertical coordinate.
    """
    position = coverage_input.position

    # Only 2-D spatial referencing is attached, even when a z coordinate is
    # present: the backing is a single 2-D raster, so there is no vertical
    # reference system to honestly declare for z (see ADR-0001,
    # docs/adr/0001-covjson-http-api-direction.md). z is passed explicitly so
    # exclude_none drops it when the position is purely horizontal.
    return Domain(
        domainType=DomainType.point,
        axes=Axes(
            x=ValuesAxis[float](values=[position.x]),
            y=ValuesAxis[float](values=[position.y]),
            z=(
                ValuesAxis[float](values=[position.z])
                if position.z is not None
                else None
            ),
        ),
        referencing=[create_spatial_2d_reference(coverage_input.crs)],
    )


def _create_point_ranges(coverage_input: PointInput) -> dict[str, RangeValue]:
    """Build one scalar (0-D) range ``NdArray`` per band, keyed by band name.

    Args:
        coverage_input: The point input whose per-band samples become ranges.

    Returns:
        dict[str, RangeValue]: Range arrays keyed by band name, each a 0-D
            scalar (``shape=[]``, ``axisNames=[]``).
    """
    # The i-th band describes data[i], one sampled scalar per band. Slice
    # (data[i : i + 1]) rather than integer-index (data[i]): indexing a 1-D
    # (bands,) array returns a bare numpy scalar (no .filled/.astype) for an
    # unmasked band, whereas the slice stays a (1,) MaskedArray that .reshape(())
    # turns into the 0-D masked array numpy_dtype_to_ndarray needs.
    return {
        band.name: numpy_dtype_to_ndarray(
            coverage_input.data[i : i + 1].reshape(()), band.dtype, ()
        )
        for i, band in enumerate(coverage_input.bands)
    }
