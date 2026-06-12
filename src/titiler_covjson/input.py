"""CoverageInput: the intermediate representation between TiTiler and CovJSON.

Every endpoint in this package (tile, bbox, point, transect, time series)
reads data through rio-tiler, but each read produces a different kind of
result (``ImageData``, ``PointData``, values assembled across many STAC
items), and none of those objects carries everything a CoverageJSON document
needs -- band descriptions and units, timestamps, source geometry, or
collection/item provenance. This module defines :class:`CoverageInput`, a
single neutral container that endpoint code fills from whatever it read, and
that the modeler consumes to build covjson-pydantic ``Coverage`` objects.

Keeping this intermediate layer separate buys three things: the modeler never
depends on rio-tiler types, changes to the rio-tiler API are contained to the
converter functions in this module, and the modeler's many conversion paths
can be tested from plain numpy arrays without raster files or readers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Any

    import numpy.typing as npt
    import rasterio
    from rio_tiler.models import ImageData, Info
    from shapely.geometry.base import BaseGeometry

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
    of the band's range -- the CoverageJSON ``NdArray`` holding its values.

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
        nodata: Source nodata value for the band, if known. Informational
            only: nodata cells in :attr:`CoverageInput.data` are marked via
            the array mask, not by comparing against this value.
    """

    name: str
    description: str = ""
    unit: str = ""
    dtype: npt.DTypeLike = np.float32
    nodata: float | None = None


@dataclass(frozen=True, eq=False)
class CoverageInput:
    """Intermediate representation of data destined for CovJSON conversion.

    A ``CoverageInput`` gathers, in one neutral container, everything the
    modeler needs to build a CoverageJSON ``Coverage``: the data values,
    where they are (bounds, CRS, optional geometry), what they mean
    (per-band metadata), when they were observed (optional timestamps), and
    where they came from (optional collection/item identifiers). Endpoint
    code constructs one from rio-tiler results -- see
    :func:`imagedata_to_coverage_input` -- so the modeler depends on neither
    rio-tiler types nor on which endpoint produced the data.

    Instances compare by identity (``eq=False``): comparing masked ``data``
    arrays element-wise is ambiguous, and two inputs that happen to hold
    equal values are not meaningfully "the same input". The dataclass is
    frozen and its collection fields are tuples, so instances are immutable
    except for the contents of the ``data`` array itself, which cannot be made
    immutable.

    Attributes:
        data: Data values as a masked array with shape
            ``(bands, height, width)`` for rasters or ``(bands, n)`` for
            point/profile data. Masked entries mark nodata and serialize as
            ``null`` in CovJSON output.
        bounds: Spatial bounds as ``(west, south, east, north)``.
        crs: Coordinate reference system of ``bounds`` and ``data``.
        geometry: Source geometry for non-grid domains -- e.g., the queried
            point, the transect line, or the aggregation polygon; ``None``
            for gridded rasters.
        bands: Per-band metadata. May be empty, in which case the modeler
            synthesizes generic band identities.
        timestamps: ISO 8601 / RFC 3339 timestamps for temporal data (e.g.,
            one per STAC item in a time series); ``None`` for purely spatial
            data.
        collection_id: Identifier of the source collection, if any.
        item_ids: Identifiers of the source items, if any.

    Examples:
        Construct an input directly when the data does not come from a single
        rio-tiler read:

        >>> import numpy as np
        >>> import rasterio
        >>> cov = CoverageInput(
        ...     data=np.ma.MaskedArray(np.zeros((1, 2, 2), dtype="float32")),
        ...     bounds=(-10.0, -5.0, 10.0, 5.0),
        ...     crs=rasterio.CRS.from_epsg(4326),
        ...     bands=(BandInfo("b1", unit="mm"),),
        ... )
        >>> cov.data.shape
        (1, 2, 2)
        >>> cov.bands[0].unit
        'mm'
        >>> cov.geometry is None  # a gridded raster
        True
    """

    data: np.ma.MaskedArray[Any, np.dtype[Any]]
    bounds: tuple[float, float, float, float]
    crs: rasterio.CRS
    geometry: BaseGeometry | None = None
    bands: tuple[BandInfo, ...] = ()
    timestamps: tuple[str, ...] | None = None
    collection_id: str | None = None
    item_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        """Validate array dimensionality and band count.

        Only domain-independent invariants are checked here; consistency
        between ``geometry``, ``timestamps``, and the array shape depends on
        the target CovJSON domain type and is validated by the modeler.

        Raises:
            ValueError: If ``data`` is not 2-D or 3-D, or if ``bands`` is
                non-empty and its length does not match ``data.shape[0]``.
        """
        if self.data.ndim not in {2, 3} or self.data.shape[0] == 0:
            msg = (
                "CoverageInput data must have shape (bands, height, width) or "
                f"(bands, n), with at least 1 band; got {self.data.ndim} dimension(s) "
                f"with {self.data.shape[0]} band(s)"
            )
            raise ValueError(msg)
        if self.bands and len(self.bands) != self.data.shape[0]:
            msg = (
                f"Number of bands ({len(self.bands)}) does not match "
                f"data.shape[0] ({self.data.shape[0]})"
            )
            raise ValueError(msg)


