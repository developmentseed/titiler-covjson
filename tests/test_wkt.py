"""Tests for the WKT parsers."""

from __future__ import annotations

import pytest

from titiler_covjson.geometry import MultiPoint, Polygon, Position
from titiler_covjson.wkt import (
    InvalidCoords,
    parse_multipoint_wkt,
    parse_point_wkt,
    parse_polygon_wkt,
    parse_position_coords,
)


@pytest.mark.parametrize(
    ("wkt", "expected"),
    [
        ("POINT(0 0)", Position(0.0, 0.0)),
        ("POINT(-5.0 2.5)", Position(-5.0, 2.5)),
        ("point(1 2)", Position(1.0, 2.0)),
        ("  POINT ( 1   2 ) ", Position(1.0, 2.0)),
        ("POINT(1e2 -3.5)", Position(100.0, -3.5)),
        ("POINT(+1 -2)", Position(1.0, -2.0)),
    ],
    ids=["canonical", "decimals", "lowercase", "whitespace", "exponent", "signs"],
)
def test_parse_point_wkt_accepts_2d_points(wkt: str, expected: Position) -> None:
    assert parse_point_wkt(wkt) == expected


@pytest.mark.parametrize(
    "wkt",
    [
        "POINT Z (0 0 5)",
        "POINT M (0 0 5)",
        "POINT ZM (0 0 5 1)",
        "POINTZ(0 0 5)",
        "POINT(0 0 5)",
        "POINT(0 0 5 1)",
    ],
    ids=["Z-tag", "M-tag", "ZM-tag", "Z-suffix", "3-token", "4-token"],
)
def test_parse_point_wkt_rejects_vertical_or_measured(wkt: str) -> None:
    # A vertical/measured geometry is rejected: the 2-D raster cannot sample it.
    parsed = parse_point_wkt(wkt)

    assert isinstance(parsed, InvalidCoords)
    assert "not supported" in parsed.message


@pytest.mark.parametrize(
    "wkt",
    [
        "POINT EMPTY",
        "MULTIPOINT(0 0)",
        "LINESTRING(0 0, 1 1)",
        "not-wkt",
        "",
        "POINT()",
        "POINT(0)",
        "POINT(1, 2)",
        "POINT(nan 0)",
        "POINT(1 inf)",
        "POINT(1e400 0)",
    ],
    ids=[
        "empty-geom",
        "multipoint",
        "linestring",
        "garbage",
        "blank",
        "no-coords",
        "one-coord",
        "comma",
        "nan",
        "inf",
        "overflow",
    ],
)
def test_parse_point_wkt_rejects_malformed_or_non_finite(wkt: str) -> None:
    parsed = parse_point_wkt(wkt)

    assert isinstance(parsed, InvalidCoords)
    assert "Invalid position" in parsed.message


@pytest.mark.parametrize(
    ("wkt", "expected"),
    [
        (
            "POLYGON((0 0, 1 0, 1 1, 0 0))",
            (((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)),),
        ),
        (
            "polygon((0 0,1 0,1 1,0 0))",
            (((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)),),
        ),
        (
            "  POLYGON (( 0 0, 1 0, 1 1, 0 0 )) ",
            (((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)),),
        ),
        (
            "POLYGON((-1e1 -2.5, 1 0, 1 1, -1e1 -2.5))",
            (((-10.0, -2.5), (1.0, 0.0), (1.0, 1.0), (-10.0, -2.5)),),
        ),
    ],
    ids=["canonical", "lowercase-no-space", "whitespace", "decimals-exp-signs"],
)
def test_parse_polygon_wkt_accepts_single_ring(
    wkt: str, expected: tuple[tuple[tuple[float, float], ...], ...]
) -> None:
    assert parse_polygon_wkt(wkt) == Polygon(rings=expected)


def test_parse_polygon_wkt_accepts_holes() -> None:
    # An exterior ring plus one interior ring (hole) yields two rings.
    wkt = "POLYGON((0 0, 4 0, 4 4, 0 4, 0 0), (1 1, 2 1, 2 2, 1 2, 1 1))"
    polygon = parse_polygon_wkt(wkt)

    assert isinstance(polygon, Polygon)
    assert len(polygon.rings) == 2
    assert polygon.rings[1] == (
        (1.0, 1.0),
        (2.0, 1.0),
        (2.0, 2.0),
        (1.0, 2.0),
        (1.0, 1.0),
    )


@pytest.mark.parametrize(
    "wkt",
    [
        "POLYGON Z ((0 0 1, 1 0 1, 1 1 1, 0 0 1))",
        "POLYGON M ((0 0 1, 1 0 1, 1 1 1, 0 0 1))",
        "POLYGON ZM ((0 0 1 1, 1 0 1 1, 1 1 1 1, 0 0 1 1))",
        "POLYGON((0 0 5, 1 0 5, 1 1 5, 0 0 5))",
    ],
    ids=["Z-tag", "M-tag", "ZM-tag", "3-token-vertex"],
)
def test_parse_polygon_wkt_rejects_vertical_or_measured(wkt: str) -> None:
    # A 3-D/measured polygon is rejected: the 2-D raster cannot sample a level.
    parsed = parse_polygon_wkt(wkt)

    assert isinstance(parsed, InvalidCoords)
    assert "not supported" in parsed.message


