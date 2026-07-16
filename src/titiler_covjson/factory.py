"""CoverageJSON factory: a titiler.core BaseFactory subclass owning the routes.

Serves a single dataset as CoverageJSON over three routes: a 2-D Grid coverage
for a bounding box (``GET {prefix}/bbox/{minx},{miny},{maxx},{maxy}``), a Point
coverage for a single position (``GET {prefix}/position?coords=POINT(x y)``), and
a Polygon coverage reducing an area to one value per band
(``GET {prefix}/area?coords=POLYGON((...))``), reusing titiler's
dependency-injectors for the dataset path, band selection, dataset options, and
(for the bounding box) output sizing. It reads with rio-tiler and funnels the
result through the model layer to a CoverageJSON response.

Mount it with ``app.include_router(CovJSONFactory().router)``. The host
application must also install titiler's exception handlers
(``add_exception_handlers(app, DEFAULT_STATUS_CODES)``) so that rio-tiler,
rasterio, and ``BadRequestError`` failures render as JSON responses with the
right status codes.
"""

# NOTE: deliberately NO ``from __future__ import annotations``. The route is a
# closure inside register_routes, and FastAPI resolves its annotations at
# runtime to build the dependency graph; stringized annotations would be a
# forward-reference hazard there. titiler's own factory omits it for the same
# reason.

import dataclasses
import math
import re
from collections.abc import Callable
from typing import Annotated, Any

import rasterio
from attrs import define
from covjson_pydantic.coverage import Coverage
from fastapi import Depends, Path, Query
from rasterio import windows
from rasterio.io import DatasetReader
from rasterio.warp import transform_bounds
from rio_tiler.constants import WGS84_CRS
from rio_tiler.errors import PointOutsideBounds
from rio_tiler.expression import get_expression_blocks
from rio_tiler.io import Reader
from rio_tiler.models import ImageData, Info, PointData
from rio_tiler.utils import get_vrt_transform
from titiler.core.dependencies import (
    CRSParams,
    DatasetParams,
    DatasetPathParams,
    PartFeatureParams,
)
from titiler.core.errors import BadRequestError
from titiler.core.factory import BaseFactory

from titiler_covjson.dependencies import (
    CovJSONBandParams,
    area_stat,
    reject_vertical_selection,
    to_kwargs,
    validate_covjson_format,
)
from titiler_covjson.helpers import crs_to_ogc_uri
from titiler_covjson.input import (
    BandInfo,
    GridInput,
    PointInput,
    Polygon,
    PolygonInput,
    Position,
    band_info_from_reader_info,
    imagedata_to_grid_input,
    imagedata_to_polygon_input,
    pointdata_to_point_input,
)
from titiler_covjson.modeler import to_coverage
from titiler_covjson.reduce import Stat
from titiler_covjson.responses import CovJSONResponse

DEFAULT_MAX_SIZE = 1024

# CRS84 is WGS84 with longitude/latitude axis order (the CovJSON-preferred label
# for geographic output). It is distinct from EPSG:4326 (latitude/longitude
# authority order) even though both denote the same positions.
CRS84 = rasterio.CRS.from_string("OGC:CRS84")

# WKT for a point: `POINT`, an optional Z/M/ZM tag, then whitespace-separated
# coordinates in parentheses. _parse_point_wkt inspects the tag and coordinate
# count to reject 3-D/measured geometries; the comma (the MULTIPOINT coordinate
# separator) is deliberately not allowed inside a single point.
_POINT_WKT = re.compile(
    r"^\s*POINT\s*(?P<tag>Z|M|ZM)?\s*\(\s*(?P<coords>[^()]*?)\s*\)\s*$",
    re.IGNORECASE,
)

