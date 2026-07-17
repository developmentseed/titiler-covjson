"""Tests for the WKT parsers."""

from __future__ import annotations

import pytest

from titiler_covjson.geometry import Polygon, Position
from titiler_covjson.wkt import InvalidCoords, parse_point_wkt, parse_polygon_wkt


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
