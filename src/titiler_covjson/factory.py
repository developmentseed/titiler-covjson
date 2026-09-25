"""CoverageJSON factory: a titiler.core BaseFactory subclass owning the routes.

Serves a single dataset as CoverageJSON over three routes: a 2-D Grid coverage
for a bounding box (``GET {prefix}/bbox/{minx},{miny},{maxx},{maxy}``), a Point
or MultiPoint coverage for one or more positions
(``GET {prefix}/position?coords=POINT(x y)`` or ``coords=MULTIPOINT((x y), ...)``),
and a Polygon coverage reducing an area to one value per band
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
from collections.abc import Callable
from typing import Annotated, Any, Literal, assert_never

import rasterio
from attrs import define
from covjson_pydantic.coverage import Coverage
from fastapi import Depends, Path, Query
from rasterio import windows
from rasterio.enums import ColorInterp
from rasterio.io import DatasetReader
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
from rio_tiler.constants import WGS84_CRS
from rio_tiler.errors import PointOutsideBounds
from rio_tiler.expression import get_expression_blocks, parse_expression
from rio_tiler.io import Reader
from rio_tiler.models import ImageData, Info, PointData
from rio_tiler.utils import get_vrt_transform, non_alpha_indexes
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
from titiler_covjson.geometry import MultiPoint, Polygon, Position
from titiler_covjson.helpers import crs_to_ogc_uri
from titiler_covjson.input import (
    BandInfo,
    GridInput,
    MultiPointInput,
    PointInput,
    PolygonInput,
    band_info_from_reader_info,
    imagedata_to_grid_input,
    imagedata_to_polygon_input,
    pointdata_to_multipoint_input,
    pointdata_to_point_input,
)
from titiler_covjson.modeler import to_coverage
from titiler_covjson.reduce import Stat
from titiler_covjson.responses import CovJSONResponse
from titiler_covjson.wkt import (
    InvalidCoords,
    parse_polygon_wkt,
    parse_position_coords,
)

DEFAULT_MAX_SIZE = 1024

# The band allowance folded into the default cell ceiling. That ceiling totals
# the cells over every array a read allocates, so a default sized for a single
# band would coarsen an ordinary multi-band full-extent read: an unsized read is
# fitted to the ceiling rather than rejected by it, so what this buys is
# resolution, not servability. Four keeps the common RGB / RGBA source at full
# resolution. It is headroom in the default, not a cap on bands: nothing rejects
# a request for more, so long as cells x bands fits under the ceiling.
DEFAULT_BAND_ALLOWANCE = 4

DEFAULT_MAX_CELLS = DEFAULT_MAX_SIZE**2 * DEFAULT_BAND_ALLOWANCE

# The default cap on the number of positions a single MULTIPOINT may name. It
# bounds the number of point reads (one per position), a distinct resource from
# max_cells (which bounds the cells a read allocates across all its bands) and
# from max_coords_length (which bounds the text parsed to find them).
DEFAULT_MAX_SAMPLES = 1000

# The default cap on the length of the `coords` query parameter, in characters
# rather than bytes: the parsers run on the decoded string, so parse cost tracks
# characters. It is the only cap that bounds the parse itself: max_samples is
# counted after `coords` has been parsed in full, so that work is already spent.
# Deliberately larger than a default uvicorn will even accept: most servers
# reject an overly-long query string before it reaches us, and this cap is here
# for the deployments whose server allows a longer one through.
DEFAULT_MAX_COORDS_LENGTH = 256 * 1024

# CRS84 is WGS84 with longitude/latitude axis order (the CovJSON-preferred label
# for geographic output). It is distinct from EPSG:4326 (latitude/longitude
# authority order) even though both denote the same positions.
CRS84 = rasterio.CRS.from_string("OGC:CRS84")


@define(kw_only=True)
class CovJSONFactory(BaseFactory):
    """Serve a single dataset as CoverageJSON over ``/bbox``, ``/position``, ``/area``.

    ``/bbox`` returns a Grid coverage for a bounding box; ``/position`` returns a
    Point coverage for a single ``POINT`` or a MultiPoint coverage for a
    ``MULTIPOINT``; ``/area`` returns a Polygon coverage reducing the dataset over
    a polygon to one value per band. Collaborators are constructor fields (the
    composition root): the reader and the titiler dependency-injectors for path,
    band selection, dataset options, and output sizing. Three sizing knobs are
    configurable: ``default_max_size``, the longest output dimension applied when
    no sizing is requested on ``/bbox`` (a request still succeeds, just coarser);
    ``max_cells``, a hard ceiling on the total cells a read allocates
    (``width * height * bands``, where bands counts the full-size arrays the
    read makes: one per source band it reads, plus one per band an
    ``expression`` derives, plus one when it reads an alpha band as the
    mask), bounding ``/bbox`` and ``/area``; and
    ``max_samples``, the cap on the number of positions a ``/position``
    ``MULTIPOINT`` may name (each is one point read). A single ``POINT`` needs
    none of the three.

    A fourth knob, ``max_coords_length``, bounds the length in characters of
    ``coords`` on ``/position`` and ``/area``; an overly-long value is rejected
    during request validation, before the dataset is opened.
    """

    reader: type[Reader] = Reader
    path_dependency: Callable[..., str] = DatasetPathParams
    band_dependency: type[CovJSONBandParams] = CovJSONBandParams
    dataset_dependency: type[DatasetParams] = DatasetParams
    image_dependency: type[PartFeatureParams] = PartFeatureParams
    default_max_size: int = DEFAULT_MAX_SIZE
    max_cells: int = DEFAULT_MAX_CELLS
    max_samples: int = DEFAULT_MAX_SAMPLES
    max_coords_length: int = DEFAULT_MAX_COORDS_LENGTH

    def __attrs_post_init__(self) -> None:
        """Validate the configured limits, then register routes (base init).

        Two floors are enforced.

        ``max_cells`` must be at least ``default_max_size ** 2``, because the
        two settings would otherwise contradict each other. When a request names
        no size, the factory supplies the cap on the output's longest side
        itself, and on a large enough source a ``default_max_size`` cap yields a
        ``default_max_size`` square: ``default_max_size ** 2`` cells. A
        ``max_cells`` below that is a ceiling that the factory's own stated
        default could never fit within, so ``default_max_size`` would be dead
        configuration, silently lowered on every unsized request. Refusing to
        build states that contradiction once, at startup, rather than leaving a
        setting that never takes effect.

        The floor is expressed for a single band because a dataset's band count
        arrives with a request rather than with the configuration. It does not
        need to cover more: an unsized read of a many-band source is fitted to
        the ceiling rather than rejected by it, so that path serves at any band
        count, just more coarsely. Sizing ``max_cells`` is therefore a choice
        about resolution, not about what is servable: multiply
        ``default_max_size ** 2`` by the widest read to keep at full resolution
        (the band count of the data served, or the widest ``bidx`` or
        ``expression`` to permit, plus one when reads take an alpha band as the
        mask). The default is exactly this, with an allowance of four bands.

        ``max_coords_length`` must be at least 1. Zero would build a factory
        that rejects every non-empty ``coords``, and a negative value fails deep
        in the validation library with a message naming neither the field nor
        the factory.

        Raises:
            ValueError: If ``max_cells < default_max_size ** 2``, or if
                ``max_coords_length < 1``.
        """
        if self.max_cells < self.default_max_size**2:
            msg = (
                f"max_cells ({self.max_cells}) is below default_max_size ** 2 "
                f"({self.default_max_size**2}). A request specifying no size is "
                "served at up to default_max_size on its longest side, so this "
                "ceiling would reject the factory's own default sizing. Raise "
                f"max_cells to at least {self.default_max_size**2}, or lower "
                "default_max_size. That minimum serves one full-resolution "
                "array. A read allocates one array per selected band, and one "
                "more when it reads an alpha band as the mask, so an N-array "
                "read needs max_cells >= N x default_max_size ** 2 to avoid "
                "being coarsened to fit."
            )
            raise ValueError(msg)

        if self.max_coords_length < 1:
            msg = (
                f"max_coords_length ({self.max_coords_length}) must be >= 1; "
                "zero admits only an empty value, and a negative is not a length."
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
            summary="Get a CoverageJSON Point or MultiPoint coverage for a position",
            description=(
                "Sample the dataset at the position(s) `coords` and return a "
                "CoverageJSON coverage: a WKT `POINT(x y)` yields a Point coverage "
                "at that location, and a WKT `MULTIPOINT((x y), ...)` yields a "
                "MultiPoint coverage with one value per band at each position. A "
                "MULTIPOINT position outside the dataset (or on nodata) becomes a "
                "`null` value rather than an error, so the request still succeeds "
                "even when every position is outside. The number of positions is "
                "capped (see `max_samples`). By default the position is interpreted "
                "in, and the output labeled with, CRS84 (longitude/latitude); pass "
                "`crs` to override. Vertical selection (a `z` level, or a 3-D "
                "`POINT Z` / `MULTIPOINT Z`) is rejected: the 2-D raster backing "
                "cannot sample a vertical level. A `datetime` selector is not yet "
                "honored (this dataset has no temporal dimension)."
            ),
        )
        def position_coverage(
            coords: Annotated[
                str,
                self._coords_query(
                    "Position(s) as WKT: POINT(x y) or "
                    "MULTIPOINT((x y), ...), e.g., POINT(0 0)."
                ),
            ],
            src_path: Annotated[str, Depends(self.path_dependency)],
            band_params: Annotated[CovJSONBandParams, Depends(self.band_dependency)],
            dataset_params: Annotated[DatasetParams, Depends(self.dataset_dependency)],
            crs: Annotated[rasterio.CRS | None, Depends(CRSParams)],
            _vertical: Annotated[None, Depends(reject_vertical_selection)],
            _format: Annotated[None, Depends(validate_covjson_format)],
        ) -> CovJSONResponse:
            parsed = parse_position_coords(coords)

            if isinstance(parsed, InvalidCoords):
                raise BadRequestError(parsed.message)

            read_crs, label_crs = _resolve_crs(crs)
            _validate_label_crs(label_crs)
            band_kwargs = to_kwargs(band_params)
            dataset_kwargs = to_kwargs(dataset_params)
            coverage_input: MultiPointInput | PointInput

            match parsed:
                case MultiPoint():
                    n_positions = len(parsed.positions)

                    if n_positions > self.max_samples:
                        msg = (
                            f"Too many positions: {n_positions} exceeds the maximum "
                            f"of {self.max_samples}."
                        )
                        raise BadRequestError(msg)

                    samples, info = _read_multipoint(
                        self.reader,
                        src_path,
                        parsed,
                        read_crs=read_crs,
                        band_kwargs=band_kwargs,
                        dataset_kwargs=dataset_kwargs,
                    )
                    coverage_input = _build_multipoint_input(
                        samples, info, band_kwargs, parsed, label_crs
                    )
                case Position():
                    point, info = _read_point(
                        self.reader,
                        src_path,
                        parsed,
                        read_crs=read_crs,
                        band_kwargs=band_kwargs,
                        dataset_kwargs=dataset_kwargs,
                    )
                    coverage_input = _build_point_input(
                        point, info, band_kwargs, parsed, label_crs
                    )
                case _:  # pragma: no cover
                    assert_never(parsed)

            return _covjson_response(to_coverage(coverage_input), label_crs)

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
                self._coords_query("Area as WKT, e.g., POLYGON((0 0, 1 0, 1 1, 0 0))."),
            ],
            src_path: Annotated[str, Depends(self.path_dependency)],
            band_params: Annotated[CovJSONBandParams, Depends(self.band_dependency)],
            dataset_params: Annotated[DatasetParams, Depends(self.dataset_dependency)],
            crs: Annotated[rasterio.CRS | None, Depends(CRSParams)],
            stat: Annotated[Stat, Depends(area_stat)],
            _vertical: Annotated[None, Depends(reject_vertical_selection)],
            _format: Annotated[None, Depends(validate_covjson_format)],
        ) -> CovJSONResponse:
            polygon = parse_polygon_wkt(coords)

            if isinstance(polygon, InvalidCoords):
                raise BadRequestError(polygon.message)

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

    def _coords_query(self, description: str) -> Any:
        """Declare a bounded ``coords`` query parameter.

        Every route taking WKT ``coords`` builds its parameter here, so the
        configured length cap reaches each one without being repeated per route.

        Args:
            description: The parameter description shown in the API schema.

        Returns:
            Any: A ``Query`` declaration carrying the configured length cap.
        """
        return Query(description=description, max_length=self.max_coords_length)


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
    returns the image alongside the reader's dataset ``info``. An invalid band
    selection (a malformed expression, or a band the dataset does not have), or an
    oversized output grid, is rejected with ``BadRequestError`` via the guards
    this calls: the cell-count ceiling is checked before the read when the output
    dimensions are known and again after as a backstop for the ``max_size``-bounded
    paths. It bounds ``width * height * bands``, not the grid alone, where ``bands``
    counts every full-size array the read allocates, as
    :func:`_selected_band_count` computes it.

    When no sizing is requested, the longest output dimension is capped here so
    a full-extent read stays bounded (rio-tiler reads native at
    ``max_size=None``, so the cap is applied rather than inherited). The cap is
    ``default_max_size`` fitted to the ceiling for the band count in play, so an
    unsized read of a many-band source comes back coarser instead of rejected. This
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
        max_cells: The hard ceiling on the total cells a read allocates
            (``width * height * bands``, counting the arrays the read makes).

    Returns:
        tuple[ImageData, Info]: The read image and the reader's dataset info.
    """
    requested_kwargs = to_kwargs(image_params)

    with reader(src_path) as src_dst:
        info = src_dst.info()
        _validate_band_selection(band_kwargs, info)

        # A reader can carry its own options, such as nodata or a cutline in
        # vrt_options, and they change what the read allocates just as the
        # request's options do. Combine the two (the request's win on a
        # conflict) and use the result both to count the arrays and for the
        # read itself, so the count always matches the read.
        read_options = {**src_dst.options, **dataset_kwargs}
        bands = _selected_band_count(
            src_dst.dataset,
            band_kwargs,
            reads_through_vrt=_reads_through_vrt(
                src_dst.dataset,
                read_crs,
                vrt_options=read_options.get("vrt_options"),
            ),
            nodata=read_options.get("nodata"),
        )

        # When the caller names no sizing the factory supplies its own cap, fitted
        # to the ceiling for this band count so a many-band read comes back
        # coarser rather than rejected: sizing is what the caller declined to
        # specify, so choosing it is ours to do. A cap the caller *did* name is
        # never lowered; an oversized one is rejected below.
        part_kwargs = requested_kwargs or {
            "max_size": _fitted_max_size(default_max_size, max_cells, bands)
        }

        # Resolve the exact output dimensions rio-tiler will produce (from the
        # width/height/max_size sizing and the read window) and reject an
        # oversized grid before the arrays are allocated. Opening the dataset only
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
            grid_width,
            grid_height,
            bands=bands,
            max_cells=max_cells,
            grid_label="Requested",
        )

        image = src_dst.part(
            bounds,
            dst_crs=read_crs,
            bounds_crs=read_crs,
            **band_kwargs,
            **part_kwargs,
            **read_options,
        )

    # Defense-in-depth backstop: the pre-read guard predicts the output grid, so
    # this re-checks the grid part actually produced. It uses the same array
    # count as the pre-read guard, because image.count holds only the bands
    # returned, not the source or alpha arrays the read also allocated.
    _enforce_cell_ceiling(
        image.width,
        image.height,
        bands=bands,
        max_cells=max_cells,
        grid_label="Output",
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
    read this drops ``max_size`` / cell-count handling entirely. An invalid band
    selection (a malformed expression, or a band the dataset does not have) is
    rejected with ``BadRequestError`` by the guard this calls. A position outside
    the dataset bounds is caught and re-raised as
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
        BadRequestError: If the band selection is invalid, or the position
            falls outside the dataset bounds. The host application's titiler
            exception handlers render this as a 400 response.
    """
    with reader(src_path) as src_dst:
        info = src_dst.info()
        _validate_band_selection(band_kwargs, info)

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


def _read_multipoint(
    reader: type[Reader],
    src_path: str,
    geometry: MultiPoint,
    *,
    read_crs: rasterio.CRS,
    band_kwargs: dict[str, Any],
    dataset_kwargs: dict[str, Any],
) -> tuple[list[PointData | None], Info]:
    """Sample each position of ``geometry`` from ``src_path``, once per position.

    Opens ``src_path`` once and samples every position (interpreting each in
    ``read_crs``), returning one entry per position alongside the reader's dataset
    ``info``. A position outside the dataset bounds is reported as ``None`` rather
    than raised, so an out-of-bounds position becomes a ``null`` value in the
    coverage and the request still succeeds even when every position is outside.
    An invalid band selection (a malformed expression, or a band the dataset
    does not have) is rejected with ``BadRequestError`` by the guard this calls,
    before any position is read, so a bad selection does not read N times before
    failing. The host application's titiler exception handlers render that as a
    400 response.

    Only ``PointOutsideBounds`` is turned into ``None``: a genuine reader error
    (e.g. a read that a ``WarpedVRT`` refuses) still propagates, since silencing
    every failure would hide real faults.

    Args:
        reader: The rio-tiler reader type used to open ``src_path``.
        src_path: The dataset path or URL.
        geometry: The positions to sample, in ``read_crs``.
        read_crs: The CRS the positions are expressed in.
        band_kwargs: Band-selection keyword arguments for ``point`` (indexes or
            expression).
        dataset_kwargs: Dataset-read keyword arguments for ``point`` (nodata,
            unscale, resampling, reprojection).

    Returns:
        tuple[list[PointData | None], Info]: One sample per position (``None`` for
            an out-of-bounds position) and the reader's dataset info.
    """
    with reader(src_path) as src_dst:
        info = src_dst.info()
        _validate_band_selection(band_kwargs, info)

        samples: list[PointData | None] = []

        for x, y in geometry.positions:
            try:
                samples.append(
                    src_dst.point(
                        x,
                        y,
                        coord_crs=read_crs,
                        **band_kwargs,
                        **dataset_kwargs,
                    )
                )
            # Only an out-of-bounds position becomes a null value; any other reader
            # error still propagates.
            except PointOutsideBounds:
                samples.append(None)

    return samples, info


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
    An invalid band selection (a malformed expression, or a band the dataset does
    not have), or a read over the cell-count ceiling, is rejected with
    ``BadRequestError`` by the guards this calls (rendered as a 400 by the host
    application's titiler exception handlers). The ceiling bounds ``width * height *
    bands`` over the polygon's bounding box, not the box alone, where ``bands``
    counts every full-size array ``feature`` allocates, as
    :func:`_selected_band_count` computes it.

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
        max_cells: The hard ceiling on the total cells a read allocates
            (``width * height * bands``, counting the arrays the read makes).

    Returns:
        tuple[ImageData, Info]: The clipped image and the reader's dataset info.
    """
    geometry = {
        "type": "Polygon",
        "coordinates": [[list(vertex) for vertex in ring] for ring in polygon.rings],
    }

    with reader(src_path) as src_dst:
        info = src_dst.info()
        _validate_band_selection(band_kwargs, info)

        # Combined as in _read_bounded_image. Passing the result to feature()
        # matters here because, unlike part(), feature() ignores the reader's
        # own vrt_options unless they are passed in. Without it, the count would
        # include an extra array for a configured cutline, but the read would
        # never apply the cutline.
        read_options = {**src_dst.options, **dataset_kwargs}

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
        bands = _selected_band_count(
            src_dst.dataset,
            band_kwargs,
            reads_through_vrt=_reads_through_vrt(
                src_dst.dataset,
                read_crs,
                vrt_options=read_options.get("vrt_options"),
            ),
            nodata=read_options.get("nodata"),
        )
        _enforce_cell_ceiling(
            grid_width,
            grid_height,
            bands=bands,
            max_cells=max_cells,
            grid_label="Requested",
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
            **read_options,
        )

    # Backstop, as in _read_bounded_image.
    _enforce_cell_ceiling(
        image.width,
        image.height,
        bands=bands,
        max_cells=max_cells,
        grid_label="Output",
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

        >>> from titiler_covjson.geometry import Polygon
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
    width: int,
    height: int,
    *,
    bands: int,
    max_cells: int,
    grid_label: Literal["Requested", "Output"],
) -> None:
    """Reject a grid whose cell count exceeds the ceiling.

    A read allocates one full-size array per band, so the band axis is counted:
    the ceiling bounds ``width * height * bands``, which is what the read costs,
    rather than the footprint of a single band's array. Callers pass the array
    count from :func:`_selected_band_count`, which is not always the number of
    bands returned: an ``expression`` reads every source band it references and
    then derives its output bands from them.

    The pre-read and post-read checks are identical apart from the word that
    labels the grid, so ``grid_label`` supplies it ("Requested" before reading,
    "Output" after).

    Args:
        width: The grid width in cells.
        height: The grid height in cells.
        bands: The number of full-size arrays the read allocates.
        max_cells: The maximum allowed cell count.
        grid_label: The word labeling the grid in the error message.

    Raises:
        BadRequestError: If ``width * height * bands`` exceeds ``max_cells``.

    Examples:
        The same grid fits at one band and does not at two, because each band is
        another array of that size:

        >>> _enforce_cell_ceiling(2, 2, bands=1, max_cells=4, grid_label="Output")
        >>> _enforce_cell_ceiling(2, 2, bands=2, max_cells=4, grid_label="Requested")
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: Requested grid 2x2x2 (w x h x bands)
        = 8 cells exceeds limit of 4.

        The label is the only difference between the pre-read check and the
        post-read backstop, which reports the grid the read returned:

        >>> _enforce_cell_ceiling(3, 3, bands=1, max_cells=4, grid_label="Output")
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: Output grid 3x3x1 (w x h x bands) =
        9 cells exceeds limit of 4.
    """
    if (n_cells := width * height * bands) > max_cells:
        msg = (
            f"{grid_label} grid {width}x{height}x{bands} (w x h x bands) = "
            f"{n_cells} cells exceeds limit of {max_cells}."
        )
        raise BadRequestError(msg)


def _fitted_max_size(default_max_size: int, max_cells: int, bands: int) -> int:
    """Cap an unsized read's longest side so its band count still fits the ceiling.

    ``default_max_size`` is what the factory applies when a request names no
    sizing. On a source with more bands than that default was budgeted for, the
    resulting grid would exceed the cell ceiling, and rejecting it would break
    the one path a caller can take without naming anything. Lowering the cap
    instead keeps that path serving, just coarser, which is what a size cap is
    for.

    The result never rises above ``default_max_size``: a deployer's cap is an
    upper bound, and fitting only ever tightens it. It also never falls below 1,
    so a band count too large to serve even one cell resolves to a 1x1 grid that
    the ceiling then rejects, rather than to a zero-sized read.

    Args:
        default_max_size: The longest output dimension applied when no sizing is
            requested.
        max_cells: The hard ceiling on the total cells a read allocates.
        bands: The number of full-size arrays the read allocates.

    Returns:
        int: The longest output dimension to apply.

    Examples:
        A band count within the ceiling's budget leaves the default alone:

        >>> _fitted_max_size(1024, 1024**2 * 4, bands=4)
        1024

        Beyond it the cap drops so the read still fits:

        >>> _fitted_max_size(1024, 1024**2 * 4, bands=11)
        617
        >>> 617 * 617 * 11 <= 1024**2 * 4
        True

        A band count too large to serve even one cell floors at 1, leaving the
        ceiling to reject it:

        >>> _fitted_max_size(1024, 4, bands=99)
        1
    """
    return max(1, min(default_max_size, math.isqrt(max_cells // bands)))


def _selected_band_count(
    dataset: DatasetReader,
    band_kwargs: dict[str, Any],
    *,
    reads_through_vrt: bool,
    nodata: float | None,
) -> int:
    """Count the full-size arrays a read of this band selection allocates.

    This is the band axis of the cell ceiling, and it counts *arrays*, not
    output bands: the two differ for an expression. A read evaluates an
    expression by first reading every source band it references, then building
    one array per ``;``-separated block from them, and both sets are live while
    the blocks are evaluated. So ``b1+b2+b3`` reads three arrays to return one,
    and counting its single output band would understate the read threefold.

    An alpha band is read as the mask alongside whatever was selected, so it
    allocates one array beyond the selection, even when the selection excludes
    it. Two things decide whether that happens, and neither is
    visible from the selection alone. A ``nodata`` value, the caller's or the
    dataset's own, overrides the mask read, so no alpha array is allocated. And
    a read routed through a ``WarpedVRT``, on any of the conditions
    :func:`_reads_through_vrt` weighs, is given an alpha band even when the
    source has none, so the source's own color interpretation does not settle it
    either.

    Args:
        dataset: The open rasterio dataset, for its band count, color
            interpretation, and nodata value.
        band_kwargs: The band selection ``to_kwargs`` resolved from the
            request's band parameters: ``indexes``, ``expression``, or empty
            when the request supplies no band selector.
        reads_through_vrt: Whether the read is wrapped in a ``WarpedVRT``, as
            :func:`_reads_through_vrt` decides.
        nodata: The caller's nodata override, or ``None`` to use the dataset's.

    Returns:
        int: The number of full-size arrays the read allocates.

    Examples:
        Every example here supplies a band selector, and on that path this
        function reads the ``colorinterp`` and ``nodata`` properties of the
        ``DatasetReader`` it is given. The examples therefore pass a
        ``SimpleNamespace`` carrying those two attributes, as a stand-in for
        the ``DatasetReader``, so that no raster file on disk is needed.

        >>> from types import SimpleNamespace
        >>> dataset = SimpleNamespace(colorinterp=(), nodata=None)
        >>> direct = {"reads_through_vrt": False, "nodata": None}
        >>> through_vrt = {"reads_through_vrt": True, "nodata": None}
        >>> overridden = {"reads_through_vrt": True, "nodata": 0.0}

        An expression allocates one array per source band it references, plus
        one per block it produces:

        >>> _selected_band_count(dataset, {"expression": "b1+b2+b3"}, **direct)
        4
        >>> _selected_band_count(dataset, {"expression": "b1;b1*2"}, **direct)
        3

        An index selection allocates exactly what it names:

        >>> _selected_band_count(dataset, {"indexes": (2, 1)}, **direct)
        2

        A read through a ``WarpedVRT`` allocates one array beyond the selection
        even on this source, which has no alpha band, because the VRT adds one:

        >>> _selected_band_count(dataset, {"indexes": (1,)}, **through_vrt)
        2

        A nodata value overrides the mask read, so the alpha array is not
        allocated after all:

        >>> _selected_band_count(dataset, {"indexes": (1,)}, **overridden)
        1

        A dataset carrying its own alpha band allocates one without a VRT too,
        whatever the selector, because it is read as the mask:

        >>> dataset = SimpleNamespace(colorinterp=(ColorInterp.alpha,), nodata=None)
        >>> _selected_band_count(dataset, {"indexes": (1,)}, **direct)
        2
        >>> _selected_band_count(dataset, {"expression": "b1+b2"}, **direct)
        4
    """
    # The governing rule is rio_tiler.reader.read's `ColorInterp.alpha in
    # dst_colorinterp and nodata is None`. Deliberately not
    # rio_tiler.utils.has_alpha_band, which is wider, also firing on
    # MaskFlags.alpha, and which rio-tiler uses for a different decision:
    # whether the VRT adds a band, not whether one is read as the mask.
    effective_nodata = nodata if nodata is not None else dataset.nodata
    reads_alpha = effective_nodata is None and (
        ColorInterp.alpha in dataset.colorinterp or reads_through_vrt
    )
    alpha = int(reads_alpha)

    if (expression := band_kwargs.get("expression")) is not None:
        # _validate_band_selection has already run at every call site that
        # reaches here, so this call is for the block count alone, not for the
        # error it would raise on a degenerate expression.
        blocks = _expression_band_names(expression)

        return len(parse_expression(expression)) + len(blocks) + alpha

    if (indexes := band_kwargs.get("indexes")) is not None:
        return len(indexes) + alpha

    return len(non_alpha_indexes(dataset)) + alpha


def _reads_through_vrt(
    dataset: DatasetReader,
    read_crs: rasterio.CRS,
    *,
    vrt_options: dict[str, Any] | None,
) -> bool:
    """Report whether a read will be wrapped in a ``WarpedVRT``.

    rio-tiler routes a read through a ``WarpedVRT`` on any of three conditions:
    the read reprojects, the caller supplied VRT options (a cutline, say), or
    the dataset already is one. This matters to the cell ceiling because a
    ``WarpedVRT`` can carry an alpha band the source does not have, and that
    band is read as the mask, costing one array beyond the selection.

    VRT options reach the read through the reader's own ``options``, so a host
    that wires a configured reader can trigger this without any request
    specifying a different CRS.

    Args:
        dataset: The open rasterio dataset the read will run against.
        read_crs: The CRS the read produces.
        vrt_options: The reader's ``vrt_options``, or ``None`` when it has none.

    Returns:
        bool: Whether the read goes through a ``WarpedVRT``.

    Examples:
        Real ``CRS`` values, not their string spellings, because that is what
        the comparison sees in a request:

        >>> from types import SimpleNamespace
        >>> import rasterio
        >>> wgs84 = rasterio.CRS.from_epsg(4326)
        >>> dataset = SimpleNamespace(crs=wgs84)
        >>> _reads_through_vrt(dataset, wgs84, vrt_options=None)
        False
        >>> _reads_through_vrt(dataset, rasterio.CRS.from_epsg(3857), vrt_options=None)
        True

        VRT options force one even when the CRS is unchanged:

        >>> cutline = {"cutline": "POLYGON ((0 0, 1 0, 1 1, 0 0))"}
        >>> _reads_through_vrt(dataset, wgs84, vrt_options=cutline)
        True
    """
    return (
        read_crs != dataset.crs or bool(vrt_options) or isinstance(dataset, WarpedVRT)
    )


def _validate_band_selection(band_kwargs: dict[str, Any], info: Info) -> None:
    """Reject an invalid band selection before reading it.

    This is the single guard every read path calls. It runs two checks:

    - :func:`_validate_band_indexes` on every call, rejecting an index outside
      ``1..band_count`` or one requested more than once. It returns immediately
      when the request supplies no indexes, which is why it needs no condition.
      ``bidx`` and ``parameter-name`` both resolve upstream to ``indexes``.
    - :func:`_validate_expression_bands` when the request supplied an
      ``expression`` (``"b1+b2"``), which identifies its bands in the expression
      text.
      It rejects a reference that is not a band number, or that falls outside
      ``1..band_count``. A band referenced more than once (``b1+b1``) is
      legitimate and allowed.

    The selectors are mutually exclusive, but that is enforced upstream rather
    than here, so the index check is unconditional: a selection carrying both is
    checked for both rather than half-checked. A request with no band selector
    at all passes both checks untouched, because the read returns every band.

    The rules divide by scope, not by selector. Most are properties of the
    request alone and consult no dataset: a blank or missing ``;``-separated
    block, two blocks deriving the same band name, a ``b`` reference whose digits
    are not a band number. Only the range check reads ``info``, for the dataset's
    full band count.

    Both checks raise ``BadRequestError``, which the host application's titiler
    exception handlers render as a 400 response.

    Args:
        band_kwargs: The band selection ``to_kwargs`` resolved from the
            request's band parameters: ``indexes``, ``expression``, or empty
            when the request supplies no band selector.
        info: The reader's dataset info, used for the band count.
    """
    # Issue #104 tracks moving the request-only rules to CovJSONBandParams, which
    # runs before the dataset is opened. Only the range check has to stay here.
    if (expression := band_kwargs.get("expression")) is not None:
        _validate_expression_bands(expression, info)

    # Unconditional rather than an `else` on the expression branch: exclusivity
    # is an invariant of CovJSONBandParams, not of this signature's plain dict,
    # so an `else` would trade a guard for an assumption held a layer up and let
    # a selection carrying both keys skip the index check entirely.
    _validate_band_indexes(band_kwargs.get("indexes"), info)


def _validate_expression_bands(expression: str, info: Info) -> None:
    """Reject an expression referencing a band the dataset does not have.

    rio-tiler resolves an expression's ``b<N>`` references to 1-based band
    indexes and reads those bands directly, so a reference the dataset cannot
    satisfy escapes as a bare ``IndexError`` from rasterio (``b9`` or ``b0`` on
    a two-band source). A reference whose digits are not a number at all fails
    earlier still, as a bare ``ValueError`` from the reference parser
    (``b1+b2b``). Neither exception carries a status mapping, so both render as
    a misleading 500 with no fault the caller can act on. Both are plain
    client input, so this turns them into actionable 400s, as
    :func:`_validate_band_indexes` does for an index selection.

    The band count is the dataset's own, alpha band included: unlike a
    no-selector read, which drops alpha, an expression may reference any band
    the dataset has.

    Args:
        expression: The ``;``-separated band expression.
        info: The reader's dataset info, used for the band count.

    Raises:
        BadRequestError: If a ``;``-separated block is blank, if the
            expression references no bands at all, if two blocks derive the
            same band name, if the expression cannot be parsed for its band
            references, or
            if it references a band outside ``1..band_count``.
    """
    # Our own block rules run first: parse_expression raises rio-tiler's error on
    # a degenerate expression, and ours identifies the fault.
    _expression_band_names(expression)

    try:
        indexes = parse_expression(expression)
    # A non-numeric band reference fails as a bare ValueError from int(), which
    # rio-tiler does not wrap in its own InvalidExpression (that one titiler
    # already maps to a 400, so it is left to propagate).
    except ValueError as exc:
        msg = (
            f"Invalid band reference in expression {expression!r}: every 'b' "
            f"reference must be a band number ({exc})."
        )
        raise BadRequestError(msg) from None

    band_count = len(info.band_descriptions)

    if out_of_range := tuple(sorted(i for i in indexes if i < 1 or band_count < i)):
        msg = (
            f"Requested band index out of range: dataset has {band_count} "
            f"band(s); expression {expression!r} references band(s) "
            f"{out_of_range}."
        )
        raise BadRequestError(msg)


def _validate_band_indexes(indexes: tuple[int, ...] | None, info: Info) -> None:
    """Reject out-of-range or duplicate band indexes before reading.

    An out-of-range index makes rio-tiler raise a bare ``IndexError`` (mapped to
    a misleading 500), and a duplicate index yields duplicate band names that the
    ``CoverageInput`` uniqueness check later rejects with a bare ``ValueError``
    (also a 500). Both are plain client input, so this pre-validation turns them
    into actionable 400s. Covers ``bidx`` and ``parameter-name`` (both resolve to
    indexes). The equivalent rules for an ``expression`` are
    :func:`_validate_expression_bands`.

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
        band_kwargs: The band selection ``to_kwargs`` resolved from the
            request's band parameters: ``indexes``, ``expression``, or empty
            when the request supplies no band selector.
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
        band_kwargs: The band selection ``to_kwargs`` resolved from the
            request's band parameters: ``indexes``, ``expression``, or empty
            when the request supplies no band selector.
        position: The sampled position, in ``crs``.
        crs: The CRS to label the coverage with.

    Returns:
        PointInput: The intermediate representation for the modeler.
    """
    bands = _resolve_read_bands(point, info, band_kwargs)

    return pointdata_to_point_input(point, position=position, bands=bands, crs=crs)


def _build_multipoint_input(
    samples: list[PointData | None],
    info: Info,
    band_kwargs: dict[str, Any],
    geometry: MultiPoint,
    crs: rasterio.CRS,
) -> MultiPointInput:
    """Build a MultiPointInput from per-position samples, resolving band metadata.

    The multipoint analogue of :func:`_build_point_input`. Band metadata is
    resolved from any successful read when there is one (its names and dtype), and
    from the dataset ``info`` alone when every position fell outside the dataset
    (there is then no read to resolve from). The converter aligns each sample to a
    position and stamps the band dtype from the stacked array.

    Args:
        samples: One entry per position (``None`` for an out-of-bounds position).
        info: The reader's dataset info (for source band metadata).
        band_kwargs: The band selection ``to_kwargs`` resolved from the
            request's band parameters: ``indexes``, ``expression``, or empty
            when the request supplies no band selector.
        geometry: The sampled positions, in ``crs``.
        crs: The CRS to label the coverage with.

    Returns:
        MultiPointInput: The intermediate representation for the modeler.
    """
    hit = next((sample for sample in samples if sample is not None), None)
    bands = (
        _resolve_read_bands(hit, info, band_kwargs)
        if hit is not None
        else _resolve_unread_bands(info, band_kwargs)
    )

    return pointdata_to_multipoint_input(
        samples, geometry=geometry, bands=bands, crs=crs
    )


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
        band_kwargs: The band selection ``to_kwargs`` resolved from the
            request's band parameters: ``indexes``, ``expression``, or empty
            when the request supplies no band selector.
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
        band_kwargs: The band selection ``to_kwargs`` resolved from the
            request's band parameters: ``indexes``, ``expression``, or empty
            when the request supplies no band selector.

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


def _resolve_unread_bands(
    info: Info,
    band_kwargs: dict[str, Any],
) -> tuple[BandInfo, ...]:
    """Resolve per-band metadata from dataset ``info`` alone, without a read.

    Answers "which bands would this selection produce?" without reading: for a
    multipoint that sampled every position outside the dataset there is no read
    to resolve bands from. This is the bands *returned*, not the arrays a read
    allocates, which is the different question :func:`_selected_band_count`
    answers for the cell ceiling: an expression returns one band per block while
    reading one array per source band its blocks reference. The result matches
    what
    :func:`_resolve_read_bands` would produce from a successful read of the same
    selection, except that the dtype is the source ``info.dtype`` rather than a
    read's (a read can differ, e.g., unscale casting an integer band to float,
    but an all-outside multipoint has no values for that to matter to: every
    entry is ``null``).

    ``indexes`` select positionally (1-based) from the dataset's bands rather than
    by reconstructing rio-tiler's ``b{ix}`` names, so this holds no second copy of
    that naming rule.

    Args:
        info: The reader's dataset info.
        band_kwargs: The band selection ``to_kwargs`` resolved from the
            request's band parameters: ``indexes``, ``expression``, or empty
            when the request supplies no band selector.

    Returns:
        tuple[BandInfo, ...]: One entry per selected band, in request order.

    Examples:
        >>> from rio_tiler.models import Info
        >>> info = Info(
        ...     bounds=(0.0, 0.0, 1.0, 1.0),
        ...     crs="http://www.opengis.net/def/crs/EPSG/0/4326",
        ...     band_metadata=[("b1", {}), ("b2", {})],
        ...     band_descriptions=[("b1", "red"), ("b2", "nir")],
        ...     dtype="int16",
        ...     nodata_type="None",
        ... )
        >>> [band.name for band in _resolve_unread_bands(info, {})]
        ['b1', 'b2']
        >>> [band.name for band in _resolve_unread_bands(info, {"indexes": (2, 1)})]
        ['b2', 'b1']
        >>> _resolve_unread_bands(info, {"expression": "b1+b2"})[0].name
        'b1+b2'
    """
    if (expression := band_kwargs.get("expression")) is not None:
        return tuple(
            BandInfo(name=name, dtype=info.dtype)
            for name in _expression_band_names(expression)
        )

    bands = band_info_from_reader_info(info)
    indexes = band_kwargs.get("indexes")

    return tuple(bands[i - 1] for i in indexes) if indexes else tuple(bands)


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
        BadRequestError: If a sub-expression is blank, if the expression names
            no bands at all, or if the derived names are not all unique.

    Examples:
        >>> _expression_band_names("b1;b2/b1")
        ('b1', 'b2/b1')

        Empty sub-expressions (e.g., from a trailing ``;``) are dropped, so the
        names stay one-to-one with the bands the read returns:

        >>> _expression_band_names("b1;b2/b1;")
        ('b1', 'b2/b1')

        A blank sub-expression is not dropped, though: it names no band, and
        the position it was written in is reported, counting the dropped empty
        blocks the caller wrote:

        >>> _expression_band_names("b1; ;b2")
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: Blank sub-expression: every
        ';'-separated block must name a band; blank at position 1.

        >>> _expression_band_names(";b1; ;b2")
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: Blank sub-expression: every
        ';'-separated block must name a band; blank at position 2.

        An expression of separators alone names nothing at all:

        >>> _expression_band_names(";")
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: Empty expression: ';' names no
        bands; a ';'-separated list must have at least one block.

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

    # It drops "" but keeps a whitespace-only block, which strips to a nameless
    # band. Reject that as its own fault: left to the duplicate check below, two
    # of them would be misreported as colliding names. Positions come from the
    # caller's own ``;``-split, not from the filtered blocks, whose indexes the
    # dropped empties shift away from what was written.
    if blanks := [
        str(i)
        for i, block in enumerate(expression.split(";"))
        if block and not block.strip()
    ]:
        fault = "blank at position" if len(blanks) == 1 else "blanks at positions"
        msg = (
            "Blank sub-expression: every ';'-separated block must name a band; "
            f"{fault} {', '.join(blanks)}."
        )
        raise BadRequestError(msg)

    # No blocks at all (only separators). Rejected here rather than left to the
    # read: a zero-band selection makes the cell ceiling's cells x bands product
    # zero, which would pass any grid.
    if not names:
        msg = (
            f"Empty expression: {expression!r} names no bands; a ';'-separated "
            "list must have at least one block."
        )
        raise BadRequestError(msg)

    if len(set(names)) != len(names):
        msg = f"Duplicate expression: derived band names must be unique; got {names}."
        raise BadRequestError(msg)

    return names
