"""CoverageJSON factory: a titiler.core BaseFactory subclass owning /bbox.

Serves a single dataset as a 2-D CoverageJSON Grid coverage over
``GET {prefix}/bbox/{minx},{miny},{maxx},{maxy}``, reusing titiler's
dependency-injectors for the dataset path, band selection, dataset options, and
output sizing. It reads a bounded region with rio-tiler and funnels the result
through the model layer to a CoverageJSON response.

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

from collections.abc import Callable
from typing import Annotated, Any

import rasterio
from attrs import define
from fastapi import Depends, Path
from rio_tiler.constants import WGS84_CRS
from rio_tiler.io import Reader
from rio_tiler.models import ImageData, Info
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
    to_kwargs,
    validate_covjson_format,
)
from titiler_covjson.helpers import crs_to_ogc_uri
from titiler_covjson.input import (
    GridInput,
    band_info_from_reader_info,
    imagedata_to_coverage_input,
)
from titiler_covjson.modeler import to_coverage
from titiler_covjson.responses import CovJSONResponse

DEFAULT_MAX_SIZE = 1024

# CRS84 is WGS84 with longitude/latitude axis order (the CovJSON-preferred label
# for geographic output). It is distinct from EPSG:4326 (latitude/longitude
# authority order) even though both denote the same positions.
CRS84 = rasterio.CRS.from_string("OGC:CRS84")


@define(kw_only=True)
class CovJSONFactory(BaseFactory):
    """Serve a single dataset as a CoverageJSON Grid over ``/bbox``.

    Collaborators are constructor fields (the composition root): the reader and
    the titiler dependency-injectors for path, band selection, dataset options,
    and output sizing. Two sizing knobs are configurable: ``default_max_size``,
    the longest output dimension applied when no sizing is requested (a request
    still succeeds, just coarser), and ``max_cells``, a hard ceiling on the
    output cell count that rejects an oversized request.
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
            ValueError: If ``max_cells < default_max_size ** 2`` -- a full-extent
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
        """Register the ``/bbox`` route."""

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

            # Pre-read ceiling guard: when width and height are both given, the output
            # cell count is known before reading, so reject an oversized request without
            # allocating a huge array. Other paths are bounded by max_size and caught by
            # the post-read backstop below.
            if image_params.width and image_params.height:
                _enforce_cell_ceiling(
                    image_params.width,
                    image_params.height,
                    max_cells=self.max_cells,
                    grid_label="Requested",
                )

            # When no sizing param is supplied, apply the downsampling default so a
            # full-extent read is bounded. rio-tiler reads native at max_size=None, so
            # the cap is applied here rather than inherited. This relies on
            # PartFeatureParams carrying only sizing fields (max_size/height/width): an
            # empty to_kwargs then means "no sizing requested". If a non-sizing field is
            # ever added upstream, revisit so it does not defeat the default.
            part_kwargs = to_kwargs(image_params) or {"max_size": self.default_max_size}

            read_crs, label_crs = _resolve_crs(crs)
            band_kwargs = to_kwargs(band_params)

            with self.reader(src_path) as src_dst:
                info = src_dst.info()
                _validate_band_indexes(band_kwargs.get("indexes"), info)
                image = src_dst.part(
                    (minx, miny, maxx, maxy),
                    dst_crs=read_crs,
                    bounds_crs=read_crs,
                    **band_kwargs,
                    **part_kwargs,
                    **to_kwargs(dataset_params),
                )

            # Post-read backstop for every path not guarded pre-read.
            _enforce_cell_ceiling(
                image.width, image.height, max_cells=self.max_cells, grid_label="Output"
            )

            grid_input = _build_grid_input(image, info, band_kwargs, label_crs)
            coverage = to_coverage(grid_input)
            headers = {"Content-Crs": f"<{crs_to_ogc_uri(label_crs)}>"}

            return CovJSONResponse(
                content=coverage.model_dump_json(exclude_none=True),
                headers=headers,
            )


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
    """Reject out-of-range band indexes before reading.

    rio-tiler raises a bare ``IndexError`` for an out-of-range index, which the
    exception handlers map to a misleading 500; this pre-validation turns it
    into an actionable 400.

    Args:
        indexes: The requested 1-based band indexes, or ``None``.
        info: The reader's dataset info, used for the band count.

    Raises:
        BadRequestError: If any index is outside ``1..band_count``.
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


def _build_grid_input(
    image: ImageData,
    info: Info,
    band_kwargs: dict[str, Any],
    crs: rasterio.CRS,
) -> GridInput:
    """Build a GridInput, naming expression bands and aligning source metadata.

    For an ``expression`` result, each derived band is named for its
    sub-expression. Otherwise the reader's ``info()`` metadata is subset to the
    bands actually returned.

    Args:
        image: The read image.
        info: The reader's dataset info (for source band metadata).
        band_kwargs: The resolved band selection (``{}`` / ``indexes`` /
            ``expression``).
        crs: The CRS to label the coverage with.

    Returns:
        GridInput: The intermediate representation for the modeler.
    """
    if (expression := band_kwargs.get("expression")) is not None:
        band_names = _expression_band_names(expression)

        return imagedata_to_coverage_input(image, band_names=band_names, crs=crs)

    by_name = {band.name: band for band in band_info_from_reader_info(info)}
    bands = tuple(by_name[name] for name in image.band_names if name in by_name)

    return imagedata_to_coverage_input(image, bands=bands or None, crs=crs)


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
        >>> _expression_band_names("b1;b1")
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: Duplicate expression: derived
        band names must be unique; got ('b1', 'b1').
    """
    names = tuple(name for part in expression.split(";") if (name := part.strip()))

    if len(set(names)) != len(names):
        msg = f"Duplicate expression: derived band names must be unique; got {names}."
        raise BadRequestError(msg)

    return names
