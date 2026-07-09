"""CoverageInput: the intermediate representation between TiTiler and CovJSON.

Every endpoint in this package (tile, bbox, point, transect, time series)
reads data through rio-tiler, but each read produces a different kind of
result (``ImageData``, ``PointData``, values assembled across many STAC
items), and none of those objects carries everything a CoverageJSON document
needs: band descriptions and units, timestamps, source geometry, or
collection/item provenance. This module defines the per-domain input variants
that carry it: a shared base plus one frozen dataclass per domain
(:class:`GridInput` now; Point and PointSeries variants follow), grouped under
the :data:`CoverageInput` alias that endpoint code fills from whatever it read
and that the modeler consumes to build covjson-pydantic ``Coverage`` objects.

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
    :func:`imagedata_to_coverage_input` produces from a rio-tiler ``ImageData``.

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


# Alias for the per-domain input union. Currently a single member; the point
# variants (PointInput, PointSeriesInput) join it in #23, at which point the
# modeler's `match` gains cases and `assert_never` enforces exhaustiveness.
CoverageInput = GridInput


def band_info_from_reader_info(info: Info) -> list[BandInfo]:
    """Build per-band metadata from a rio-tiler reader ``info()`` result.

    An ``ImageData`` carries values but little band semantics; descriptions
    and units live on the reader's ``info()``. Use this helper to carry that
    metadata into a :class:`CoverageInput`::

        info = band_info_from_reader_info(reader.info())
        coverage_input = imagedata_to_coverage_input(img, bands=info)

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
        n_bands: Number of bands in the image.
        values: Caller-supplied per-band values, or ``None`` to use
            ``default``.
        default: Values to use when ``values`` is ``None``. Also validated:
            a default drawn from image metadata (e.g., ``img.band_names``)
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
    collection_id: str | None = None,
    item_ids: Sequence[str] | None = None,
) -> GridInput:
    """Convert a rio-tiler ``ImageData`` to a :class:`GridInput`.

    This is the converter used by raster (grid) endpoints: tile, bbox, and
    overview reads all yield an ``ImageData``. The image's masked array is
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
        img: Source image, e.g., from ``Reader.tile()`` or ``Reader.part()``.
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

    return GridInput(
        data=img.array,
        bounds=(left, bottom, right, top),
        crs=resolved_crs,
        bands=_resolve_bands(img, bands, band_names, band_descriptions, band_units),
        collection_id=collection_id,
        item_ids=tuple(item_ids) if item_ids is not None else None,
    )