def band_info_from_reader_info(info: Info) -> list[BandInfo]:
    """Build per-band metadata from a rio-tiler reader ``info()`` result.

    An ``ImageData`` carries values but little band semantics; descriptions,
    units, and nodata live on the reader's ``info()``. Use this helper to
    carry that metadata into a :class:`CoverageInput`::

        info = band_info_from_reader_info(reader.info())
        coverage_input = imagedata_to_coverage_input(img, bands=info)

    Band names and descriptions come from ``info.band_descriptions``; units
    are probed from the per-band GDAL tags in ``info.band_metadata`` using,
    in order of precedence: ``units``, ``unit``, ``UNITTYPE``, ``GRIB_UNIT``.
    The nodata value comes from ``info.nodata_value`` -- a field rio-tiler
    adds dynamically (the ``Info`` model permits extra fields) only when
    ``info.nodata_type == "Nodata"`` -- and is applied to every band.

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
        ...     nodata_type="Nodata",
        ...     nodata_value=-9999,
        ... )
        >>> bands = band_info_from_reader_info(info)
        >>> bands[0].name, bands[0].unit, bands[0].nodata
        ('b1', 'mm', -9999.0)
        >>> bands[1].description, bands[1].unit
        ('', '')
    """
    nodata_value = getattr(info, "nodata_value", None)
    nodata = (
        float(nodata_value)
        if info.nodata_type == "Nodata" and nodata_value is not None
        else None
    )

    return [
        BandInfo(
            name=name,
            description=description,
            unit=next((str(tags[key]) for key in _UNIT_TAG_KEYS if key in tags), ""),
            dtype=info.dtype,
            nodata=nodata,
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
        values: Caller-supplied per-band values, or ``None`` to use
            ``default``.
        default: Values to use when ``values`` is ``None``. Also validated:
            a default drawn from image metadata (e.g., ``img.band_names``)
            is not guaranteed to match the band count, since rio-tiler does
            not validate ``band_names`` length at construction.
        n_bands: Number of bands in the image.

    Returns:
        Sequence[str]: Per-band values, one entry per band.

    Raises:
        ValueError: If the resolved values do not have one entry per band.
    """
    if values is None:
        values = default

    if len(values) != n_bands:
        msg = f"`{label}` has {len(values)} entries but the image has {n_bands} band(s)"
        raise ValueError(msg)

    return values


def _resolve_bands(
    img: ImageData,
    bands: Sequence[BandInfo] | None,
    band_names: Sequence[str] | None,
    band_descriptions: Sequence[str] | None,
    band_units: Sequence[str] | None,
) -> tuple[BandInfo, ...]:
    """Resolve per-band metadata for :func:`imagedata_to_coverage_input`.

    Args:
        img: Source image.
        bands: Complete per-band metadata, used as given. Mutually exclusive
            with the per-attribute arguments.
        band_names: Per-band names overriding ``img.band_names``.
        band_descriptions: Per-band descriptions.
        band_units: Per-band UCUM unit codes.

    Returns:
        tuple[BandInfo, ...]: One entry per image band.

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

    n_bands = img.count
    names = _per_band(
        "band_names",
        n_bands,
        values=band_names,
        default=img.band_names or [],
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
        BandInfo(name=name, description=description, unit=unit, dtype=img.array.dtype)
        for name, description, unit in zip(names, descriptions, units, strict=True)
    )


def imagedata_to_coverage_input(
    img: ImageData,
    *,
    bands: Sequence[BandInfo] | None = None,
    band_names: Sequence[str] | None = None,
    band_descriptions: Sequence[str] | None = None,
    band_units: Sequence[str] | None = None,
    crs: rasterio.CRS | None = None,
    geometry: BaseGeometry | None = None,
    timestamps: Sequence[str] | None = None,
    collection_id: str | None = None,
    item_ids: Sequence[str] | None = None,
) -> CoverageInput:
    """Convert a rio-tiler ``ImageData`` to a :class:`CoverageInput`.

    This is the converter used by raster (grid) endpoints: tile, bbox, and
    overview reads all yield an ``ImageData``. The image's masked array is
    passed through unchanged -- rio-tiler stores ``ImageData.array`` as a 3-D
    ``(bands, height, width)`` masked array with nodata already encoded in
    the mask -- so no further nodata handling is required here.

    Band metadata is resolved with the following precedence: an explicit
    ``bands`` sequence; per-attribute overrides (``band_names``,
    ``band_descriptions``, ``band_units``); the image's own ``band_names``
    with empty descriptions and units. To carry reader-level metadata
    (descriptions, units, nodata), pass
    ``bands=band_info_from_reader_info(reader.info())``.

    Args:
        img: Source image, e.g., from ``Reader.tile()`` or ``Reader.part()``.
        bands: Complete per-band metadata. Mutually exclusive with
            ``band_names``, ``band_descriptions``, and ``band_units``.
        band_names: Per-band names overriding ``img.band_names``.
        band_descriptions: Per-band descriptions.
        band_units: Per-band UCUM unit codes.
        crs: CRS overriding ``img.crs``.
        geometry: Source geometry for non-grid domains.
        timestamps: ISO 8601 / RFC 3339 timestamps for temporal data.
        collection_id: Identifier of the source collection, if any.
        item_ids: Identifiers of the source items, if any.

    Returns:
        CoverageInput: The intermediate representation of the image.

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
        >>> cov = imagedata_to_coverage_input(img)
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
        >>> cov = imagedata_to_coverage_input(img)
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

    if (resolved_crs := crs or img.crs) is None:
        msg = "ImageData has no CRS; pass an explicit `crs` argument"
        raise ValueError(msg)

    left, bottom, right, top = img.bounds

    return CoverageInput(
        data=img.array,
        bounds=(left, bottom, right, top),
        crs=resolved_crs,
        geometry=geometry,
        bands=_resolve_bands(img, bands, band_names, band_descriptions, band_units),
        timestamps=tuple(timestamps) if timestamps is not None else None,
        collection_id=collection_id,
        item_ids=tuple(item_ids) if item_ids is not None else None,
    )
