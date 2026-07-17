"""Tests for the input layer: the CoverageInput variants and converters."""

from __future__ import annotations

import dataclasses
from typing import Any

import numpy as np
import pytest
import rasterio
from rio_tiler.models import ImageData, Info, PointData

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
from titiler_covjson.reduce import Stat

BOUNDS = (-10.0, -5.0, 10.0, 5.0)
CRS_EPSG_3857 = rasterio.CRS.from_epsg(3857)
POSITION = Position(1.0, 2.0)
# A unit square exterior ring (closed), no holes.
SQUARE = Polygon(rings=(((0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0), (0.0, 0.0)),))


def make_image(
    array: np.ndarray[Any, np.dtype[Any]] | None = None,
    **kwargs: Any,
) -> ImageData:
    """Build an ImageData with CRS and bounds by default.

    Args:
        array: Source array; defaults to a 2-band (2, 4, 4) float32 array.
        kwargs: Extra ImageData keyword arguments.

    Returns:
        ImageData: The assembled image.
    """
    if array is None:
        array = np.arange(32, dtype="float32").reshape(2, 4, 4)

    kwargs.setdefault("crs", CRS_EPSG_3857)
    kwargs.setdefault("bounds", BOUNDS)

    return ImageData(array, **kwargs)


def make_point(
    array: np.ndarray[Any, np.dtype[Any]] | None = None,
    **kwargs: Any,
) -> PointData:
    """Build a PointData with a CRS by default.

    Args:
        array: Source array; defaults to a 2-band (2,) float32 array.
        kwargs: Extra PointData keyword arguments.

    Returns:
        PointData: The assembled point.
    """
    if array is None:
        array = np.arange(2, dtype="float32")

    kwargs.setdefault("crs", CRS_EPSG_3857)

    return PointData(array, **kwargs)


def masked_input(data: np.ma.MaskedArray[Any, np.dtype[Any]]) -> GridInput:
    """Build a minimal GridInput around the given array.

    Args:
        data: The masked data array, shaped ``(bands, height, width)``.

    Returns:
        GridInput: An input with default bounds and CRS.
    """
    return GridInput(data=data, bounds=BOUNDS, crs=CRS_EPSG_3857)


def point_input(data: np.ma.MaskedArray[Any, np.dtype[Any]]) -> PointInput:
    """Build a minimal PointInput around the given array.

    Args:
        data: The masked data array, shaped ``(bands,)``.

    Returns:
        PointInput: An input with a default position and CRS.
    """
    return PointInput(data=data, position=POSITION, crs=CRS_EPSG_3857)


def polygon_input(data: np.ma.MaskedArray[Any, np.dtype[Any]]) -> PolygonInput:
    """Build a minimal PolygonInput around the given array.

    Args:
        data: The masked data array, shaped ``(bands,)``.

    Returns:
        PolygonInput: An input with a default polygon geometry and CRS.
    """
    return PolygonInput(data=data, geometry=SQUARE, crs=CRS_EPSG_3857)


class TestBandInfo:
    """Test the BandInfo dataclass."""

    def test_defaults(self) -> None:
        """Only the name is required; everything else has sensible defaults."""
        band = BandInfo("b1")

        assert band.name == "b1"
        assert band.description == ""
        assert band.unit == ""
        assert band.dtype is np.float32

    def test_frozen(self) -> None:
        """BandInfo is immutable."""
        band = BandInfo("b1")

        with pytest.raises(dataclasses.FrozenInstanceError):
            band.name = "b2"  # type: ignore[misc]


