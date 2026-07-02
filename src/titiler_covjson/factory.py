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

import math
from collections.abc import Callable
from typing import Annotated, Any

import rasterio
from attrs import define
from fastapi import Depends, Path
from rasterio import windows
from rasterio.io import DatasetReader
from rio_tiler.constants import WGS84_CRS
from rio_tiler.io import Reader
from rio_tiler.models import ImageData, Info
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
            coverage = to_coverage(grid_input)
            headers = {"Content-Crs": f"<{crs_to_ogc_uri(label_crs)}>"}

            return CovJSONResponse(
                content=coverage.model_dump_json(exclude_none=True),
                headers=headers,
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


def _resolve_grid_dimensions(
    dataset: DatasetReader,
    bounds: tuple[float, float, float, float],
    *,
    read_crs: rasterio.CRS,
    width: int | None,
    height: int | None,
    max_size: int | None,
) -> tuple[int, int]:
    """Resolve the output grid dimensions rio-tiler's ``part`` will produce.

    Mirrors the dimension logic in ``rio_tiler.reader.part`` so the resulting
    cell count can be checked against the ceiling before the array is read:

    - both ``width`` and ``height`` given: returned unchanged (``part`` ignores
      ``max_size`` then);
    - exactly one given: the other is derived from the read window's aspect
      ratio (``part`` upsamples the given dimension);
    - neither given: ``max_size`` caps the longer axis of the read window, or,
      when ``max_size`` is also ``None``, the native window is read.

    A test locks this in step with ``part``, so a change to its derivation fails
    loudly rather than silently defeating the cell-count ceiling.

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

        >>> _resolve_grid_dimensions(
        ...     None, (0, 0, 1, 1), read_crs=None, width=256, height=128,
        ...     max_size=None,
        ... )
        (256, 128)

        Every other case (a lone dimension, or a ``max_size`` cap) is derived
        from the read window, so it needs an open dataset and is not shown here.
    """

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

    # Take the aspect ratio first, then multiply, matching part's exact float
    # association so the derived dimension is bit-identical to what it produces.
    ratio = window_height / window_width

    if width is not None:
        return width, math.ceil(width * ratio)

    if height is not None:
        return math.ceil(height / ratio), height

    if max_size is None:
        return max(1, round(window_width)), max(1, round(window_height))

    return _scale_to_max_size(max_size, round(window_width), round(window_height))


def _scale_to_max_size(
    max_size: int, window_width: int, window_height: int
) -> tuple[int, int]:
    """Cap the longer window axis at ``max_size``, preserving the aspect ratio.

    Replicates rio-tiler's ``max_size`` handling: when the window already fits,
    it is returned unchanged, otherwise the longer axis is set to ``max_size``
    and the shorter is scaled to match (rounding up). A test locks this in step
    with ``rio_tiler.reader.part``, so a change to its behavior fails loudly.

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
