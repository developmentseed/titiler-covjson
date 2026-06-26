"""Tests for the input layer: the CoverageInput variants and converters."""

from __future__ import annotations

import dataclasses
from typing import Any

import numpy as np
import pytest
import rasterio
from rio_tiler.models import ImageData, Info

from titiler_covjson.input import (
    BandInfo,
    GridInput,
    band_info_from_reader_info,
    imagedata_to_coverage_input,
)

BOUNDS = (-10.0, -5.0, 10.0, 5.0)
CRS = rasterio.CRS.from_epsg(3857)


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

    kwargs.setdefault("crs", CRS)
    kwargs.setdefault("bounds", BOUNDS)

    return ImageData(array, **kwargs)


def masked_input(data: np.ma.MaskedArray[Any, np.dtype[Any]]) -> GridInput:
    """Build a minimal GridInput around the given array.

    Args:
        data: The masked data array, shaped ``(bands, height, width)``.

    Returns:
        GridInput: An input with default bounds and CRS.
    """
    return GridInput(data=data, bounds=BOUNDS, crs=CRS)


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
                crs=CRS,
                bands=(BandInfo("b1"),),
            )

    def test_band_count_match_passes(self) -> None:
        """A bands tuple with one entry per band is accepted."""
        cov = GridInput(
            data=np.ma.MaskedArray(np.zeros((2, 2, 2))),
            bounds=BOUNDS,
            crs=CRS,
            bands=(BandInfo("b1"), BandInfo("b2")),
        )

        assert [band.name for band in cov.bands] == ["b1", "b2"]

    def test_duplicate_band_names_raises(self) -> None:
        """Band names become CovJSON keys, so they must be unique."""
        with pytest.raises(ValueError, match="unique"):
            GridInput(
                data=np.ma.MaskedArray(np.zeros((2, 2, 2))),
                bounds=BOUNDS,
                crs=CRS,
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
            crs=CRS,
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
        cov = imagedata_to_coverage_input(img)

        assert cov.data is img.array
        assert cov.bounds == BOUNDS
        assert cov.crs == CRS
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
        cov = imagedata_to_coverage_input(make_image(arr))

        cov_mask = np.ma.getmaskarray(cov.data)
        assert cov_mask[0, 0, 0]
        assert not cov_mask[1, 0, 0]
        assert cov.data[1, 0, 0] == 1.0

    def test_plain_array_gets_materialized_mask(self) -> None:
        """A plain ndarray input still yields a fully materialized mask."""
        img = make_image(np.ones((2, 4, 4), dtype="float32"))
        cov = imagedata_to_coverage_input(img)

        assert np.ma.getmaskarray(cov.data).shape == (2, 4, 4)
        assert not cov.data.mask.any()

    def test_nodata_derived_mask(self) -> None:
        """A nodata-sentinel mask (as built by Reader(..., nodata=...)) survives."""
        data = np.full((1, 4, 4), -9999.0, dtype="float32")
        data[0, 1, 1] = 42.0
        arr = np.ma.masked_equal(data, -9999.0)
        cov = imagedata_to_coverage_input(make_image(arr))

        assert cov.data.mask.sum() == 15
        assert cov.data[0, 1, 1] == 42.0

    def test_2d_array_coerced_to_3d(self) -> None:
        """Single-band 2-D input becomes (1, h, w) with one band."""
        cov = imagedata_to_coverage_input(make_image(np.zeros((4, 4), dtype="float32")))

        assert cov.data.shape == (1, 4, 4)
        assert [band.name for band in cov.bands] == ["b1"]

    def test_band_attribute_overrides(self) -> None:
        """band_names/band_descriptions/band_units kwargs override defaults."""
        cov = imagedata_to_coverage_input(
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
            imagedata_to_coverage_input(make_image(), **kwargs)

    def test_mismatched_img_band_names_raises(self) -> None:
        """Defaulted names from a malformed ImageData get a clear error.

        rio-tiler does not validate band_names length at construction, so the
        defaulted names must be length-checked like caller-supplied ones.
        """
        img = make_image(band_names=["only-one"])

        with pytest.raises(ValueError, match="`band_names` has 1 entries"):
            imagedata_to_coverage_input(img)

    def test_bands_kwarg_applied(self) -> None:
        """An explicit bands sequence is stored as a tuple, entries unchanged."""
        bands = [BandInfo("a", description="alpha"), BandInfo("b", unit="m")]
        cov = imagedata_to_coverage_input(make_image(), bands=bands)

        assert cov.bands == tuple(bands)

    @pytest.mark.parametrize("band_names", [["x", "y"], []], ids=("non-empty", "empty"))
    def test_bands_kwarg_conflicts_with_overrides(self, band_names: list[str]) -> None:
        """bands= is mutually exclusive with overrides, even empty ones."""
        with pytest.raises(ValueError, match="Cannot combine `bands`"):
            imagedata_to_coverage_input(
                make_image(),
                bands=[BandInfo("a"), BandInfo("b")],
                band_names=band_names,
            )

    def test_bands_kwarg_wrong_length_raises(self) -> None:
        """A bands list of the wrong length fails GridInput validation."""
        with pytest.raises(ValueError, match="does not match"):
            imagedata_to_coverage_input(make_image(), bands=[BandInfo("a")])

    def test_missing_crs_raises(self) -> None:
        """An image without a CRS is rejected."""
        with pytest.raises(ValueError, match="no CRS"):
            imagedata_to_coverage_input(make_image(crs=None))

    def test_crs_kwarg_overrides(self) -> None:
        """An explicit crs= kwarg wins over (or substitutes for) img.crs."""
        wgs84 = rasterio.CRS.from_epsg(4326)

        assert imagedata_to_coverage_input(make_image(crs=None), crs=wgs84).crs == wgs84
        assert imagedata_to_coverage_input(make_image(), crs=wgs84).crs == wgs84

    def test_missing_bounds_raises(self) -> None:
        """An image without bounds is rejected."""
        with pytest.raises(ValueError, match="no bounds"):
            imagedata_to_coverage_input(make_image(bounds=None))

    def test_passthrough_provenance_fields(self) -> None:
        """collection_id and item_ids carry over to the GridInput."""
        cov = imagedata_to_coverage_input(
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
        cov = imagedata_to_coverage_input(
            make_image(), bands=band_info_from_reader_info(self.make_info())
        )

        assert [band.name for band in cov.bands] == ["b1", "b2"]
        assert [band.unit for band in cov.bands] == ["mm", ""]