class TestGridInput:
    """Test the GridInput dataclass."""

    def test_minimal_construction(self) -> None:
        """A 3-D masked array with bounds and CRS is sufficient."""
        cov = masked_input(np.ma.MaskedArray(np.zeros((1, 2, 2))))

        # bands is resolved at construction, so it is never empty afterwards.
        assert [band.name for band in cov.bands] == ["b1"]
        assert cov.collection_id is None
        assert cov.item_ids is None

    @pytest.mark.parametrize(
        "shape", [(4,), (2, 5), (1, 2, 2, 2)], ids=("1D", "2D", "4D")
    )
    def test_non_3d_data_raises(self, shape: tuple[int, ...]) -> None:
        """GridInput requires 3-D (bands, height, width) data."""
        with pytest.raises(ValueError, match="Grid data must have shape"):
            masked_input(np.ma.MaskedArray(np.zeros(shape)))

    def test_band_count_mismatch_raises(self) -> None:
        """A non-empty bands tuple must match data.shape[0]."""
        with pytest.raises(ValueError, match="does not match"):
            GridInput(
                data=np.ma.MaskedArray(np.zeros((2, 2, 2))),
                bounds=BOUNDS,
                crs=CRS_EPSG_3857,
                bands=(BandInfo("b1"),),
            )

    def test_band_count_match_passes(self) -> None:
        """A bands tuple with one entry per band is accepted."""
        cov = GridInput(
            data=np.ma.MaskedArray(np.zeros((2, 2, 2))),
            bounds=BOUNDS,
            crs=CRS_EPSG_3857,
            bands=(BandInfo("b1"), BandInfo("b2")),
        )

        assert [band.name for band in cov.bands] == ["b1", "b2"]

    def test_duplicate_band_names_raises(self) -> None:
        """Band names become CovJSON keys, so they must be unique."""
        with pytest.raises(ValueError, match="unique"):
            GridInput(
                data=np.ma.MaskedArray(np.zeros((2, 2, 2))),
                bounds=BOUNDS,
                crs=CRS_EPSG_3857,
                bands=(BandInfo("x"), BandInfo("x")),
            )

    @pytest.mark.parametrize("shape", [(0, 2, 2), (1, 0, 2), (1, 2, 0)])
    def test_empty_data_axis_raises(self, shape: tuple[int, ...]) -> None:
        """A zero-size data axis (including zero bands) is rejected early."""
        with pytest.raises(ValueError, match="non-empty"):
            masked_input(np.ma.MaskedArray(np.zeros(shape)))

    def test_frozen(self) -> None:
        """GridInput is immutable."""
        cov = masked_input(np.ma.MaskedArray(np.zeros((1, 2, 2))))

        with pytest.raises(dataclasses.FrozenInstanceError):
            cov.data = np.ma.MaskedArray(np.zeros((1, 2, 2)))  # type: ignore[misc]


class TestBandSynthesis:
    """Test construction-time resolution of GridInput.bands."""

    def test_supplied_bands_kept_unchanged(self) -> None:
        """When bands are supplied, they are stored as-is (no synthesis)."""
        bands = (BandInfo("red"), BandInfo("nir"))
        cov = GridInput(
            data=np.ma.MaskedArray(np.zeros((2, 2, 2))),
            bounds=BOUNDS,
            crs=CRS_EPSG_3857,
            bands=bands,
        )

        assert cov.bands == bands

    def test_absent_bands_synthesized_at_construction(self) -> None:
        """With no bands, one b1, b2, ... entry per band is synthesized."""
        cov = masked_input(np.ma.MaskedArray(np.zeros((3, 2, 2), dtype="int16")))

        assert [band.name for band in cov.bands] == ["b1", "b2", "b3"]
        # Synthesized bands share the data's dtype, so range typing is correct.
        assert all(band.dtype == np.dtype("int16") for band in cov.bands)


