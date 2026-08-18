"""Tests for the geometry value types."""

from __future__ import annotations

import dataclasses

import pytest

from titiler_covjson.geometry import MultiPoint, Polygon, Position

POSITION = Position(1.0, 2.0)
# A square exterior ring (closed), no holes.
SQUARE = Polygon(rings=(((0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0), (0.0, 0.0)),))


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
        ("x", "y", "z", "expected"),
        [
            (float("nan"), 2.0, None, "got x=nan, y=2.0."),
            (float("inf"), 2.0, None, "got x=inf, y=2.0."),
            (1.0, float("-inf"), None, "got x=1.0, y=-inf."),
            (1.0, 2.0, float("nan"), "got x=1.0, y=2.0, z=nan."),
            (1.0, 2.0, float("inf"), "got x=1.0, y=2.0, z=inf."),
        ],
        ids=("x-nan", "x-inf", "y-neg-inf", "z-nan", "z-inf"),
    )
    def test_non_finite_coordinate_raises(
        self, x: float, y: float, z: float | None, expected: str
    ) -> None:
        """A NaN or infinite x, y, or z is rejected, naming the axis at fault."""
        with pytest.raises(ValueError, match="must be finite") as excinfo:
            Position(x, y, z=z)

        assert str(excinfo.value).endswith(expected)

    def test_frozen(self) -> None:
        """Position is immutable."""
        pos = Position(1.5, -2.5)

        with pytest.raises(dataclasses.FrozenInstanceError):
            pos.x = 0.0  # type: ignore[misc]


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

    def test_ring_faults_name_the_offending_ring(self) -> None:
        """A hole at fault is reported by index, not left for the reader to find.

        A polygon with holes has no other way to say which ring failed, and the
        exterior is a poor guess: it is the ring most likely to be correct.
        """
        exterior = ((0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 0.0))
        short_hole = ((1.0, 1.0), (2.0, 1.0), (1.0, 1.0))
        unclosed_hole = ((1.0, 1.0), (2.0, 1.0), (2.0, 2.0), (1.0, 2.0))

        with pytest.raises(ValueError, match=r"in ring 1 got 3\."):
            Polygon(rings=(exterior, short_hole))

        with pytest.raises(ValueError, match=r"in ring 1 got: \(1\.0, 1\.0\)"):
            Polygon(rings=(exterior, unclosed_hole))

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


class TestMultiPoint:
    """Test the MultiPoint geometry value type."""

    def test_single_position(self) -> None:
        """One position is a valid multipoint."""
        assert MultiPoint(positions=((1.0, 2.0),)).positions == ((1.0, 2.0),)

    def test_positions_kept_in_order(self) -> None:
        """Positions are preserved in the order given, not sorted or deduped."""
        positions = ((3.0, 3.0), (1.0, 1.0), (2.0, 2.0))

        assert MultiPoint(positions=positions).positions == positions

    def test_empty_positions_raises(self) -> None:
        """A multipoint with no positions is rejected."""
        with pytest.raises(ValueError, match="at least one position"):
            MultiPoint(positions=())

    @pytest.mark.parametrize(
        "bad",
        [float("nan"), float("inf"), float("-inf")],
        ids=("nan", "inf", "neg-inf"),
    )
    def test_non_finite_coordinate_raises(self, bad: float) -> None:
        """A NaN or infinite coordinate is rejected at construction."""
        with pytest.raises(ValueError, match="must be finite"):
            MultiPoint(positions=((0.0, 0.0), (bad, 1.0)))

    def test_duplicate_positions_raises(self) -> None:
        """A repeated position is rejected: a coverage axis cannot index it twice."""
        with pytest.raises(ValueError, match="unique"):
            MultiPoint(positions=((0.0, 0.0), (1.0, 1.0), (0.0, 0.0)))

    def test_duplicate_positions_name_both_indexes(self) -> None:
        """The repeat names the pair, which a long point list otherwise hides."""
        with pytest.raises(
            ValueError, match=r"position 3 \(1\.0, 1\.0\) repeats position 1"
        ):
            MultiPoint(positions=((0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (1.0, 1.0)))

    def test_negative_zero_and_zero_are_the_same_position(self) -> None:
        """-0.0 and 0.0 collide as duplicates, matching the schema's uniqueness test.

        Both values are echoed, not just the repeat's: these two positions are
        equal but do not look it, so a message naming one index and the other
        index's value would read as a mistake.
        """
        with pytest.raises(
            ValueError,
            match=r"position 1 \(-0\.0, 0\.0\) repeats position 0 \(0\.0, 0\.0\)",
        ):
            MultiPoint(positions=((0.0, 0.0), (-0.0, 0.0)))

    def test_frozen(self) -> None:
        """MultiPoint is immutable."""
        mp = MultiPoint(positions=((1.0, 2.0),))

        with pytest.raises(dataclasses.FrozenInstanceError):
            mp.positions = ()  # type: ignore[misc]
