"""CoverageInput: the intermediate representation between TiTiler and CovJSON.

Every endpoint in this package reads data through rio-tiler, but each read
produces a different kind of result (``ImageData``, ``PointData``, values
assembled across many STAC items), and none of those objects carries
everything a CoverageJSON document needs: band descriptions and units,
timestamps, source geometry, or collection/item provenance. This module
defines the per-domain input variants
that carry it: a shared base plus one frozen dataclass per domain
(:class:`GridInput`, :class:`PointInput`, and :class:`PolygonInput` now; a
PointSeries variant follows), grouped under the :data:`CoverageInput` alias that
endpoint code fills from
whatever it read and that the modeler consumes to build covjson-pydantic
``Coverage`` objects.

Keeping this intermediate layer separate buys three things: the modeler never
depends on rio-tiler types, changes to the rio-tiler API are contained to the
converter functions in this module, and the modeler's many conversion paths
can be tested from plain numpy arrays without raster files or readers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import numpy as np

from titiler_covjson.reduce import Stat, reduce_each_band

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Any

    import numpy.typing as npt
    import rasterio
    from rio_tiler.models import ImageData, Info, PointData

# Per-band GDAL metadata keys probed (in order) for a unit string. netCDF
# exposes "units", GRIB uses "GRIB_UNIT", and some drivers use "UNITTYPE";
# plain GeoTIFFs typically carry none of these.
_UNIT_TAG_KEYS = ("units", "unit", "UNITTYPE", "GRIB_UNIT")


@dataclass(frozen=True)
class BandInfo:
    """Metadata for a single band/variable.

    Describes what one band of a :class:`CoverageInput` data array means.
    The modeler turns each ``BandInfo`` into a CoverageJSON ``Parameter``
    (observed property plus unit) and uses ``dtype`` to pick the value type
    of the band's range (the CoverageJSON ``NdArray`` holding its values).

    Attributes:
        name: Band/variable identifier (e.g., ``"b1"``).
        description: Human-readable description of the band.
        unit: Unit of measure as a raw UCUM code (Unified Code for Units of
            Measure, e.g., ``"mm"`` or ``"m/s"``); resolvable to a CoverageJSON
            ``Unit`` via :func:`titiler_covjson.helpers.create_unit`. Empty
            when unknown or dimensionless.
        dtype: Declared band dtype; determines whether range values are
            serialized as floats, integers, or strings (see
            :func:`titiler_covjson.helpers.numpy_to_covjson_dtype`).
    """

    name: str
    description: str = ""
    unit: str = ""
    dtype: npt.DTypeLike = np.float32


@dataclass(frozen=True)
class Position:
    """A point location in a coordinate reference system.

    Carries the horizontal coordinates of a single position and, optionally, a
    vertical coordinate. The coordinate reference system is not stored here: it
    lives alongside on the :class:`CoverageInput` variant that holds the position
    (as ``crs``), so ``x``, ``y``, and ``z`` are bare numbers expressed in that
    CRS.

    Attributes:
        x: Easting or longitude, in the holder's CRS.
        y: Northing or latitude, in the holder's CRS.
        z: Vertical coordinate (e.g., height or depth), or ``None`` when the
            position is purely horizontal.
    """

    x: float
    y: float
    z: float | None = None

    def __post_init__(self) -> None:
        """Reject non-finite coordinates at construction.

        A NaN or infinite coordinate names no location on the ground and would
        serialize to a JSON ``null`` in a coverage domain axis, silently
        corrupting the output rather than failing, so it is rejected here where
        the value becomes a :class:`Position`.

        Raises:
            ValueError: If ``x``, ``y``, or a non-``None`` ``z`` is not finite
                (NaN or infinity).
        """
        finite = (self.x, self.y) if self.z is None else (self.x, self.y, self.z)

        if not all(map(math.isfinite, finite)):
            msg = (
                "Position coordinates must be finite (not NaN or infinity); got "
                f"x={self.x}, y={self.y}, z={self.z}."
            )
            raise ValueError(msg)


@dataclass(frozen=True)
class Polygon:
    """A polygon geometry: one exterior ring and zero or more interior rings.

    Carries the polygon's linear rings as ``(exterior, *holes)``. Each ring is a
    sequence of ``(x, y)`` vertices, closed so the first and last vertex coincide.
    The coordinate reference system is not stored here: it lives alongside on the
    :class:`CoverageInput` variant that holds the polygon (as ``crs``), so the
    vertex coordinates are bare numbers expressed in that CRS.

    Attributes:
        rings: The linear rings as ``(exterior, *holes)``. The first ring is the
            exterior boundary; any further rings are interior boundaries (holes).
            Each ring is a tuple of ``(x, y)`` vertices, closed (first vertex
            equal to last).
    """

    rings: tuple[tuple[tuple[float, float], ...], ...]

    def __post_init__(self) -> None:
        """Reject a structurally invalid or non-finite polygon at construction.

        A ring that is unclosed, too short to bound an area, or carries a NaN or
        infinite coordinate names no region on the ground and would produce an
        invalid clip or a silently corrupt domain axis, so it is rejected here
        where the value becomes a :class:`Polygon`.

        Raises:
            ValueError: If there are no rings; if any coordinate is not finite
                (NaN or infinity); if any ring has fewer than four vertices; or if
                any ring is not closed (its first vertex differs from its last).
        """
        if not self.rings:
            msg = "A polygon must have at least one ring (the exterior ring)."
            raise ValueError(msg)

        coordinates = [
            coordinate
            for ring in self.rings
            for vertex in ring
            for coordinate in vertex
        ]

        if not all(map(math.isfinite, coordinates)):
            msg = "Polygon coordinates must be finite (not NaN or infinity)."
            raise ValueError(msg)

        for ring in self.rings:
            if len(ring) < 4:
                msg = (
                    "Each polygon ring must have at least four vertices "
                    f"(a closed triangle); got {len(ring)}."
                )
                raise ValueError(msg)

            if ring[0] != ring[-1]:
                msg = (
                    "Each polygon ring must be closed (first vertex equal to last); "
                    f"got {ring[0]} != {ring[-1]}."
                )
                raise ValueError(msg)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """The ``(minx, miny, maxx, maxy)`` bounding box of the exterior ring.

        The exterior ring (``rings[0]``) encloses any interior holes, so it alone
        determines the extent. A degenerate polygon (a point or an axis-aligned
        line) has ``minx == maxx`` or ``miny == maxy``.

        Returns:
            tuple[float, float, float, float]: The bounding box, in the holder's
                CRS.
        """
        exterior = self.rings[0]
        xs = [x for x, _ in exterior]
        ys = [y for _, y in exterior]

        return min(xs), min(ys), max(xs), max(ys)


@dataclass(frozen=True, eq=False, kw_only=True)
class _CoverageInputBase:
    """Fields and validation shared by every per-domain input variant.

    Holds the data cube (leading axis is always bands), its CRS, the per-band
    metadata, and optional provenance. Concrete subclasses add the
    domain-specific fields (e.g., ``GridInput.bounds``) and a shape contract via
    :meth:`_validate_shape`. The base is never instantiated directly; construct a
    variant such as :class:`GridInput`.

    Instances compare by identity (``eq=False``): comparing masked ``data``
    arrays element-wise is ambiguous, and two inputs holding equal values are not
    meaningfully "the same input". Frozen with tuple collection fields, so
    instances are immutable except for the contents of ``data`` itself.

    Attributes:
        data: Data values as a masked array whose leading axis is bands. Masked
            entries mark nodata and serialize as ``null`` in CovJSON output.
        crs: Coordinate reference system of ``data``.
        bands: Per-band metadata, one entry per band. Resolved at construction:
            when not supplied, generic ``b1, b2, ...`` identities are synthesized,
            so this is always populated afterwards.
        collection_id: Identifier of the source collection, if any.
        item_ids: Identifiers of the source items, if any.
    """

    data: np.ma.MaskedArray[Any, np.dtype[Any]]
    crs: rasterio.CRS
    bands: tuple[BandInfo, ...] = ()
    collection_id: str | None = None
    item_ids: tuple[str, ...] | None = None

    def _validate_shape(self) -> None:
        """Validate ``data``'s shape against the variant's domain contract.

        Overridden by each concrete variant. The base implementation is never
        reached, since the base is not instantiated.

        Raises:
            NotImplementedError: Always, on the abstract base.
        """
        raise NotImplementedError  # pragma: no cover

    def __post_init__(self) -> None:
        """Validate the shape contract and band invariants, then resolve ``bands``.

        Runs the variant's :meth:`_validate_shape`, then the domain-independent
        checks (no empty data axis, band count matching ``data.shape[0]``, unique
        band names), then resolves ``bands``, synthesizing ``b1, b2, ...`` when
        empty (assigned via ``object.__setattr__``, as the dataclass is frozen),
        matching rio-tiler's default band naming.

        Raises:
            ValueError: If the variant's shape contract is violated; if any
                ``data`` axis is empty (size 0, which also catches zero bands); if
                ``bands`` is non-empty and its length does not match
                ``data.shape[0]``; or if two ``bands`` share a name (names become
                CoverageJSON keys, so must be unique).
        """
        self._validate_shape()

        # No data axis may be empty: a zero-size band/height/width/sample axis is
        # a degenerate coverage and would otherwise surface as an opaque error
        # deep in the modeler. (The zero-band case, shape[0] == 0, is caught here.)
        if 0 in self.data.shape:
            msg = (
                "CoverageInput data axes must all be non-empty; "
                f"got shape {self.data.shape}"
            )
            raise ValueError(msg)

        if self.bands and len(self.bands) != self.data.shape[0]:
            msg = (
                f"Number of bands ({len(self.bands)}) does not match "
                f"data.shape[0] ({self.data.shape[0]})"
            )
            raise ValueError(msg)

        # Band names become CoverageJSON range/parameter keys, so they must be
        # unique; duplicates would silently collapse entries in the modeler.
        if self.bands and len({band.name for band in self.bands}) != len(self.bands):
            names = [band.name for band in self.bands]
            msg = f"CoverageInput band names must be unique; got {names}"
            raise ValueError(msg)

        if not self.bands:
            object.__setattr__(
                self,
                "bands",
                tuple(
                    BandInfo(name=f"b{i + 1}", dtype=self.data.dtype)
                    for i in range(self.data.shape[0])
                ),
            )


@dataclass(frozen=True, eq=False, kw_only=True)
class GridInput(_CoverageInputBase):
    """Grid (gridded raster) domain input.

    ``data`` is a 3-D masked array shaped ``(bands, height, width)``; ``bounds``
    gives its spatial extent in ``crs``. This is the variant
    :func:`imagedata_to_grid_input` produces from a rio-tiler ``ImageData``.

    Attributes:
        bounds: Spatial bounds as ``(west, south, east, north)``, in ``crs``.

    Examples:
        Construct one directly when the data does not come from a single
        rio-tiler read:

        >>> import numpy as np
        >>> import rasterio
        >>> cov = GridInput(
        ...     data=np.ma.MaskedArray(np.zeros((1, 2, 2), dtype="float32")),
        ...     bounds=(-10.0, -5.0, 10.0, 5.0),
        ...     crs=rasterio.CRS.from_epsg(4326),
        ...     bands=(BandInfo("b1", unit="mm"),),
        ... )
        >>> cov.data.shape
        (1, 2, 2)
        >>> cov.bands[0].unit
        'mm'
    """

    bounds: tuple[float, float, float, float]

    def _validate_shape(self) -> None:
        """Require 3-D ``(bands, height, width)`` data.

        Raises:
            ValueError: If ``data`` is not 3-D.
        """
        if self.data.ndim != 3:
            msg = (
                "Grid data must have shape (bands, height, width); "
                f"got {self.data.ndim} dimension(s)"
            )
            raise ValueError(msg)


@dataclass(frozen=True, eq=False, kw_only=True)
class PointInput(_CoverageInputBase):
    """Point (single position) domain input.

    ``data`` is a 1-D masked array shaped ``(bands,)``: one sampled value per
    band at a single location. ``position`` gives that location in ``crs``. This
    is the variant :func:`pointdata_to_point_input` produces from a rio-tiler
    ``PointData``, whose ``array`` is already 1-D per band.

    Attributes:
        position: The sampled location, in ``crs``.

    Examples:
        >>> import numpy as np
        >>> import rasterio
        >>> cov = PointInput(
        ...     data=np.ma.MaskedArray(np.array([1.5], dtype="float32")),
        ...     position=Position(-5.0, 2.5),
        ...     crs=rasterio.CRS.from_epsg(4326),
        ...     bands=(BandInfo("b1", unit="mm"),),
        ... )
        >>> cov.data.shape
        (1,)
        >>> cov.position.x, cov.position.y
        (-5.0, 2.5)
    """

    position: Position

    def _validate_shape(self) -> None:
        """Require 1-D ``(bands,)`` data.

        Raises:
            ValueError: If ``data`` is not 1-D.
        """
        if self.data.ndim != 1:
            msg = (
                f"Point data must have shape (bands,); got {self.data.ndim} dimensions"
            )
            raise ValueError(msg)


@dataclass(frozen=True, eq=False, kw_only=True)
class PolygonInput(_CoverageInputBase):
    """Polygon (single polygon, scalar-per-band zonal reduction) domain input.

    ``data`` is a 1-D masked array shaped ``(bands,)``: one reduced scalar per
    band summarizing the raster over ``geometry`` (the same shape contract as
    :class:`PointInput`). ``geometry`` gives that polygon in ``crs``. This is the
    variant :func:`imagedata_to_polygon_input` produces after clipping a raster
    to a polygon and reducing it by a statistic. A masked entry marks a band with
    no valid pixels (an empty or all-nodata polygon), which serializes as
    ``null``.

    Attributes:
        geometry: The polygon the values summarize, in ``crs``.

    Examples:
        >>> import numpy as np
        >>> import rasterio
        >>> cov = PolygonInput(
        ...     data=np.ma.MaskedArray(np.array([1.5], dtype="float32")),
        ...     geometry=Polygon(
        ...         rings=(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)),)
        ...     ),
        ...     crs=rasterio.CRS.from_epsg(4326),
        ...     bands=(BandInfo("b1", unit="mm"),),
        ... )
        >>> cov.data.shape
        (1,)
        >>> len(cov.geometry.rings)
        1
    """

    geometry: Polygon

    def _validate_shape(self) -> None:
        """Require 1-D ``(bands,)`` data.

        Raises:
            ValueError: If ``data`` is not 1-D.
        """
        if self.data.ndim != 1:
            msg = (
                "Polygon data must have shape (bands,); "
                f"got {self.data.ndim} dimensions"
            )
            raise ValueError(msg)


# Alias for the per-domain input union. GridInput, PointInput, and PolygonInput
# are members; the modeler's `match` dispatches on each, and `assert_never` in
# its default arm enforces exhaustiveness as further variants (e.g.,
# PointSeriesInput) join.
CoverageInput = GridInput | PointInput | PolygonInput


def band_info_from_reader_info(info: Info) -> list[BandInfo]:
    """Build per-band metadata from a rio-tiler reader ``info()`` result.

    An ``ImageData`` carries values but little band semantics; descriptions
    and units live on the reader's ``info()``. Use this helper to carry that
    metadata into a :class:`CoverageInput`::

        info = band_info_from_reader_info(reader.info())
        coverage_input = imagedata_to_grid_input(img, bands=info)

    Band names and descriptions come from ``info.band_descriptions``; units
    are probed from the per-band GDAL tags in ``info.band_metadata`` using,
    in order of precedence: ``units``, ``unit``, ``UNITTYPE``, ``GRIB_UNIT``.

    ``BandInfo.dtype`` is per-band, but ``info.dtype`` is a single
    dataset-level value, so every band here is assigned the same dtype.
    Sources with genuinely mixed-dtype bands (uncommon, but possible in
    netCDF) are uniformized to that one dtype. This loader cannot recover
    per-band dtypes. That is acceptable for the single-array raster path.
    Callers needing true per-band dtypes must build :class:`BandInfo` entries
    directly rather than going through this helper.

    Args:
        info: A rio-tiler ``Info`` model, as returned by ``Reader.info()``.

    Returns:
        list[BandInfo]: One entry per band, in band order.

    Examples:
        >>> from rio_tiler.models import Info
        >>> info = Info(
        ...     bounds=(0.0, 0.0, 1.0, 1.0),
        ...     crs="http://www.opengis.net/def/crs/EPSG/0/4326",
        ...     band_metadata=[("b1", {"units": "mm"}), ("b2", {})],
        ...     band_descriptions=[("b1", "precipitation"), ("b2", "")],
        ...     dtype="float32",
        ...     nodata_type="None",
        ... )
        >>> bands = band_info_from_reader_info(info)
        >>> bands[0].name, bands[0].description, bands[0].unit
        ('b1', 'precipitation', 'mm')
        >>> bands[1].description, bands[1].unit
        ('', '')
    """
    # Uniform dataset-level dtype; per-band dtypes for heterogeneous STAC assets
    # combined into one coverage are out of scope here (see
    # docs/04-modeler-converter-design.md, Section 3.1).
    return [
        BandInfo(
            name=name,
            description=description,
            unit=next((str(tags[key]) for key in _UNIT_TAG_KEYS if key in tags), ""),
            dtype=info.dtype,
        )
        for (name, tags), (_, description) in zip(
            info.band_metadata, info.band_descriptions, strict=True
        )
    ]


def _per_band(
    label: str,
    n_bands: int,
    *,
    values: Sequence[str] | None,
    default: Sequence[str],
) -> Sequence[str]:
    """Return ``values`` (or ``default``) validated to one entry per band.

    Args:
        label: Argument name used in the error message.
        n_bands: Number of bands in the read result (an image or point sample).
        values: Caller-supplied per-band values, or ``None`` to use
            ``default``.
        default: Values to use when ``values`` is ``None``. Also validated:
            a default drawn from reader metadata (e.g., ``read.band_names``)
            is not guaranteed to match the band count, since rio-tiler does
            not validate ``band_names`` length at construction.

    Returns:
        Sequence[str]: Per-band values, one entry per band.

    Raises:
        ValueError: If the resolved values do not have one entry per band.
    """
    if values is None:
        values = default

    if len(values) != n_bands:
        msg = f"`{label}` has {len(values)} entries but the data has {n_bands} band(s)"
        raise ValueError(msg)

    return values


def _resolve_bands(
    source: ImageData | PointData,
    bands: Sequence[BandInfo] | None,
    band_names: Sequence[str] | None,
    band_descriptions: Sequence[str] | None,
    band_units: Sequence[str] | None,
) -> tuple[BandInfo, ...]:
    """Resolve per-band metadata for the ``ImageData``/``PointData`` converters.

    ``ImageData`` and ``PointData`` share the ``count``, ``band_names``, and
    ``array`` attributes this reads, so one resolver serves both converters.

    Args:
        source: Source image or point.
        bands: Complete per-band metadata, used as given. Mutually exclusive
            with the per-attribute arguments.
        band_names: Per-band names overriding ``source.band_names``.
        band_descriptions: Per-band descriptions.
        band_units: Per-band UCUM unit codes.

    Returns:
        tuple[BandInfo, ...]: One entry per band.

    Raises:
        ValueError: If ``bands`` is combined with a per-attribute argument,
            or if a per-attribute argument does not have one entry per band.
    """
    overrides = (band_names, band_descriptions, band_units)

    if bands is not None:
        if any(override is not None for override in overrides):
            msg = (
                "Cannot combine `bands` with `band_names`, `band_descriptions`,"
                " or `band_units`"
            )
            raise ValueError(msg)

        return tuple(bands)

    n_bands = source.count
    names = _per_band(
        "band_names",
        n_bands,
        values=band_names,
        default=source.band_names or [],
    )
    descriptions = _per_band(
        "band_descriptions",
        n_bands,
        values=band_descriptions,
        default=[""] * n_bands,
    )
    units = _per_band(
        "band_units",
        n_bands,
        values=band_units,
        default=[""] * n_bands,
    )

    return tuple(
        BandInfo(
            name=name, description=description, unit=unit, dtype=source.array.dtype
        )
        for name, description, unit in zip(names, descriptions, units, strict=True)
    )


def _require_crs(
    source: ImageData | PointData, crs: rasterio.CRS | None
) -> rasterio.CRS:
    """Resolve the CRS to build a coverage with, preferring an explicit override.

    The raster and point converters resolve their CRS the same way: an explicit
    ``crs`` argument wins, else the read result's own ``crs`` is used, and a
    missing CRS is an error, since a coverage cannot be built without one.

    Args:
        source: The read result whose ``crs`` is the fallback.
        crs: An explicit CRS override, or ``None`` to use ``source.crs``.

    Returns:
        rasterio.CRS: The resolved CRS.

    Raises:
        ValueError: If neither ``crs`` nor ``source.crs`` is available.

    Examples:
        >>> import numpy as np
        >>> import rasterio
        >>> from rio_tiler.models import PointData
        >>> point = PointData(np.ma.MaskedArray(np.array([1.0])))
        >>> _require_crs(point, rasterio.CRS.from_epsg(4326)).to_epsg()
        4326
        >>> _require_crs(point, None)
        Traceback (most recent call last):
            ...
        ValueError: PointData has no CRS; pass an explicit `crs` argument
    """
    # Prefer an explicit override with `is not None`, not truthiness: an empty
    # rasterio.CRS() is falsy but a real (if unusable) override, and `crs or ...`
    # would silently discard it in favor of source.crs.
    if (resolved_crs := crs if crs is not None else source.crs) is None:
        msg = f"{type(source).__name__} has no CRS; pass an explicit `crs` argument"
        raise ValueError(msg)

    return resolved_crs


def imagedata_to_grid_input(
    img: ImageData,
    *,
    bands: Sequence[BandInfo] | None = None,
    band_names: Sequence[str] | None = None,
    band_descriptions: Sequence[str] | None = None,
    band_units: Sequence[str] | None = None,
    crs: rasterio.CRS | None = None,
    collection_id: str | None = None,
    item_ids: Sequence[str] | None = None,
) -> GridInput:
    """Convert a rio-tiler ``ImageData`` to a :class:`GridInput`.

    This is the converter for raster (grid) reads: a ``Reader.part``
    bounding-box read yields an ``ImageData``. The image's masked array is
    passed through unchanged: rio-tiler stores ``ImageData.array`` as a 3-D
    ``(bands, height, width)`` masked array with nodata already encoded in
    the mask, so no further nodata handling is required here.

    Band metadata is resolved with the following precedence: an explicit
    ``bands`` sequence; per-attribute overrides (``band_names``,
    ``band_descriptions``, ``band_units``); the image's own ``band_names``
    with empty descriptions and units. To carry reader-level metadata
    (descriptions and units), pass
    ``bands=band_info_from_reader_info(reader.info())``.

    Args:
        img: Source image, e.g., from ``Reader.part()``.
        bands: Complete per-band metadata. Mutually exclusive with
            ``band_names``, ``band_descriptions``, and ``band_units``.
        band_names: Per-band names overriding ``img.band_names``.
        band_descriptions: Per-band descriptions.
        band_units: Per-band UCUM unit codes.
        crs: CRS overriding ``img.crs``.
        collection_id: Identifier of the source collection, if any.
        item_ids: Identifiers of the source items, if any.

    Returns:
        GridInput: The intermediate representation of the image.

    Raises:
        ValueError: If the image has no bounds; if no CRS is available from
            either ``crs`` or ``img.crs``; if ``bands`` is combined with a
            per-attribute override; or if a per-attribute override does not
            have one entry per band.

    Examples:
        >>> import numpy as np
        >>> import rasterio
        >>> from rio_tiler.models import ImageData
        >>> img = ImageData(
        ...     np.zeros((2, 4, 4), dtype="float32"),
        ...     crs=rasterio.CRS.from_epsg(4326),
        ...     bounds=(-10.0, -5.0, 10.0, 5.0),
        ... )
        >>> cov = imagedata_to_grid_input(img)
        >>> cov.bounds
        (-10.0, -5.0, 10.0, 5.0)
        >>> [band.name for band in cov.bands]
        ['b1', 'b2']
        >>> cov.data.shape
        (2, 4, 4)

        Nodata encoded in the image's mask survives conversion as masked
        entries. Here, one band of 2x2 pixels uses -9999.0 as its nodata
        sentinel, and only the top-left pixel holds a real value:

        >>> data = np.array([[[42.0, -9999.0], [-9999.0, -9999.0]]], dtype="float32")
        >>> img = ImageData(
        ...     np.ma.masked_equal(data, -9999.0),
        ...     crs=rasterio.CRS.from_epsg(4326),
        ...     bounds=(-10.0, -5.0, 10.0, 5.0),
        ... )
        >>> cov = imagedata_to_grid_input(img)
        >>> cov.data
        masked_array(
          data=[[[42.0, --],
                 [--, --]]],
          mask=[[[False,  True],
                 [ True,  True]]],
          fill_value=-9999.0,
          dtype=float32)
    """
    if img.bounds is None:
        msg = "ImageData has no bounds; cannot build a CoverageInput"
        raise ValueError(msg)

    resolved_crs = _require_crs(img, crs)
    left, bottom, right, top = img.bounds

    return GridInput(
        data=img.array,
        bounds=(left, bottom, right, top),
        crs=resolved_crs,
        bands=_resolve_bands(img, bands, band_names, band_descriptions, band_units),
        collection_id=collection_id,
        item_ids=tuple(item_ids) if item_ids is not None else None,
    )


def pointdata_to_point_input(
    point: PointData,
    *,
    position: Position,
    bands: Sequence[BandInfo] | None = None,
    crs: rasterio.CRS | None = None,
) -> PointInput:
    """Convert a rio-tiler ``PointData`` to a :class:`PointInput`.

    This is the converter used by point endpoints, which sample a single
    location and yield a ``PointData``. The point's masked array is passed
    through unchanged: rio-tiler stores ``PointData.array`` as a 1-D ``(bands,)``
    masked array with nodata already encoded in the mask, which is exactly the
    :class:`PointInput` data shape, so no reshaping or further nodata handling is
    required here.

    The sampled ``position`` is supplied by the caller rather than taken from
    ``point.coordinates``, so the coverage advertises the position the caller
    requested, in the CRS the coverage is labeled with. A ``PointData`` reports
    its ``coordinates`` in the CRS the read was queried in, which need not be the
    label CRS, so passing ``position`` directly keeps the reported location and
    the domain's referencing consistent, and keeps this converter independent of
    how the reader reports coordinates.

    Band metadata comes from an explicit ``bands`` sequence when given, otherwise
    from the point's own ``band_names`` with empty descriptions and units.

    Args:
        point: Source point, e.g., from ``Reader.point()``.
        position: The sampled location, in ``crs``, to label the coverage with.
        bands: Complete per-band metadata; defaults to the point's ``band_names``.
        crs: CRS overriding ``point.crs``.

    Returns:
        PointInput: The intermediate representation of the point.

    Examples:
        >>> import numpy as np
        >>> import rasterio
        >>> from rio_tiler.models import PointData
        >>> point = PointData(
        ...     np.ma.MaskedArray(np.array([1.5, 2.5], dtype="float32")),
        ...     crs=rasterio.CRS.from_epsg(4326),
        ... )
        >>> cov = pointdata_to_point_input(point, position=Position(-5.0, 2.5))
        >>> cov.data.shape
        (2,)
        >>> [band.name for band in cov.bands]
        ['b1', 'b2']
        >>> cov.position.x, cov.position.y
        (-5.0, 2.5)
    """
    resolved_crs = _require_crs(point, crs)

    return PointInput(
        data=point.array,
        position=position,
        crs=resolved_crs,
        bands=_resolve_bands(point, bands, None, None, None),
    )


def imagedata_to_polygon_input(
    img: ImageData,
    *,
    geometry: Polygon,
    stat: Stat,
    bands: Sequence[BandInfo] | None = None,
    crs: rasterio.CRS | None = None,
) -> PolygonInput:
    """Reduce a polygon-clipped rio-tiler ``ImageData`` to a :class:`PolygonInput`.

    This is the converter used by an area query: a raster clipped to a polygon
    (its outside-polygon and nodata pixels already masked) is reduced to one
    scalar per band by ``stat``. Unlike the grid and point converters, the band
    ``dtype`` is taken from the *reduced* array rather than the source raster: the
    statistic determines the range's value type (``mean`` of an integer raster is
    float; ``count`` is integer), so a supplied or resolved band's own dtype is
    intentionally replaced. This keeps the range's declared type and its values in
    agreement, which the grid and point paths get for free (there the band dtype
    is the array dtype).

    Band names, descriptions, and units come from an explicit ``bands`` sequence
    when given, otherwise from the image's own ``band_names`` with empty
    descriptions and units.

    Args:
        img: The clipped source image, e.g., from ``Reader.feature()``.
        geometry: The polygon the reduced values summarize, in ``crs``.
        stat: The statistic to reduce each band by.
        bands: Complete per-band metadata supplying names/units; the dtype is
            overridden by the reduction. Defaults to the image's ``band_names``.
        crs: CRS overriding ``img.crs``.

    Returns:
        PolygonInput: The intermediate representation of the reduced polygon.

    Examples:
        >>> import numpy as np
        >>> import rasterio
        >>> from rio_tiler.models import ImageData
        >>> from titiler_covjson.reduce import Stat
        >>> img = ImageData(
        ...     np.ma.MaskedArray(np.arange(8, dtype="float32").reshape(2, 2, 2)),
        ...     crs=rasterio.CRS.from_epsg(4326),
        ...     bounds=(0.0, 0.0, 2.0, 2.0),
        ... )
        >>> geometry = Polygon(
        ...     rings=(((0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 0.0)),)
        ... )
        >>> cov = imagedata_to_polygon_input(img, geometry=geometry, stat=Stat.MEAN)
        >>> cov.data.tolist()
        [1.5, 5.5]
    """
    reduced = reduce_each_band(img.array, stat)
    resolved_crs = _require_crs(img, crs)

    # The statistic sets the value type, so stamp every band's dtype from the
    # reduced array; the names/units resolved here are what carry through.
    typed_bands = tuple(
        replace(band, dtype=reduced.dtype)
        for band in _resolve_bands(img, bands, None, None, None)
    )

    return PolygonInput(
        data=reduced,
        geometry=geometry,
        crs=resolved_crs,
        bands=typed_bands,
    )