class TestImagedataToCoverageInput:
    """Test conversion from rio-tiler ImageData."""

    def test_basic_conversion(self) -> None:
        """Array, bounds, CRS, band names, and dtype all carry over."""
        img = make_image()
        cov = imagedata_to_grid_input(img)

        assert cov.data is img.array
        assert cov.bounds == BOUNDS
        assert cov.crs == CRS_EPSG_3857
        assert isinstance(cov.bands, tuple)
        assert [band.name for band in cov.bands] == ["b1", "b2"]
        assert all(band.dtype == np.dtype("float32") for band in cov.bands)

    def test_mask_propagation(self) -> None:
        """Masked entries in the source array survive conversion."""
        mask = np.zeros((2, 4, 4), dtype=bool)
        mask[0, 0, 0] = True
        arr: np.ma.MaskedArray[Any, np.dtype[Any]] = np.ma.MaskedArray(
            np.ones((2, 4, 4), dtype="float32"), mask=mask
        )
        cov = imagedata_to_grid_input(make_image(arr))

        cov_mask = np.ma.getmaskarray(cov.data)
        assert cov_mask[0, 0, 0]
        assert not cov_mask[1, 0, 0]
        assert cov.data[1, 0, 0] == 1.0

    def test_plain_array_gets_materialized_mask(self) -> None:
        """A plain ndarray input still yields a fully materialized mask."""
        img = make_image(np.ones((2, 4, 4), dtype="float32"))
        cov = imagedata_to_grid_input(img)

        assert np.ma.getmaskarray(cov.data).shape == (2, 4, 4)
        assert not cov.data.mask.any()

    def test_nodata_derived_mask(self) -> None:
        """A nodata-sentinel mask (as built by Reader(..., nodata=...)) survives."""
        data = np.full((1, 4, 4), -9999.0, dtype="float32")
        data[0, 1, 1] = 42.0
        arr = np.ma.masked_equal(data, -9999.0)
        cov = imagedata_to_grid_input(make_image(arr))

        assert cov.data.mask.sum() == 15
        assert cov.data[0, 1, 1] == 42.0

    def test_2d_array_coerced_to_3d(self) -> None:
        """Single-band 2-D input becomes (1, h, w) with one band."""
        cov = imagedata_to_grid_input(make_image(np.zeros((4, 4), dtype="float32")))

        assert cov.data.shape == (1, 4, 4)
        assert [band.name for band in cov.bands] == ["b1"]

    def test_band_attribute_overrides(self) -> None:
        """band_names/band_descriptions/band_units kwargs override defaults."""
        cov = imagedata_to_grid_input(
            make_image(),
            band_names=["red", "nir"],
            band_descriptions=["Red band", "Near infrared"],
            band_units=["%", "%"],
        )

        assert [band.name for band in cov.bands] == ["red", "nir"]
        assert [band.description for band in cov.bands] == ["Red band", "Near infrared"]
        assert [band.unit for band in cov.bands] == ["%", "%"]

    @pytest.mark.parametrize("kwarg", ["band_names", "band_descriptions", "band_units"])
    def test_wrong_length_override_raises(self, kwarg: str) -> None:
        """Each per-attribute override must have one entry per band."""
        kwargs: dict[str, Any] = {kwarg: ["only-one"]}

        with pytest.raises(ValueError, match=f"`{kwarg}` has 1 entries"):
            imagedata_to_grid_input(make_image(), **kwargs)

    def test_mismatched_img_band_names_raises(self) -> None:
        """Defaulted names from a malformed ImageData get a clear error.

        rio-tiler does not validate band_names length at construction, so the
        defaulted names must be length-checked like caller-supplied ones.
        """
        img = make_image(band_names=["only-one"])

        with pytest.raises(ValueError, match="`band_names` has 1 entries"):
            imagedata_to_grid_input(img)

    def test_bands_kwarg_applied(self) -> None:
        """An explicit bands sequence is stored as a tuple, entries unchanged."""
        bands = [BandInfo("a", description="alpha"), BandInfo("b", unit="m")]
        cov = imagedata_to_grid_input(make_image(), bands=bands)

        assert cov.bands == tuple(bands)

    @pytest.mark.parametrize("band_names", [["x", "y"], []], ids=("non-empty", "empty"))
    def test_bands_kwarg_conflicts_with_overrides(self, band_names: list[str]) -> None:
        """bands= is mutually exclusive with overrides, even empty ones."""
        with pytest.raises(ValueError, match="Cannot combine `bands`"):
            imagedata_to_grid_input(
                make_image(),
                bands=[BandInfo("a"), BandInfo("b")],
                band_names=band_names,
            )

    def test_bands_kwarg_wrong_length_raises(self) -> None:
        """A bands list of the wrong length fails GridInput validation."""
        with pytest.raises(ValueError, match="does not match"):
            imagedata_to_grid_input(make_image(), bands=[BandInfo("a")])

    def test_missing_crs_raises(self) -> None:
        """An image without a CRS is rejected."""
        with pytest.raises(ValueError, match="no CRS"):
            imagedata_to_grid_input(make_image(crs=None))

    def test_crs_kwarg_overrides(self) -> None:
        """An explicit crs= kwarg wins over (or substitutes for) img.crs."""
        wgs84 = rasterio.CRS.from_epsg(4326)

        assert imagedata_to_grid_input(make_image(crs=None), crs=wgs84).crs == wgs84
        assert imagedata_to_grid_input(make_image(), crs=wgs84).crs == wgs84

    def test_missing_bounds_raises(self) -> None:
        """An image without bounds is rejected."""
        with pytest.raises(ValueError, match="no bounds"):
            imagedata_to_grid_input(make_image(bounds=None))

    def test_passthrough_provenance_fields(self) -> None:
        """collection_id and item_ids carry over to the GridInput."""
        cov = imagedata_to_grid_input(
            make_image(),
            collection_id="my-collection",
            item_ids=["item-1", "item-2"],
        )

        assert cov.collection_id == "my-collection"
        assert cov.item_ids == ("item-1", "item-2")