# WKT for a polygon: `POLYGON`, an optional Z/M/ZM tag, then the parenthesized
# ring list `((x y, ...), (x y, ...))`. _parse_polygon_wkt inspects the tag and
# splits the ring list with _POLYGON_RING (each parenthesized group is one ring);
# MULTIPOLYGON fails this pattern (the leading `MULTI`), keeping a single polygon.
_POLYGON_WKT = re.compile(
    r"^\s*POLYGON\s*(?P<tag>Z|M|ZM)?\s*\((?P<rings>.*)\)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_POLYGON_RING = re.compile(r"\(([^()]*)\)")


@define(kw_only=True)
class CovJSONFactory(BaseFactory):
    """Serve a single dataset as CoverageJSON over ``/bbox``, ``/position``, ``/area``.

    ``/bbox`` returns a Grid coverage for a bounding box; ``/position`` returns a
    Point coverage for a single position; ``/area`` returns a Polygon coverage
    reducing the dataset over a polygon to one value per band. Collaborators are
    constructor fields (the composition root): the reader and the titiler
    dependency-injectors for path, band selection, dataset options, and output
    sizing. Two sizing knobs are configurable: ``default_max_size``, the longest
    output dimension applied when no sizing is requested on ``/bbox`` (a request
    still succeeds, just coarser), and ``max_cells``, a hard ceiling on the read
    cell count that bounds ``/bbox`` and ``/area`` (``/position`` samples a single
    cell and needs neither).
    """

    reader: type[Reader] = Reader
    path_dependency: Callable[..., str] = DatasetPathParams
    band_dependency: type[CovJSONBandParams] = CovJSONBandParams
    dataset_dependency: type[DatasetParams] = DatasetParams
    image_dependency: type[PartFeatureParams] = PartFeatureParams
    default_max_size: int = DEFAULT_MAX_SIZE
    max_cells: int = DEFAULT_MAX_SIZE * DEFAULT_MAX_SIZE

    def __attrs_post_init__(self) -> None:
        """Validate the sizing invariant, then register routes (base init).

        Raises:
            ValueError: If ``max_cells < default_max_size ** 2``. A full-extent
                read at the downsampling default could otherwise exceed the
                ceiling and be wrongly rejected.
        """
        if self.max_cells < self.default_max_size**2:
            msg = (
                f"max_cells ({self.max_cells}) must be >= default_max_size ** 2 "
                f"({self.default_max_size**2}); otherwise a full-extent read at "
                "the downsampling default could exceed the cell-count ceiling."
            )
            raise ValueError(msg)

        # titiler's BaseFactory.__attrs_post_init__ (untyped) registers the
        # routes and configures the operation prefix.
        super().__attrs_post_init__()  # type: ignore[no-untyped-call]

    def register_routes(self) -> None:
        """Register the ``/bbox``, ``/position``, and ``/area`` routes."""

        @self.router.get(
            "/bbox/{minx},{miny},{maxx},{maxy}",
            response_class=CovJSONResponse,
            operation_id=f"{self.operation_prefix}getCoverageForBoundingBox",
            summary="Get a CoverageJSON Grid coverage for a bounding box",
            description=(
                "Read the bounding box `minx,miny,maxx,maxy` from the dataset and "
                "return a 2-D CoverageJSON Grid coverage. By default the box is "
                "interpreted in, and the output labeled with, CRS84 "
                "(longitude/latitude); pass `crs` to override."
            ),
        )
        def bbox_coverage(
            minx: Annotated[float, Path(description="Bounding box minimum X.")],
            miny: Annotated[float, Path(description="Bounding box minimum Y.")],
            maxx: Annotated[float, Path(description="Bounding box maximum X.")],
            maxy: Annotated[float, Path(description="Bounding box maximum Y.")],
            src_path: Annotated[str, Depends(self.path_dependency)],
            band_params: Annotated[CovJSONBandParams, Depends(self.band_dependency)],
            dataset_params: Annotated[DatasetParams, Depends(self.dataset_dependency)],
            image_params: Annotated[PartFeatureParams, Depends(self.image_dependency)],
            crs: Annotated[rasterio.CRS | None, Depends(CRSParams)],
            _format: Annotated[None, Depends(validate_covjson_format)],
        ) -> CovJSONResponse:
            _validate_bbox(minx, miny, maxx, maxy)
            _validate_output_dimensions(image_params.width, image_params.height)

            read_crs, label_crs = _resolve_crs(crs)
            _validate_label_crs(label_crs)
            band_kwargs = to_kwargs(band_params)

            image, info = _read_bounded_image(
                self.reader,
                src_path,
                (minx, miny, maxx, maxy),
                read_crs=read_crs,
                band_kwargs=band_kwargs,
                image_params=image_params,
                dataset_kwargs=to_kwargs(dataset_params),
                default_max_size=self.default_max_size,
                max_cells=self.max_cells,
            )

            grid_input = _build_grid_input(image, info, band_kwargs, label_crs)

            return _covjson_response(to_coverage(grid_input), label_crs)

        @self.router.get(
            "/position",
            response_class=CovJSONResponse,
            operation_id=f"{self.operation_prefix}getCoverageForPosition",
            summary="Get a CoverageJSON Point coverage for a position",
            description=(
                "Sample the dataset at the position `coords` (a WKT `POINT(x y)`) "
                "and return a CoverageJSON Point coverage. By default the position "
                "is interpreted in, and the output labeled with, CRS84 "
                "(longitude/latitude); pass `crs` to override. Vertical selection "
                "(a `z` level, or a 3-D `POINT Z`) is rejected: the 2-D raster "
                "backing cannot sample a vertical level. A `datetime` selector is "
                "not yet honored (this dataset has no temporal dimension)."
            ),
        )
        def position_coverage(
            coords: Annotated[
                str,
                Query(description="Position as WKT, e.g., POINT(0 0)."),
            ],
            src_path: Annotated[str, Depends(self.path_dependency)],
            band_params: Annotated[CovJSONBandParams, Depends(self.band_dependency)],
            dataset_params: Annotated[DatasetParams, Depends(self.dataset_dependency)],
            crs: Annotated[rasterio.CRS | None, Depends(CRSParams)],
            _vertical: Annotated[None, Depends(reject_vertical_selection)],
            _format: Annotated[None, Depends(validate_covjson_format)],
        ) -> CovJSONResponse:
            position = _parse_point_wkt(coords)
            read_crs, label_crs = _resolve_crs(crs)
            _validate_label_crs(label_crs)
            band_kwargs = to_kwargs(band_params)

            point, info = _read_point(
                self.reader,
                src_path,
                position,
                read_crs=read_crs,
                band_kwargs=band_kwargs,
                dataset_kwargs=to_kwargs(dataset_params),
            )

            point_input = _build_point_input(
                point, info, band_kwargs, position, label_crs
            )

            return _covjson_response(to_coverage(point_input), label_crs)

        @self.router.get(
            "/area",
            response_class=CovJSONResponse,
            operation_id=f"{self.operation_prefix}getCoverageForArea",
            summary="Get a CoverageJSON Polygon coverage for an area",
            description=(
                "Reduce the dataset over the polygon `coords` (a WKT "
                "`POLYGON((x y, ...))`) to a single value per band by `stat` "
                "(default `mean`) and return a CoverageJSON Polygon coverage. The "
                "reduction is an unweighted, all-touched pixel statistic: every "
                "pixel the polygon boundary touches is included whole, at equal "
                "weight, and none is weighted by the fraction of it the polygon "
                "actually covers. Expect results to diverge from an area-weighted "
                "zonal statistic where boundary pixels are a large share of the "
                "polygon, i.e., for polygons only a few pixels across. By "
                "default the polygon is interpreted in, and the output labeled "
                "with, CRS84 (longitude/latitude); pass `crs` to override. A "
                "polygon that selects no valid pixels (outside the dataset, or "
                "all nodata) yields a `null` value rather than an error. Vertical "
                "selection (a `z` level, or a 3-D `POLYGON Z`) is rejected: the "
                "2-D raster backing has no vertical dimension to reduce over."
            ),
        )
        def area_coverage(
            coords: Annotated[
                str,
                Query(description="Area as WKT, e.g., POLYGON((0 0, 1 0, 1 1, 0 0))."),
            ],
            src_path: Annotated[str, Depends(self.path_dependency)],
            band_params: Annotated[CovJSONBandParams, Depends(self.band_dependency)],
            dataset_params: Annotated[DatasetParams, Depends(self.dataset_dependency)],
            crs: Annotated[rasterio.CRS | None, Depends(CRSParams)],
            stat: Annotated[Stat, Depends(area_stat)],
            _vertical: Annotated[None, Depends(reject_vertical_selection)],
            _format: Annotated[None, Depends(validate_covjson_format)],
        ) -> CovJSONResponse:
            polygon = _parse_polygon_wkt(coords)
            _reject_degenerate_polygon(polygon)
            read_crs, label_crs = _resolve_crs(crs)
            _validate_label_crs(label_crs)
            band_kwargs = to_kwargs(band_params)

            image, info = _read_polygon_image(
                self.reader,
                src_path,
                polygon,
                read_crs=read_crs,
                band_kwargs=band_kwargs,
                dataset_kwargs=to_kwargs(dataset_params),
                max_cells=self.max_cells,
            )

            polygon_input = _build_polygon_input(
                image, info, band_kwargs, polygon, stat, label_crs
            )

            return _covjson_response(to_coverage(polygon_input), label_crs)


def _covjson_response(coverage: Coverage, label_crs: rasterio.CRS) -> CovJSONResponse:
    """Serialize a coverage to a ``CovJSONResponse`` with the ``Content-Crs`` header.

    The shared response epilogue for every route: serialize with
    ``exclude_none=True`` (the CoverageJSON schema rejects explicit ``null``
    members, though ``null`` *elements* inside a range's ``values`` are kept) and
    advertise the output CRS as an OGC Uniform Resource Identifier (URI) in the
    ``Content-Crs`` response header.

    Args:
        coverage: The coverage to serialize.
        label_crs: The output (label) CRS, for the ``Content-Crs`` header.

    Returns:
        CovJSONResponse: The serialized CoverageJSON response.
    """
    return CovJSONResponse(
        content=coverage.model_dump_json(exclude_none=True),
        headers={"Content-Crs": f"<{crs_to_ogc_uri(label_crs)}>"},
    )


def _read_bounded_image(
    reader: type[Reader],
    src_path: str,
    bounds: tuple[float, float, float, float],
    *,
    read_crs: rasterio.CRS,
    band_kwargs: dict[str, Any],
    image_params: PartFeatureParams,
    dataset_kwargs: dict[str, Any],
    default_max_size: int,
    max_cells: int,
) -> tuple[ImageData, Info]:
    """Read ``bounds`` from ``src_path`` as an image, enforcing the cell ceiling.

    Opens ``src_path``, reads the region (reprojecting to ``read_crs``), and
    returns the image alongside the reader's dataset ``info``. An out-of-range
    band index or an oversized output grid is rejected with ``BadRequestError``
    via the guards this calls: the cell-count ceiling is checked before the read
    when the output dimensions are known and again after as a backstop for the
    ``max_size``-bounded paths.

    When no sizing is requested, ``default_max_size`` caps the longest output
    dimension so a full-extent read stays bounded; rio-tiler reads native at
    ``max_size=None``, so the cap is applied here rather than inherited. This
    relies on ``PartFeatureParams`` carrying only sizing fields, so an empty
    ``to_kwargs`` means "no sizing requested"; revisit if a non-sizing field is
    ever added upstream.

    Args:
        reader: The rio-tiler reader type used to open ``src_path``.
        src_path: The dataset path or URL.
        bounds: The output bounds ``(minx, miny, maxx, maxy)`` in ``read_crs``.
        read_crs: The CRS the bounds are expressed in and the read reprojects to.
        band_kwargs: Band-selection keyword arguments for ``part`` (indexes or
            expression).
        image_params: The output-sizing parameters (max_size / width / height).
        dataset_kwargs: Dataset-read keyword arguments for ``part`` (nodata,
            unscale, resampling, reprojection).
        default_max_size: The longest output dimension applied when no sizing is
            requested.
        max_cells: The hard cell-count ceiling.

    Returns:
        tuple[ImageData, Info]: The read image and the reader's dataset info.
    """
    part_kwargs = to_kwargs(image_params) or {"max_size": default_max_size}

    with reader(src_path) as src_dst:
        info = src_dst.info()
        _validate_band_indexes(band_kwargs.get("indexes"), info)

        # Resolve the exact output dimensions rio-tiler will produce (from the
        # width/height/max_size sizing and the read window) and reject an
        # oversized grid before the array is allocated. Opening the dataset only
        # reads metadata, not pixels.
        grid_width, grid_height = _resolve_grid_dimensions(
            src_dst.dataset,
            bounds,
            read_crs=read_crs,
            width=image_params.width,
            height=image_params.height,
            max_size=part_kwargs.get("max_size"),
        )
        _enforce_cell_ceiling(
            grid_width, grid_height, max_cells=max_cells, grid_label="Requested"
        )

        image = src_dst.part(
            bounds,
            dst_crs=read_crs,
            bounds_crs=read_crs,
            **band_kwargs,
            **part_kwargs,
            **dataset_kwargs,
        )

    # Defense-in-depth backstop: the pre-read guard resolves the exact output
    # dimensions, so this only bites if that resolution ever diverges from what
    # part actually produced (a lock-in test guards against silent drift).
    _enforce_cell_ceiling(
        image.width, image.height, max_cells=max_cells, grid_label="Output"
    )

    return image, info


def _read_point(
    reader: type[Reader],
    src_path: str,
    position: Position,
    *,
    read_crs: rasterio.CRS,
    band_kwargs: dict[str, Any],
    dataset_kwargs: dict[str, Any],
) -> tuple[PointData, Info]:
    """Sample ``position`` from ``src_path``, returning the point and dataset info.

    Opens ``src_path``, samples the single position (interpreting it in
    ``read_crs``), and returns the point alongside the reader's dataset ``info``.
    No sizing apparatus applies to a point sample, so unlike the bounding-box
    read this drops ``max_size`` / cell-count handling entirely. An out-of-range
    band index is rejected with ``BadRequestError`` by the guard this calls; a
    position outside the dataset bounds is caught and re-raised as
    ``BadRequestError`` (rio-tiler's ``PointOutsideBounds`` is not in titiler's
    default status map, so it would otherwise surface as an opaque 500).

    Args:
        reader: The rio-tiler reader type used to open ``src_path``.
        src_path: The dataset path or URL.
        position: The position to sample, in ``read_crs``.
        read_crs: The CRS the position is expressed in.
        band_kwargs: Band-selection keyword arguments for ``point`` (indexes or
            expression).
        dataset_kwargs: Dataset-read keyword arguments for ``point`` (nodata,
            unscale, resampling, reprojection).

    Returns:
        tuple[PointData, Info]: The sampled point and the reader's dataset info.

    Raises:
        BadRequestError: If a requested band index is out of range, or the
            position falls outside the dataset bounds. The host application's
            titiler exception handlers render this as a 400 response.
    """
    with reader(src_path) as src_dst:
        info = src_dst.info()
        _validate_band_indexes(band_kwargs.get("indexes"), info)

        try:
            point = src_dst.point(
                position.x,
                position.y,
                coord_crs=read_crs,
                **band_kwargs,
                **dataset_kwargs,
            )
        except PointOutsideBounds as exc:
            msg = (
                f"Position is outside the dataset bounds: ({position.x}, {position.y})."
            )
            raise BadRequestError(msg) from exc

    return point, info


def _read_polygon_image(
    reader: type[Reader],
    src_path: str,
    polygon: Polygon,
    *,
    read_crs: rasterio.CRS,
    band_kwargs: dict[str, Any],
    dataset_kwargs: dict[str, Any],
    max_cells: int,
) -> tuple[ImageData, Info]:
    """Clip ``src_path`` to ``polygon``, returning the masked image and dataset info.

    Opens ``src_path``, reads the polygon's bounding-box window at native
    resolution, and applies the polygon as a cutline so pixels outside it (and
    nodata pixels) are masked. Returns the clipped image alongside the reader's
    dataset ``info``. A polygon outside the dataset does not raise: rio-tiler's
    ``feature`` returns an all-masked array, which the caller reduces to ``null``.
    An out-of-range band index, or a bounding box over the cell-count ceiling, is
    rejected with ``BadRequestError`` by the guards this calls (rendered as a 400
    by the host application's titiler exception handlers).

    The read is bounded before allocation, since it is native-resolution (no
    ``max_size``, so a downstream zonal statistic stays exact) and an enormous
    polygon would otherwise allocate an enormous array. The bounding box is
    measured on the destination grid ``feature`` will produce (via
    :func:`_output_grid_dimensions`, the same dimensions the ``/bbox`` path vets),
    so a reprojection that stretches the destination grid (Web Mercator near the
    poles) far beyond the source window is bounded, not under-counted. This does
    not reject a sub-pixel-thin polygon: a tiny polygon reads a tiny window and
    reduces to ``null`` or a single value, which the empty-polygon contract
    already allows. A degenerate (zero-area) polygon is rejected earlier, in the
    route, before the dataset is opened.

    Args:
        reader: The rio-tiler reader type used to open ``src_path``.
        src_path: The dataset path or URL.
        polygon: The polygon to clip to, in ``read_crs``.
        read_crs: The CRS the polygon is expressed in and the read reprojects to.
        band_kwargs: Band-selection keyword arguments for ``feature`` (indexes or
            expression).
        dataset_kwargs: Dataset-read keyword arguments for ``feature`` (nodata,
            unscale, resampling, reprojection).
        max_cells: The hard cell-count ceiling.

    Returns:
        tuple[ImageData, Info]: The clipped image and the reader's dataset info.
    """
    geometry = {
        "type": "Polygon",
        "coordinates": [[list(vertex) for vertex in ring] for ring in polygon.rings],
    }

    with reader(src_path) as src_dst:
        info = src_dst.info()
        _validate_band_indexes(band_kwargs.get("indexes"), info)

        # Bound the read on the destination grid feature() will allocate (the same
        # dimensions _resolve_grid_dimensions vets for /bbox), not a source-grid
        # measure. `polygon.bounds` is non-degenerate: the route rejects a
        # zero-extent polygon before the dataset is opened, so the dimension
        # computation's aspect-ratio division is safe.
        grid_width, grid_height = _output_grid_dimensions(
            src_dst.dataset,
            polygon.bounds,
            read_crs=read_crs,
            width=None,
            height=None,
            max_size=None,
        )
        _enforce_cell_ceiling(
            grid_width, grid_height, max_cells=max_cells, grid_label="Requested"
        )

        # feature() rasterizes the cutline with all_touched=True, hardcoded rather
        # than exposed as a parameter, so every pixel the polygon boundary touches
        # is masked in whole and the downstream reduction weights each equally.
        # Area weighting would not require shapely, should we want it later:
        # ImageData.get_coverage_array() returns a geometry's fractional per-cell
        # coverage (rasterize at a subpixel scale, then aggregate), and
        # ImageData.statistics() takes that array as its `coverage` argument. Both
        # ship with rio-tiler and rest on rasterio.features, already a dependency.
        image = src_dst.feature(
            geometry,
            shape_crs=read_crs,
            dst_crs=read_crs,
            **band_kwargs,
            **dataset_kwargs,
        )

    _enforce_cell_ceiling(
        image.width, image.height, max_cells=max_cells, grid_label="Output"
    )

    return image, info


def _validate_bbox(minx: float, miny: float, maxx: float, maxy: float) -> None:
    """Reject a degenerate bounding box (each min must be strictly below its max).

    Args:
        minx: Minimum X (west edge).
        miny: Minimum Y (south edge).
        maxx: Maximum X (east edge).
        maxy: Maximum Y (north edge).

    Raises:
        BadRequestError: If ``minx >= maxx`` or ``miny >= maxy``.

    Examples:
        >>> _validate_bbox(-10, -5, 10, 5)
        >>> _validate_bbox(10, -5, -10, 5)
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: Degenerate bbox: require minx <
        maxx and miny < maxy.
    """
    if minx >= maxx or miny >= maxy:
        msg = "Degenerate bbox: require minx < maxx and miny < maxy."
        raise BadRequestError(msg)


def _parse_point_wkt(coords: str) -> Position:
    """Parse a 2-D WKT ``POINT(x y)`` into a :class:`Position`.

    A hand-rolled parser, deliberately dependency-free: a two-float point does
    not justify a GEOS-backed geometry library, and confining WKT handling here
    keeps the model layer geometry-free (only the parser body would change if a
    future geometry endpoint made such a dependency load-bearing). Accepts a
    plain 2-D ``POINT(x y)`` with whitespace-separated coordinates; everything
    else is rejected with ``BadRequestError``:

    - a 3-D or measured geometry (a ``Z`` / ``M`` / ``ZM`` tag, or three or four
      coordinates): the 2-D raster backing cannot sample a vertical level, so
      echoing or dropping it would be dishonest (see
      docs/adr/0001-covjson-http-api-direction.md);
    - a non-POINT geometry, ``POINT EMPTY``, the wrong coordinate count, a
      comma-separated ``POINT(1, 2)`` (the comma is the ``MULTIPOINT`` separator,
      not an intra-point one), or any other malformed input;
    - a non-finite coordinate (NaN or infinity), which would otherwise serialize
      to a silent ``null`` domain axis.

    Args:
        coords: The raw ``coords`` query value.

    Returns:
        Position: The parsed 2-D position.

    Raises:
        BadRequestError: If ``coords`` is not a finite 2-D WKT point. The host
            application's titiler exception handlers render this as a 400
            response.

    Examples:
        >>> _parse_point_wkt("POINT(0 0)")
        Position(x=0.0, y=0.0, z=None)
        >>> _parse_point_wkt("  point ( -5.0   2.5 ) ")
        Position(x=-5.0, y=2.5, z=None)

        A 3-D point is rejected (vertical selection is unsupported here):

        >>> _parse_point_wkt("POINT Z (0 0 5)")
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: Vertical or measured coordinates ...

        A malformed point is rejected:

        >>> _parse_point_wkt("POINT(0)")
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: Invalid position 'POINT(0)': ...
    """
    if (match := _POINT_WKT.match(coords)) is None:
        msg = f"Invalid position {coords!r}: expected WKT POINT(x y), e.g., POINT(0 0)."
        raise BadRequestError(msg)

    tokens = match["coords"].split()

    if match["tag"] or len(tokens) in (3, 4):
        msg = (
            "Vertical or measured coordinates are not supported: this endpoint "
            f"samples a single 2-D raster. Provide a 2-D POINT(x y); got {coords!r}."
        )
        raise BadRequestError(msg)

    if len(tokens) != 2:
        msg = (
            f"Invalid position {coords!r}: expected two coordinates, e.g., POINT(0 0)."
        )
        raise BadRequestError(msg)

    # float() rejects non-numeric tokens and Position rejects non-finite ones
    # (NaN/infinity), so one handler covers both: Position owns the finiteness
    # invariant as the single source of truth (mirroring _validate_label_crs,
    # which likewise turns a helper's ValueError into a BadRequestError).
    try:
        x, y = (float(token) for token in tokens)

        return Position(x, y)
    except ValueError:
        msg = (
            f"Invalid position {coords!r}: coordinates must be finite numbers "
            "(not NaN or infinity), e.g., POINT(0 0)."
        )
        raise BadRequestError(msg) from None


def _parse_polygon_wkt(coords: str) -> Polygon:
    """Parse a 2-D WKT ``POLYGON((x y, ...), ...)`` into a :class:`Polygon`.

    A hand-rolled parser, deliberately dependency-free like :func:`_parse_point_wkt`:
    it splits the parenthesized ring list and reads each vertex as two floats,
    handing the rings to :class:`Polygon`, which owns the ring invariants (closed,
    at least four vertices, finite coordinates). Accepts a single 2-D ``POLYGON``
    with one exterior ring and zero or more interior rings (holes); everything
    else is rejected with ``BadRequestError``:

    - a 3-D or measured geometry (a ``Z`` / ``M`` / ``ZM`` tag, or a vertex with
      three or four coordinates): the 2-D raster backing has no vertical level to
      reduce over, so echoing or dropping it would be dishonest (see
      docs/adr/0001-covjson-http-api-direction.md);
    - a non-POLYGON geometry (including ``MULTIPOLYGON``, whose ``MULTI`` prefix
      fails the pattern), ``POLYGON EMPTY``, an empty ring, a non-finite or
      non-numeric coordinate, an unclosed ring, a ring with fewer than four
      vertices, or any other malformed input.

    Args:
        coords: The raw ``coords`` query value.

    Returns:
        Polygon: The parsed 2-D polygon.

    Raises:
        BadRequestError: If ``coords`` is not a valid 2-D WKT polygon. The host
            application's titiler exception handlers render this as a 400
            response.

    Examples:
        >>> _parse_polygon_wkt("POLYGON((0 0, 1 0, 1 1, 0 0))").rings
        (((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)),)

        A 3-D polygon is rejected (vertical selection is unsupported here):

        >>> _parse_polygon_wkt("POLYGON Z ((0 0 1, 1 0 1, 1 1 1, 0 0 1))")
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: Vertical or measured coordinates ...

        An unclosed ring is rejected:

        >>> _parse_polygon_wkt("POLYGON((0 0, 1 0, 1 1, 0 1))")
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: Invalid polygon 'POLYGON((0 0, ...
    """
    if (match := _POLYGON_WKT.match(coords)) is None:
        msg = (
            f"Invalid polygon {coords!r}: expected WKT POLYGON((x y, x y, ...)), "
            "e.g., POLYGON((0 0, 1 0, 1 1, 0 0))."
        )
        raise BadRequestError(msg)

    if match["tag"]:
        msg = (
            "Vertical or measured coordinates are not supported: this endpoint "
            f"reduces a single 2-D raster. Provide a 2-D POLYGON; got {coords!r}."
        )
        raise BadRequestError(msg)

    if not (ring_strings := _POLYGON_RING.findall(match["rings"])):
        msg = (
            f"Invalid polygon {coords!r}: expected at least one parenthesized ring, "
            "e.g., POLYGON((0 0, 1 0, 1 1, 0 0))."
        )
        raise BadRequestError(msg)

    # _parse_ring rejects a non-2-D or non-numeric vertex and Polygon rejects a
    # non-finite, unclosed, or too-short ring; one handler turns every ValueError
    # into a 400. Polygon owns the ring invariants as the single source of truth
    # (mirroring _parse_point_wkt delegating finiteness to Position).
    try:
        return Polygon(rings=tuple(map(_parse_ring, ring_strings)))
    except ValueError as exc:
        msg = f"Invalid polygon {coords!r}: {exc}"
        raise BadRequestError(msg) from exc


def _parse_ring(ring: str) -> tuple[tuple[float, float], ...]:
    """Parse a WKT ring body (``x y, x y, ...``) into a tuple of ``(x, y)`` vertices.

    Args:
        ring: A single ring's comma-separated ``x y`` vertices (the text inside
            one ring's parentheses).

    Returns:
        tuple[tuple[float, float], ...]: The parsed vertices, in order.

    Raises:
        ValueError: If a vertex is not a 2-D ``x y`` pair (including a 3-D or
            measured vertex), or a coordinate is not a number. The caller turns
            this into a ``BadRequestError``.
    """
    vertices: list[tuple[float, float]] = []

    for pair in ring.split(","):
        tokens = pair.split()

        if len(tokens) in {3, 4}:
            msg = (
                "vertical or measured coordinates are not supported: each vertex "
                f"must be a 2-D 'x y' pair; got {pair.strip()!r}."
            )
            raise ValueError(msg)

        if len(tokens) != 2:
            msg = f"each ring vertex must be an 'x y' pair; got {pair.strip()!r}."
            raise ValueError(msg)

        # float() rejects a non-numeric token; a non-finite one (NaN/infinity)
        # parses here and is rejected by Polygon, as in _parse_point_wkt.
        x, y = (float(token) for token in tokens)
        vertices.append((x, y))

    return tuple(vertices)


def _reject_degenerate_polygon(polygon: Polygon) -> None:
    """Reject a degenerate polygon (a point or an axis-aligned line) before I/O.

    A polygon whose bounding box has zero width or height cannot bound an area to
    reduce: rio-tiler's ``feature`` cannot sample it (it raises "Cannot invert
    geotransform"). Degeneracy is a pure property of the geometry, so it is
    checked in the route before the dataset is opened, mirroring
    :func:`_validate_bbox` for the ``/bbox`` path (a sub-pixel but nonzero polygon
    is not rejected: it reads as a tiny window).

    Args:
        polygon: The parsed request polygon.

    Raises:
        BadRequestError: If the polygon's bounding box has zero width or height.
            The host application's titiler exception handlers render this as a 400
            response.

    Examples:
        A polygon collapsed to a point (or an axis-aligned line) is rejected:

        >>> from titiler_covjson.input import Polygon
        >>> _reject_degenerate_polygon(
        ...     Polygon(rings=(((5.0, 5.0), (5.0, 5.0), (5.0, 5.0), (5.0, 5.0)),))
        ... )
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: Polygon area is degenerate: its
        bounding box has zero width or height (a point or a line). Provide a
        polygon covering a nonzero area.
    """
    minx, miny, maxx, maxy = polygon.bounds

    if minx == maxx or miny == maxy:
        msg = (
            "Polygon area is degenerate: its bounding box has zero width or height "
            "(a point or a line). Provide a polygon covering a nonzero area."
        )
        raise BadRequestError(msg)


def _validate_output_dimensions(width: int | None, height: int | None) -> None:
    """Reject a non-positive explicit output ``width`` or ``height``.

    ``PartFeatureParams`` does not constrain these to be positive, so ``?width=0``
    or ``?width=-5`` reaches the read. A zero or negative dimension is a
    degenerate grid rio-tiler cannot produce (it surfaces as an opaque 500), and
    a zero would also be conflated with an absent dimension; rejecting it up
    front turns it into an actionable 400.

    Args:
        width: The requested output width, or ``None``.
        height: The requested output height, or ``None``.

    Raises:
        BadRequestError: If ``width`` or ``height`` is given and is less than 1.

    Examples:
        >>> _validate_output_dimensions(256, 128)
        >>> _validate_output_dimensions(None, None)
        >>> _validate_output_dimensions(0, 128)
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: width must be a positive integer; got 0.
    """
    for name, value in (("width", width), ("height", height)):
        if value is not None and value < 1:
            msg = f"{name} must be a positive integer; got {value}."
            raise BadRequestError(msg)


def _output_grid_dimensions(
    dataset: DatasetReader,
    bounds: tuple[float, float, float, float],
    *,
    read_crs: rasterio.CRS,
    width: int | None,
    height: int | None,
    max_size: int | None,
) -> tuple[int, int]:
    """Compute the output grid dimensions rio-tiler's ``part``/``feature`` produces.

    Mirrors the dimension logic in ``rio_tiler.reader.part`` so the resulting cell
    count can be checked against the ceiling before the array is read:

    - both ``width`` and ``height`` given: returned unchanged (``part`` ignores
      ``max_size`` then);
    - exactly one given: the other is derived from the read window's aspect
      ratio (``part`` upsamples the given dimension);
    - neither given: ``max_size`` caps the longer axis of the read window, or,
      when ``max_size`` is also ``None``, the native window is read.

    A reprojecting read (``read_crs != dataset.crs``) is measured on the
    *destination* VRT grid, which Web Mercator stretches near the poles far beyond
    the same box on the source pixel grid, so bounding a reprojecting read on the
    source grid would under-count it by orders of magnitude. Unlike
    :func:`_resolve_grid_dimensions`, this only computes dimensions; it does not
    reject a too-thin box (the ``/area`` read is permissive: a sub-pixel polygon
    reads a tiny window). The caller must pass a non-degenerate box (non-zero
    width and height), so the aspect-ratio division is safe.

    Args:
        dataset: The open rasterio dataset, for the read-window geometry.
        bounds: The output bounds ``(minx, miny, maxx, maxy)`` in ``read_crs``.
        read_crs: The Coordinate Reference System (CRS) the bounds are expressed
            in and that the read reprojects to.
        width: The requested output width, or ``None``.
        height: The requested output height, or ``None``.
        max_size: The longest-output-dimension cap applied when neither width nor
            height is given, or ``None`` to read the native window.

    Returns:
        tuple[int, int]: The resolved ``(width, height)``.

    Examples:
        Both dimensions given are returned unchanged; this is the only case that
        short-circuits before the read window is consulted, so ``dataset`` and
        ``bounds`` are unused (hence the placeholder values below):

        >>> _output_grid_dimensions(
        ...     None, (0, 0, 1, 1), read_crs=None, width=256, height=128,
        ...     max_size=None,
        ... )
        (256, 128)

        Every other case (a lone dimension, or a ``max_size`` cap) is derived
        from the read window, so it needs an open dataset and is not shown here.
    """
    # The derivation mirrors rio_tiler.reader.part; the lock-in test
    # test_resolve_grid_dimensions_matches_rio_tiler guards against drift that
    # would silently defeat the pre-read cell-count ceiling.
    if width is not None and height is not None:
        return width, height

    # Match part's read window: the reprojected VRT grid when the read
    # reprojects, else the native window over the source transform.
    if read_crs != dataset.crs:
        _, window_width, window_height = get_vrt_transform(
            dataset, bounds, height, width, dst_crs=read_crs
        )
    else:
        window = windows.from_bounds(*bounds, transform=dataset.transform)
        window_width, window_height = window.width, window.height

    # Aspect ratio first, then multiply, matching part's exact float association
    # so the derived dimension is bit-identical to what it produces. Taken only in
    # the lone-dimension branches, where the read window is non-empty.
    if width is not None:
        return width, math.ceil(width * (window_height / window_width))

    if height is not None:
        return math.ceil(height / (window_height / window_width)), height

    if max_size is None:
        return round(window_width), round(window_height)

    return _scale_to_max_size(max_size, round(window_width), round(window_height))


def _resolve_grid_dimensions(
    dataset: DatasetReader,
    bounds: tuple[float, float, float, float],
    *,
    read_crs: rasterio.CRS,
    width: int | None,
    height: int | None,
    max_size: int | None,
) -> tuple[int, int]:
    """Resolve the ``part`` output dimensions, rejecting a too-thin bounding box.

    The bounding-box (``/bbox``) contract: :func:`_output_grid_dimensions`, but
    when the size is left entirely to the read window (neither ``width`` nor
    ``height`` given) a box spanning less than half a source pixel in an axis is
    rejected, since it has no data to sample and would read as a single value
    stretched across the extent.

    Args:
        dataset: The open rasterio dataset, for the read-window geometry.
        bounds: The output bounds ``(minx, miny, maxx, maxy)`` in ``read_crs``.
        read_crs: The CRS the bounds are expressed in and that the read reprojects
            to.
        width: The requested output width, or ``None``.
        height: The requested output height, or ``None``.
        max_size: The longest-output-dimension cap, or ``None`` to read native.

    Returns:
        tuple[int, int]: The resolved ``(width, height)``.

    Examples:
        >>> _resolve_grid_dimensions(
        ...     None, (0, 0, 1, 1), read_crs=None, width=256, height=128,
        ...     max_size=None,
        ... )
        (256, 128)
    """
    # A too-thin box is rejected with a BadRequestError by the guard this calls
    # (rendered as a 400 by the host application's titiler exception handlers).
    if width is None and height is None:
        _reject_subpixel_bbox(dataset, bounds, read_crs)

    return _output_grid_dimensions(
        dataset,
        bounds,
        read_crs=read_crs,
        width=width,
        height=height,
        max_size=max_size,
    )


def _reject_subpixel_bbox(
    dataset: DatasetReader,
    bounds: tuple[float, float, float, float],
    read_crs: rasterio.CRS,
) -> None:
    """Reject a bounding box spanning less than half a source pixel in an axis.

    Measures the box on the source pixel grid (reprojecting the bounds to the
    source CRS first when the read reprojects), which is uniform at every
    latitude. The destination grid is not: rio-tiler clamps its resolution near
    the poles, so measuring there would misjudge ordinary reads of a global
    dataset.

    Args:
        dataset: The open rasterio dataset, for the source pixel grid.
        bounds: The output bounds ``(minx, miny, maxx, maxy)`` in ``read_crs``.
        read_crs: The CRS the bounds are expressed in.

    Raises:
        BadRequestError: If the box spans less than half a source pixel in an
            axis. The host application's titiler exception handlers render this as
            a 400 response.
    """
    source_bounds = (
        transform_bounds(read_crs, dataset.crs, *bounds)
        if read_crs != dataset.crs
        else bounds
    )
    source_window = windows.from_bounds(*source_bounds, transform=dataset.transform)

    if round(source_window.width) < 1 or round(source_window.height) < 1:
        msg = (
            "Bounding box is too thin to sample: it spans less than half a source "
            "pixel in one dimension. Widen the box, or request an explicit width "
            "and height."
        )
        raise BadRequestError(msg)


def _scale_to_max_size(
    max_size: int, window_width: int, window_height: int
) -> tuple[int, int]:
    """Cap the longer window axis at ``max_size``, preserving the aspect ratio.

    Replicates rio-tiler's ``max_size`` handling: when the window already fits,
    it is returned unchanged, otherwise the longer axis is set to ``max_size``
    and the shorter is scaled to match (rounding up).

    Args:
        max_size: The longest-output-dimension cap.
        window_width: The read-window width in pixels.
        window_height: The read-window height in pixels.

    Returns:
        tuple[int, int]: The resulting ``(width, height)``.

    Examples:
        >>> _scale_to_max_size(50, 80, 40)  # wider than tall, cap the width
        (50, 25)
        >>> _scale_to_max_size(50, 40, 80)  # taller than wide, cap the height
        (25, 50)
        >>> _scale_to_max_size(100, 40, 40)  # already within max_size
        (40, 40)
    """
    # Replicates rio_tiler.reader.part's _get_width_height; the same lock-in test
    # that guards _resolve_grid_dimensions catches drift here.
    if max(window_width, window_height) < max_size:
        return window_width, window_height

    # Same aspect-ratio-first association as part's _get_width_height.
    ratio = window_height / window_width

    if window_height > window_width:
        return math.ceil(max_size / ratio), max_size

    return max_size, math.ceil(max_size * ratio)


def _validate_label_crs(crs: rasterio.CRS) -> None:
    """Reject an output CRS that cannot be expressed as an OGC CRS URI.

    The coverage identifies its Coordinate Reference System (CRS) by an OGC
    Uniform Resource Identifier (URI), which requires a recognized authority
    (such as EPSG). The ``crs`` request parameter accepts anything rasterio can
    parse (Well-Known Text, PROJ strings, ESRI codes), so a CRS with no such
    authority would otherwise reach the modeler and the response header, where
    the URI lookup raises ``ValueError`` (an unhandled 500 for what is really
    invalid input). Validating up front turns it into an actionable 400 before
    the read.

    Args:
        crs: The output (label) CRS resolved from the request.

    Raises:
        BadRequestError: If ``crs`` has no OGC-URI-mappable authority code.

    Examples:
        >>> import rasterio
        >>> _validate_label_crs(rasterio.CRS.from_epsg(4326))
        >>> _validate_label_crs(rasterio.CRS.from_user_input("ESRI:54009"))
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: Unsupported crs: the requested CRS
        has no OGC authority code (such as EPSG) and cannot be expressed as a
        CoverageJSON CRS URI.
    """
    try:
        crs_to_ogc_uri(crs)
    except ValueError:
        msg = (
            "Unsupported crs: the requested CRS has no OGC authority code (such "
            "as EPSG) and cannot be expressed as a CoverageJSON CRS URI."
        )
        raise BadRequestError(msg) from None


def _resolve_crs(requested: rasterio.CRS | None) -> tuple[rasterio.CRS, rasterio.CRS]:
    """Return ``(read_crs, label_crs)`` for a requested output CRS.

    ``read_crs`` is the CRS rio-tiler reprojects the pixels to; ``label_crs`` is
    the CRS the coverage advertises. They coincide except for WGS84 longitude/
    latitude output: an absent ``crs`` reads in ``WGS84_CRS`` (EPSG:4326, a no-op
    for an EPSG:4326 source) while labeling the result CRS84, and an explicit
    EPSG:4326 or CRS84 request is likewise read in EPSG:4326 (avoiding the lossy
    EPSG:4326-to-CRS84 self-reprojection) while keeping the requested label.

    Args:
        requested: The requested output CRS, or ``None`` for the default.

    Returns:
        tuple[rasterio.CRS, rasterio.CRS]: The read CRS and the label CRS.
    """
    if requested is None:
        return WGS84_CRS, CRS84

    read_crs = WGS84_CRS if requested in (WGS84_CRS, CRS84) else requested

    return read_crs, requested


def _enforce_cell_ceiling(
    width: int, height: int, *, max_cells: int, grid_label: str
) -> None:
    """Reject a grid whose cell count exceeds the ceiling.

    The pre-read and post-read checks are identical apart from the word that
    labels the grid, so ``grid_label`` supplies it ("Requested" before reading,
    "Output" after).

    Args:
        width: The grid width in cells.
        height: The grid height in cells.
        max_cells: The maximum allowed cell count.
        grid_label: The word labeling the grid in the error message.

    Raises:
        BadRequestError: If ``width * height`` exceeds ``max_cells``.

    Examples:
        >>> _enforce_cell_ceiling(2, 2, max_cells=4, grid_label="Output")
        >>> _enforce_cell_ceiling(3, 3, max_cells=4, grid_label="Requested")
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: Requested grid 3x3 (w x h) = 9
        cells exceeds limit of 4.
    """
    if (n_cells := width * height) > max_cells:
        msg = (
            f"{grid_label} grid {width}x{height} (w x h) = {n_cells} cells "
            f"exceeds limit of {max_cells}."
        )
        raise BadRequestError(msg)


def _validate_band_indexes(indexes: tuple[int, ...] | None, info: Info) -> None:
    """Reject out-of-range or duplicate band indexes before reading.

    An out-of-range index makes rio-tiler raise a bare ``IndexError`` (mapped to
    a misleading 500), and a duplicate index yields duplicate band names that the
    ``CoverageInput`` uniqueness check later rejects with a bare ``ValueError``
    (also a 500). Both are plain client input, so this pre-validation turns them
    into actionable 400s. Covers ``bidx`` and ``parameter-name`` (both resolve to
    indexes); duplicate band references inside an ``expression`` are handled
    where the expression is parsed.

    Args:
        indexes: The requested 1-based band indexes, or ``None``.
        info: The reader's dataset info, used for the band count.

    Raises:
        BadRequestError: If any index is outside ``1..band_count``, or an index
            is requested more than once.
    """
    if indexes is None:
        return

    band_count = len(info.band_descriptions)

    if any(i < 1 or band_count < i for i in indexes):
        msg = (
            "Requested band index out of range: dataset has "
            f"{band_count} band(s); got {indexes}."
        )
        raise BadRequestError(msg)

    if len(set(indexes)) != len(indexes):
        msg = f"Duplicate band index: band indexes must be unique; got {indexes}."
        raise BadRequestError(msg)


def _build_grid_input(
    image: ImageData,
    info: Info,
    band_kwargs: dict[str, Any],
    crs: rasterio.CRS,
) -> GridInput:
    """Build a GridInput from a read image, resolving per-band metadata.

    Args:
        image: The read image.
        info: The reader's dataset info (for source band metadata).
        band_kwargs: The resolved band selection (``{}`` / ``indexes`` /
            ``expression``).
        crs: The CRS to label the coverage with.

    Returns:
        GridInput: The intermediate representation for the modeler.
    """
    bands = _resolve_read_bands(image, info, band_kwargs)

    return imagedata_to_grid_input(image, bands=bands, crs=crs)


def _build_point_input(
    point: PointData,
    info: Info,
    band_kwargs: dict[str, Any],
    position: Position,
    crs: rasterio.CRS,
) -> PointInput:
    """Build a PointInput from a read point, resolving per-band metadata.

    The mirror of :func:`_build_grid_input` for the point path: the same band
    resolution, then the point converter carrying the sampled ``position``.

    Args:
        point: The read point sample.
        info: The reader's dataset info (for source band metadata).
        band_kwargs: The resolved band selection (``{}`` / ``indexes`` /
            ``expression``).
        position: The sampled position, in ``crs``.
        crs: The CRS to label the coverage with.

    Returns:
        PointInput: The intermediate representation for the modeler.
    """
    bands = _resolve_read_bands(point, info, band_kwargs)

    return pointdata_to_point_input(point, position=position, bands=bands, crs=crs)


def _build_polygon_input(
    image: ImageData,
    info: Info,
    band_kwargs: dict[str, Any],
    polygon: Polygon,
    stat: Stat,
    crs: rasterio.CRS,
) -> PolygonInput:
    """Build a PolygonInput from a clipped image, resolving per-band metadata.

    The area path's analogue of :func:`_build_grid_input`: the same band
    resolution (for names and units), then the polygon converter, which reduces
    the clipped image to one scalar per band by ``stat`` and takes each band's
    range dtype from that reduced value.

    Args:
        image: The polygon-clipped image.
        info: The reader's dataset info (for source band metadata).
        band_kwargs: The resolved band selection (``{}`` / ``indexes`` /
            ``expression``).
        polygon: The polygon the reduced values summarize, in ``crs``.
        stat: The statistic to reduce each band by.
        crs: The CRS to label the coverage with.

    Returns:
        PolygonInput: The intermediate representation for the modeler.
    """
    bands = _resolve_read_bands(image, info, band_kwargs)

    return imagedata_to_polygon_input(
        image, geometry=polygon, stat=stat, bands=bands, crs=crs
    )


def _resolve_read_bands(
    read: ImageData | PointData,
    info: Info,
    band_kwargs: dict[str, Any],
) -> tuple[BandInfo, ...]:
    """Resolve per-band metadata for a read result, aligned to the returned bands.

    Shared by the grid and point paths (``ImageData`` and ``PointData`` both
    expose ``array`` and ``band_names``). For an ``expression`` result, each
    derived band is named for its sub-expression. Otherwise the reader's
    ``info()`` metadata is subset to the bands actually returned.

    A read can change dtype from the source storage dtype (e.g., unscale casts an
    integer band to float when applying scale/offset), so the CoverageJSON range
    type is selected from the returned array's dtype, not ``info``'s.

    Args:
        read: The read image or point sample.
        info: The reader's dataset info (for source band metadata).
        band_kwargs: The resolved band selection (``{}`` / ``indexes`` /
            ``expression``).

    Returns:
        tuple[BandInfo, ...]: One entry per returned band, in band order.
    """
    if (expression := band_kwargs.get("expression")) is not None:
        return tuple(
            BandInfo(name=name, dtype=read.array.dtype)
            for name in _expression_band_names(expression)
        )

    by_name = {band.name: band for band in band_info_from_reader_info(info)}

    # Each returned band name is looked up directly: a missing one is an internal
    # invariant break that should surface loudly, not be silently dropped.
    return tuple(
        dataclasses.replace(by_name[name], dtype=read.array.dtype)
        for name in read.band_names
    )


def _expression_band_names(expression: str) -> tuple[str, ...]:
    """Derive unique CoverageJSON band names from a band expression.

    An expression is a ``;``-separated list of sub-expressions; each names one
    derived band (rio-tiler itself numbers them ``b1``, ``b2``, ..., which would
    collide with source band names). The names double as CoverageJSON parameter
    keys, so they must be unique.

    Args:
        expression: The ``;``-separated band expression.

    Returns:
        tuple[str, ...]: The derived band names, in request order.

    Raises:
        BadRequestError: If the derived names are not all unique.

    Examples:
        >>> _expression_band_names("b1;b2/b1")
        ('b1', 'b2/b1')

        Empty sub-expressions (e.g., from a trailing ``;``) are dropped, so the
        names stay one-to-one with the bands the read returns:

        >>> _expression_band_names("b1;b2/b1;")
        ('b1', 'b2/b1')

        >>> _expression_band_names("b1;b1")
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: Duplicate expression: derived
        band names must be unique; got ('b1', 'b1').
    """
    # Tokenize with rio-tiler's own block splitter so our per-band names stay in
    # exact one-to-one correspondence with the bands the read returns for the
    # same expression (it splits on ``;`` and drops empty sub-expressions).
    names = tuple(block.strip() for block in get_expression_blocks(expression))

    if len(set(names)) != len(names):
        msg = f"Duplicate expression: derived band names must be unique; got {names}."
        raise BadRequestError(msg)

    return names