@pytest.mark.parametrize(
    "wkt",
    [
        "not-wkt",
        "",
        "POLYGON EMPTY",
        "POLYGON(())",
        "MULTIPOLYGON(((0 0, 1 0, 1 1, 0 0)))",
        "LINESTRING(0 0, 1 1)",
        "POLYGON((0 0, 1 0, 1 1, 0 1))",
        "POLYGON((0 0, 1 0, 0 0))",
        "POLYGON((nan 0, 1 0, 1 1, nan 0))",
        "POLYGON((1 inf, 1 0, 1 1, 1 inf))",
        "POLYGON((0 0, x 0, 1 1, 0 0))",
    ],
    ids=[
        "garbage",
        "blank",
        "empty-geom",
        "empty-ring",
        "multipolygon",
        "linestring",
        "unclosed-ring",
        "too-few-vertices",
        "nan",
        "inf",
        "non-numeric",
    ],
)
def test_parse_polygon_wkt_rejects_malformed_or_invalid(wkt: str) -> None:
    parsed = parse_polygon_wkt(wkt)

    assert isinstance(parsed, InvalidCoords)
    assert "Invalid polygon" in parsed.message


@pytest.mark.parametrize(
    ("wkt", "expected"),
    [
        ("MULTIPOINT((0 0), (1 1))", ((0.0, 0.0), (1.0, 1.0))),
        ("MULTIPOINT(0 0, 1 1)", ((0.0, 0.0), (1.0, 1.0))),
        ("MULTIPOINT((0 0))", ((0.0, 0.0),)),
        ("MULTIPOINT(-5.0 2.5, 1e1 -3)", ((-5.0, 2.5), (10.0, -3.0))),
        ("  multipoint ( ( 0 0 ) , ( 1 1 ) ) ", ((0.0, 0.0), (1.0, 1.0))),
    ],
    ids=("parenthesized", "flat", "single", "decimals-signs", "whitespace-case"),
)
def test_parse_multipoint_wkt_accepts_both_forms(
    wkt: str, expected: tuple[tuple[float, float], ...]
) -> None:
    assert parse_multipoint_wkt(wkt) == MultiPoint(positions=expected)


def test_parse_multipoint_wkt_accepts_mixed_parenthesization() -> None:
    """A mixed ``((x y), x y)`` keeps every point, dropping none.

    This locks the strip-not-findall decision: stripping the per-point parens
    reduces both spellings to one grammar, whereas collecting parenthesized
    groups would silently discard the bare ``3 4`` here.
    """
    parsed = parse_multipoint_wkt("MULTIPOINT((1 2), 3 4)")

    assert parsed == MultiPoint(positions=((1.0, 2.0), (3.0, 4.0)))


@pytest.mark.parametrize(
    "wkt",
    [
        "MULTIPOINT Z ((0 0 5), (1 1 5))",
        "MULTIPOINT M ((0 0 5))",
        "MULTIPOINT ZM ((0 0 5 1))",
        "MULTIPOINT(0 0 5, 1 1 5)",
    ],
    ids=("Z-tag", "M-tag", "ZM-tag", "3-token-vertex"),
)
def test_parse_multipoint_wkt_rejects_vertical_or_measured(wkt: str) -> None:
    parsed = parse_multipoint_wkt(wkt)

    assert isinstance(parsed, InvalidCoords)


@pytest.mark.parametrize(
    "wkt",
    [
        "MULTIPOINT EMPTY",
        "POINT(0 0)",
        "not-wkt",
        "",
        "MULTIPOINT()",
        "MULTIPOINT((0 0), (x 1))",
    ],
    ids=(
        "empty-geom",
        "point",
        "garbage",
        "blank",
        "no-points",
        "non-numeric",
    ),
)
def test_parse_multipoint_wkt_rejects_malformed_or_invalid(wkt: str) -> None:
    parsed = parse_multipoint_wkt(wkt)

    assert isinstance(parsed, InvalidCoords)


def test_parse_multipoint_wkt_reports_duplicate_and_non_finite() -> None:
    """A duplicate or non-finite position surfaces MultiPoint's own message."""
    dup = parse_multipoint_wkt("MULTIPOINT((0 0), (0 0))")
    nan = parse_multipoint_wkt("MULTIPOINT((nan 0))")

    assert isinstance(dup, InvalidCoords)
    assert "unique" in dup.message
    assert isinstance(nan, InvalidCoords)
    assert "finite" in nan.message


def test_parse_position_coords_dispatches_point_and_multipoint() -> None:
    """The one /position entry point returns a Position or a MultiPoint by form."""
    assert parse_position_coords("POINT(0 0)") == Position(0.0, 0.0)
    assert parse_position_coords("MULTIPOINT((0 0), (1 1))") == MultiPoint(
        positions=((0.0, 0.0), (1.0, 1.0))
    )


def test_parse_position_coords_rejects_other_geometries_with_one_message() -> None:
    """A geometry that is neither POINT nor MULTIPOINT names both accepted forms."""
    parsed = parse_position_coords("POLYGON((0 0, 1 0, 1 1, 0 0))")

    assert isinstance(parsed, InvalidCoords)
    assert "POINT" in parsed.message
    assert "MULTIPOINT" in parsed.message


def test_parse_position_coords_delegates_malformed_messages() -> None:
    """A malformed POINT or MULTIPOINT surfaces that parser's specific message."""
    bad_point = parse_position_coords("POINT Z (0 0 5)")
    bad_multi = parse_position_coords("MULTIPOINT((0 0 5))")

    assert isinstance(bad_point, InvalidCoords)
    assert "not supported" in bad_point.message
    assert isinstance(bad_multi, InvalidCoords)