class TestBandInfoFromReaderInfo:
    """Test band metadata extraction from rio-tiler reader info."""

    @staticmethod
    def make_info(**kwargs: Any) -> Info:
        """Build an Info model with two bands and overridable fields.

        Args:
            kwargs: Overrides for the default Info fields.

        Returns:
            Info: The assembled reader info.
        """
        defaults: dict[str, Any] = {
            "bounds": (0.0, 0.0, 1.0, 1.0),
            "crs": "http://www.opengis.net/def/crs/EPSG/0/4326",
            "band_metadata": [("b1", {"units": "mm"}), ("b2", {})],
            "band_descriptions": [("b1", "precipitation"), ("b2", "")],
            "dtype": "int16",
            "nodata_type": "None",
        }

        return Info(**(defaults | kwargs))

    def test_extraction(self) -> None:
        """Names, descriptions, units, and dtype are extracted."""
        bands = band_info_from_reader_info(self.make_info())

        assert [band.name for band in bands] == ["b1", "b2"]
        assert [band.description for band in bands] == ["precipitation", ""]
        assert [band.unit for band in bands] == ["mm", ""]
        assert all(band.dtype == "int16" for band in bands)

    def test_dtype_is_uniform_dataset_level(self) -> None:
        """Every band gets the single dataset-level ``info.dtype``.

        ``info.dtype`` is dataset-level while ``BandInfo.dtype`` is per-band,
        so this loader cannot deliver heterogeneous per-band dtypes: all bands
        are uniformized to the one reported dtype. Pinning that documented
        behavior guards against a future change that silently sources dtype
        per band from somewhere the reader does not actually populate.
        """
        bands = band_info_from_reader_info(self.make_info(dtype="float64"))

        assert {str(band.dtype) for band in bands} == {"float64"}

    @pytest.mark.parametrize("key", ["units", "unit", "UNITTYPE", "GRIB_UNIT"])
    def test_unit_key_variants(self, key: str) -> None:
        """Each supported GDAL unit tag key is picked up."""
        info = self.make_info(band_metadata=[("b1", {key: "K"}), ("b2", {})])

        assert band_info_from_reader_info(info)[0].unit == "K"

    @pytest.mark.parametrize(
        ("winner", "loser"),
        [("units", "unit"), ("unit", "UNITTYPE"), ("UNITTYPE", "GRIB_UNIT")],
    )
    def test_unit_key_precedence(self, winner: str, loser: str) -> None:
        """Each unit tag key wins over the next, locking in the full ordering.

        Sweeping the adjacent pairs of ``_UNIT_TAG_KEYS`` is cheap insurance
        against an accidental reorder of the precedence list.
        """
        info = self.make_info(
            band_metadata=[("b1", {loser: "ft", winner: "m"}), ("b2", {})]
        )

        assert band_info_from_reader_info(info)[0].unit == "m"

    def test_composes_with_imagedata_converter(self) -> None:
        """bands=band_info_from_reader_info(info) works end-to-end."""
        cov = imagedata_to_grid_input(
            make_image(), bands=band_info_from_reader_info(self.make_info())
        )

        assert [band.name for band in cov.bands] == ["b1", "b2"]
        assert [band.unit for band in cov.bands] == ["mm", ""]


