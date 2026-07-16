"""Tests for the per-band statistical reduction (reduce.py)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from titiler_covjson.reduce import Stat, reduce_each_band


def _band(
    values: list[float], *, dtype: str = "float32"
) -> np.ma.MaskedArray[Any, np.dtype[Any]]:
    """Build a single-band ``(1, 2, 2)`` unmasked array from four values.

    Args:
        values: The four pixel values, laid out row-major into a 2x2 band.
        dtype: The array dtype.

    Returns:
        np.ma.MaskedArray: A ``(1, 2, 2)`` array with no masked entries.
    """
    array = np.array(values, dtype=dtype).reshape(1, 2, 2)

    return np.ma.MaskedArray(array, mask=False)


class TestReduceBands:
    """Test reduce_each_band: one scalar per band over the non-band axes."""

    @pytest.mark.parametrize(
        ("stat", "expected"),
        [
            (Stat.MIN, 1.0),
            (Stat.MAX, 4.0),
            (Stat.MEAN, 2.5),
            (Stat.MEDIAN, 2.5),
            (Stat.STD, math.sqrt(1.25)),
            (Stat.SUM, 10.0),
            (Stat.COUNT, 4.0),
        ],
        ids=("min", "max", "mean", "median", "std", "sum", "count"),
    )
    def test_computes_each_stat(self, stat: Stat, expected: float) -> None:
        """Each statistic reduces a band's valid pixels to the right scalar."""
        reduced = reduce_each_band(_band([1.0, 2.0, 3.0, 4.0]), stat)

        assert reduced.shape == (1,)
        assert reduced[0] == pytest.approx(expected)

    def test_returns_1d_masked_array(self) -> None:
        """The result is a 1-D masked array, one entry per band."""
        data = np.ma.MaskedArray(np.arange(8, dtype="float32").reshape(2, 2, 2))
        reduced = reduce_each_band(data, Stat.MEAN)

        assert isinstance(reduced, np.ma.MaskedArray)
        assert reduced.shape == (2,)

    def test_reduces_each_band_independently(self) -> None:
        """Bands do not bleed into each other: each reduces on its own pixels."""
        data = np.ma.MaskedArray(
            np.array(
                [[[0.0, 0.0], [0.0, 0.0]], [[10.0, 20.0], [30.0, 40.0]]], "float32"
            )
        )
        reduced = reduce_each_band(data, Stat.MAX)

        assert reduced.tolist() == [0.0, 40.0]

    @pytest.mark.parametrize(
        "stat",
        [Stat.MIN, Stat.MAX, Stat.MEAN, Stat.MEDIAN, Stat.STD, Stat.SUM],
        ids=("min", "max", "mean", "median", "std", "sum"),
    )
    def test_all_masked_band_reduces_to_masked(self, stat: Stat) -> None:
        """A band with no valid pixels reduces to masked (serializes as null)."""
        data = np.ma.MaskedArray(
            np.ones((2, 2, 2), dtype="float32"),
            mask=[
                [[False, False], [False, False]],
                [[True, True], [True, True]],
            ],
        )
        reduced = reduce_each_band(data, stat)

        assert not np.ma.getmaskarray(reduced)[0]
        assert np.ma.getmaskarray(reduced)[1]

    def test_count_of_all_masked_band_is_zero(self) -> None:
        """count reports 0 valid pixels (never masked), unlike the other stats."""
        data = np.ma.MaskedArray(
            np.ones((2, 2, 2), dtype="float32"),
            mask=[
                [[False, False], [False, False]],
                [[True, True], [True, True]],
            ],
        )
        reduced = reduce_each_band(data, Stat.COUNT)

        assert not np.ma.getmaskarray(reduced).any()
        assert reduced.tolist() == [4, 0]

    @pytest.mark.parametrize(
        ("stat", "expected_kind"),
        [
            (Stat.MIN, "i"),
            (Stat.MAX, "i"),
            (Stat.SUM, "i"),
            (Stat.COUNT, "i"),
            (Stat.MEAN, "f"),
            (Stat.MEDIAN, "f"),
            (Stat.STD, "f"),
        ],
        ids=("min", "max", "sum", "count", "mean", "median", "std"),
    )
    def test_result_dtype_follows_stat_not_source(
        self, stat: Stat, expected_kind: str
    ) -> None:
        """The stat, not the source raster, sets the result dtype.

        Over an integer raster, ``min``/``max``/``sum``/``count`` stay integer
        while ``mean``/``median``/``std`` promote to float, so the CoverageJSON
        range value type must be chosen from the reduced array, not the source.
        """
        reduced = reduce_each_band(_band([1.0, 2.0, 3.0, 4.0], dtype="int16"), stat)

        assert reduced.dtype.kind == expected_kind


class TestStat:
    """Test the Stat enum's self-describing metadata."""

    @pytest.mark.parametrize(
        ("stat", "label"),
        [
            (Stat.MIN, "min"),
            (Stat.MAX, "max"),
            (Stat.MEAN, "mean"),
            (Stat.MEDIAN, "median"),
            (Stat.STD, "population standard deviation"),
            (Stat.SUM, "sum"),
            (Stat.COUNT, "valid pixel count"),
        ],
        ids=("min", "max", "mean", "median", "std", "sum", "count"),
    )
    def test_label(self, stat: Stat, label: str) -> None:
        """Each stat has a human-readable label for a coverage parameter.

        The wire value doubles as the label, except where it is ambiguous on its
        own: count ("valid pixel count") and std ("population standard
        deviation", the convention it follows being unrecoverable from "std").
        """
        assert stat.label == label

    @pytest.mark.parametrize(
        ("stat", "preserves"),
        [
            (Stat.MIN, True),
            (Stat.MAX, True),
            (Stat.MEAN, True),
            (Stat.MEDIAN, True),
            (Stat.STD, True),
            (Stat.SUM, True),
            (Stat.COUNT, False),
        ],
        ids=("min", "max", "mean", "median", "std", "sum", "count"),
    )
    def test_preserves_unit(self, stat: Stat, preserves: bool) -> None:
        """Only count changes the unit (to dimensionless); the rest preserve it."""
        assert stat.preserves_unit is preserves