class TestPosition:
    """Test the Position value type."""

    def test_minimal_construction(self) -> None:
        """x and y are required; z defaults to None."""
        pos = Position(1.5, -2.5)

        assert pos.x == 1.5
        assert pos.y == -2.5
        assert pos.z is None

    def test_optional_z(self) -> None:
        """A vertical coordinate is carried when supplied."""
        pos = Position(1.5, -2.5, z=100.0)

        assert pos.z == 100.0

    @pytest.mark.parametrize(
        ("x", "y", "z"),
        [
            (float("nan"), 2.0, None),
            (float("inf"), 2.0, None),
            (1.0, float("-inf"), None),
            (1.0, 2.0, float("nan")),
            (1.0, 2.0, float("inf")),
        ],
        ids=("x-nan", "x-inf", "y-neg-inf", "z-nan", "z-inf"),
    )
    def test_non_finite_coordinate_raises(
        self, x: float, y: float, z: float | None
    ) -> None:
        """A NaN or infinite x, y, or z is rejected at construction."""
        with pytest.raises(ValueError, match="must be finite"):
            Position(x, y, z=z)

    def test_frozen(self) -> None:
        """Position is immutable."""
        pos = Position(1.5, -2.5)

        with pytest.raises(dataclasses.FrozenInstanceError):
            pos.x = 0.0  # type: ignore[misc]


class TestPointInput:
    """Test the PointInput dataclass."""

    def test_minimal_construction(self) -> None:
        """A 1-D masked array with a position and CRS is sufficient."""
        cov = point_input(np.ma.MaskedArray(np.zeros(2)))

        # bands is resolved at construction, so it is never empty afterwards.
        assert [band.name for band in cov.bands] == ["b1", "b2"]
        assert cov.position == POSITION
        assert cov.collection_id is None
        assert cov.item_ids is None

    @pytest.mark.parametrize(
        "shape",
        [(2, 1), (2, 3), (2, 1, 1), ()],
        ids=("2D-column", "2D", "3D", "0D"),
    )
    def test_non_1d_data_raises(self, shape: tuple[int, ...]) -> None:
        """PointInput requires 1-D (bands,) data."""
        with pytest.raises(ValueError, match="Point data must have shape"):
            point_input(np.ma.MaskedArray(np.zeros(shape)))

    def test_band_count_mismatch_raises(self) -> None:
        """A non-empty bands tuple must match data.shape[0]."""
        with pytest.raises(ValueError, match="does not match"):
            PointInput(
                data=np.ma.MaskedArray(np.zeros(2)),
                position=POSITION,
                crs=CRS_EPSG_3857,
                bands=(BandInfo("b1"),),
            )

    def test_duplicate_band_names_raises(self) -> None:
        """Band names become CovJSON keys, so they must be unique."""
        with pytest.raises(ValueError, match="unique"):
            PointInput(
                data=np.ma.MaskedArray(np.zeros(2)),
                position=POSITION,
                crs=CRS_EPSG_3857,
                bands=(BandInfo("x"), BandInfo("x")),
            )

    def test_empty_data_axis_raises(self) -> None:
        """A zero-size band axis is rejected early."""
        with pytest.raises(ValueError, match="non-empty"):
            point_input(np.ma.MaskedArray(np.zeros(0)))

    def test_frozen(self) -> None:
        """PointInput is immutable."""
        cov = point_input(np.ma.MaskedArray(np.zeros(2)))

        with pytest.raises(dataclasses.FrozenInstanceError):
            cov.data = np.ma.MaskedArray(np.zeros(2))  # type: ignore[misc]


class TestPointdataToCoverageInput:
    """Test conversion from rio-tiler PointData."""

    def test_basic_conversion(self) -> None:
        """Array (unchanged, no reshape), position, CRS, names, and dtype carry over."""
        point = make_point()
        cov = pointdata_to_point_input(point, position=POSITION)

        assert cov.data is point.array
        assert cov.data.shape == (2,)
        assert cov.position == POSITION
        assert cov.crs == CRS_EPSG_3857
        assert [band.name for band in cov.bands] == ["b1", "b2"]
        assert all(band.dtype == np.dtype("float32") for band in cov.bands)

    def test_mask_propagation(self) -> None:
        """Masked entries in the source array survive conversion."""
        arr: np.ma.MaskedArray[Any, np.dtype[Any]] = np.ma.MaskedArray(
            np.ones(2, dtype="float32"), mask=[True, False]
        )
        cov = pointdata_to_point_input(make_point(arr), position=POSITION)
        cov_mask = np.ma.getmaskarray(cov.data)

        assert cov_mask[0]
        assert not cov_mask[1]
        assert cov.data[1] == 1.0

    def test_bands_kwarg_applied(self) -> None:
        """An explicit bands sequence is stored as a tuple, entries unchanged."""
        bands = [BandInfo("a", description="alpha"), BandInfo("b", unit="m")]
        cov = pointdata_to_point_input(make_point(), position=POSITION, bands=bands)

        assert cov.bands == tuple(bands)

    def test_missing_crs_raises(self) -> None:
        """A point without a CRS is rejected."""
        with pytest.raises(ValueError, match="no CRS"):
            pointdata_to_point_input(make_point(crs=None), position=POSITION)

    def test_crs_kwarg_overrides(self) -> None:
        """An explicit crs= kwarg wins over (or substitutes for) point.crs."""
        wgs84 = rasterio.CRS.from_epsg(4326)
        substituted = pointdata_to_point_input(
            make_point(crs=None), position=POSITION, crs=wgs84
        )
        overridden = pointdata_to_point_input(
            make_point(), position=POSITION, crs=wgs84
        )

        assert substituted.crs == wgs84
        assert overridden.crs == wgs84

    def test_falsy_crs_override_is_honored(self) -> None:
        """A non-None but falsy crs= override wins, rather than point.crs.

        An empty ``rasterio.CRS()`` is falsy, so a truthiness-based fallback
        would silently discard it in favor of ``point.crs``; the explicit
        override must be honored instead.
        """
        empty = rasterio.CRS()
        cov = pointdata_to_point_input(make_point(), position=POSITION, crs=empty)

        assert cov.crs == empty
        assert cov.crs != CRS_EPSG_3857


class TestPolygon:
    """Test the Polygon geometry value type."""

    def test_minimal_construction(self) -> None:
        """A single closed exterior ring is sufficient (no holes)."""
        poly = Polygon(rings=(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)),))

        assert len(poly.rings) == 1
        assert poly.rings[0][0] == (0.0, 0.0)

    def test_with_hole(self) -> None:
        """An exterior ring plus one interior ring (hole) is carried."""
        exterior = ((0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0), (0.0, 0.0))
        hole = ((1.0, 1.0), (2.0, 1.0), (2.0, 2.0), (1.0, 2.0), (1.0, 1.0))
        poly = Polygon(rings=(exterior, hole))

        assert len(poly.rings) == 2

    def test_empty_rings_raises(self) -> None:
        """A polygon must have at least an exterior ring."""
        with pytest.raises(ValueError, match="at least one ring"):
            Polygon(rings=())

    def test_too_few_vertices_raises(self) -> None:
        """A ring needs at least four vertices (a closed triangle)."""
        with pytest.raises(ValueError, match="at least four vertices"):
            Polygon(rings=(((0.0, 0.0), (1.0, 0.0), (0.0, 0.0)),))

    def test_unclosed_ring_raises(self) -> None:
        """A ring whose first and last vertices differ is rejected."""
        with pytest.raises(ValueError, match="closed"):
            Polygon(rings=(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)),))

    @pytest.mark.parametrize(
        "bad", [float("nan"), float("inf"), float("-inf")], ids=("nan", "inf", "-inf")
    )
    def test_non_finite_vertex_raises(self, bad: float) -> None:
        """A NaN or infinite vertex coordinate is rejected at construction."""
        with pytest.raises(ValueError, match="must be finite"):
            Polygon(rings=(((0.0, 0.0), (bad, 0.0), (1.0, 1.0), (0.0, 0.0)),))

    def test_bounds(self) -> None:
        """bounds is the (minx, miny, maxx, maxy) box of the exterior ring."""
        # An asymmetric extent (x 1..7, y 2..3) so an x/y swap would be caught.
        poly = Polygon(
            rings=(((1.0, 2.0), (7.0, 2.0), (7.0, 3.0), (1.0, 3.0), (1.0, 2.0)),)
        )

        assert poly.bounds == (1.0, 2.0, 7.0, 3.0)

    def test_bounds_contained_hole_does_not_extend(self) -> None:
        """A hole inside the exterior leaves the bounding box unchanged."""
        exterior = ((0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0), (0.0, 0.0))
        hole = ((1.0, 1.0), (2.0, 1.0), (2.0, 2.0), (1.0, 2.0), (1.0, 1.0))

        assert Polygon(rings=(exterior, hole)).bounds == (0.0, 0.0, 4.0, 4.0)

    def test_bounds_spans_all_rings(self) -> None:
        """bounds spans every ring, so a hole reaching past the exterior widens it.

        A hole normally sits inside the exterior, but construction is permissive
        and does not enforce containment. The read a polygon drives (rio-tiler's
        feature) bounds all rings, so bounds must too: an interior ring extending
        beyond the exterior widens the box rather than hiding behind it.
        """
        exterior = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0))
        beyond = ((-5.0, -5.0), (5.0, -5.0), (5.0, 5.0), (-5.0, 5.0), (-5.0, -5.0))

        assert Polygon(rings=(exterior, beyond)).bounds == (-5.0, -5.0, 5.0, 5.0)

    def test_frozen(self) -> None:
        """Polygon is immutable."""
        with pytest.raises(dataclasses.FrozenInstanceError):
            SQUARE.rings = ()  # type: ignore[misc]


class TestPolygonInput:
    """Test the PolygonInput dataclass."""

    def test_minimal_construction(self) -> None:
        """A 1-D masked array with a geometry and CRS is sufficient."""
        cov = polygon_input(np.ma.MaskedArray(np.zeros(2)))

        # bands is resolved at construction, so it is never empty afterwards.
        assert [band.name for band in cov.bands] == ["b1", "b2"]
        assert cov.geometry == SQUARE
        assert cov.collection_id is None
        assert cov.item_ids is None

    @pytest.mark.parametrize(
        "shape",
        [(2, 1), (2, 3), (2, 1, 1), ()],
        ids=("2D-column", "2D", "3D", "0D"),
    )
    def test_non_1d_data_raises(self, shape: tuple[int, ...]) -> None:
        """PolygonInput requires 1-D (bands,) data: one reduced scalar per band."""
        with pytest.raises(ValueError, match="Polygon data must have shape"):
            polygon_input(np.ma.MaskedArray(np.zeros(shape)))

    def test_band_count_mismatch_raises(self) -> None:
        """A non-empty bands tuple must match data.shape[0]."""
        with pytest.raises(ValueError, match="does not match"):
            PolygonInput(
                data=np.ma.MaskedArray(np.zeros(2)),
                geometry=SQUARE,
                crs=CRS_EPSG_3857,
                bands=(BandInfo("b1"),),
            )

    def test_frozen(self) -> None:
        """PolygonInput is immutable."""
        cov = polygon_input(np.ma.MaskedArray(np.zeros(2)))

        with pytest.raises(dataclasses.FrozenInstanceError):
            cov.data = np.ma.MaskedArray(np.zeros(2))  # type: ignore[misc]


class TestImagedataToPolygonInput:
    """Test the reduce-and-convert path from a clipped ImageData."""

    def test_reduces_to_one_scalar_per_band(self) -> None:
        """Each band's valid pixels reduce to a single scalar (mean here)."""
        img = make_image(np.arange(32, dtype="float32").reshape(2, 4, 4))
        cov = imagedata_to_polygon_input(img, geometry=SQUARE, stat=Stat.MEAN)

        assert cov.data.shape == (2,)
        # band 0 is 0..15 (mean 7.5); band 1 is 16..31 (mean 23.5)
        assert cov.data.tolist() == [7.5, 23.5]
        assert cov.geometry == SQUARE
        assert cov.crs == CRS_EPSG_3857

    def test_band_dtype_follows_reduced_not_source(self) -> None:
        """The range dtype comes from the reduced array, not the source raster.

        A ``mean`` over an int16 raster is float, so the band dtype (which drives
        the CoverageJSON range value type) must be float, not the source int16.
        """
        img = make_image(np.arange(8, dtype="int16").reshape(2, 2, 2))
        cov = imagedata_to_polygon_input(img, geometry=SQUARE, stat=Stat.MEAN)

        assert all(np.dtype(band.dtype).kind == "f" for band in cov.bands)
        assert cov.data.dtype == np.dtype(cov.bands[0].dtype)

    def test_count_yields_integer_band_dtype(self) -> None:
        """A ``count`` reduction yields an integer range even over a float raster."""
        img = make_image(np.ones((2, 4, 4), dtype="float32"))
        cov = imagedata_to_polygon_input(img, geometry=SQUARE, stat=Stat.COUNT)

        assert all(np.dtype(band.dtype).kind == "i" for band in cov.bands)

    def test_all_masked_band_reduces_to_masked_scalar(self) -> None:
        """A band with no valid pixels becomes a masked scalar (serializes null)."""
        arr = np.ma.MaskedArray(
            np.ones((2, 4, 4), dtype="float32"),
            mask=np.repeat([[False], [True]], 16).reshape(2, 4, 4),
        )
        cov = imagedata_to_polygon_input(
            make_image(arr), geometry=SQUARE, stat=Stat.MEAN
        )

        assert not np.ma.getmaskarray(cov.data)[0]
        assert np.ma.getmaskarray(cov.data)[1]

    def test_bands_kwarg_supplies_names_and_units(self) -> None:
        """Explicit bands supply names/units, but dtype is taken from the reduction."""
        img = make_image(np.arange(8, dtype="int16").reshape(2, 2, 2))
        bands = [BandInfo("red", unit="K"), BandInfo("nir", unit="K")]
        cov = imagedata_to_polygon_input(
            img, geometry=SQUARE, stat=Stat.MEAN, bands=bands
        )

        assert [band.name for band in cov.bands] == ["red", "nir"]
        assert [band.unit for band in cov.bands] == ["K", "K"]
        # names/units kept, but the int16 declared dtype is replaced by the mean's float
        assert all(np.dtype(band.dtype).kind == "f" for band in cov.bands)

    def test_bands_describe_the_reduction(self) -> None:
        """Each band's description names the reduction; count drops the unit.

        The stat shapes the output metadata, not just the value: a
        unit-preserving reduction keeps the source unit and prefixes the
        description ("mean of precipitation"); count is a dimensionless valid
        pixel count, so it drops the unit.
        """
        img = make_image(np.arange(4, dtype="float32").reshape(1, 2, 2))
        band = BandInfo("b1", description="precipitation", unit="mm")

        mean = imagedata_to_polygon_input(
            img, geometry=SQUARE, stat=Stat.MEAN, bands=[band]
        )
        assert mean.bands[0].description == "mean of precipitation"
        assert mean.bands[0].unit == "mm"

        count = imagedata_to_polygon_input(
            img, geometry=SQUARE, stat=Stat.COUNT, bands=[band]
        )
        assert count.bands[0].description == "valid pixel count of precipitation"
        assert count.bands[0].unit == ""

    def test_reduction_description_falls_back_to_band_name(self) -> None:
        """With no band description, the reduction is named over the band name."""
        img = make_image(np.zeros((1, 2, 2), dtype="float32"))

        cov = imagedata_to_polygon_input(
            img, geometry=SQUARE, stat=Stat.MEAN, bands=[BandInfo("b1")]
        )

        assert cov.bands[0].description == "mean of b1"

    def test_missing_crs_raises(self) -> None:
        """An image without a CRS is rejected."""
        with pytest.raises(ValueError, match="no CRS"):
            imagedata_to_polygon_input(
                make_image(crs=None), geometry=SQUARE, stat=Stat.MEAN
            )
