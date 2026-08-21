"""Tests for the WKT parsers."""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest

from titiler_covjson.geometry import MultiPoint, Polygon, Position
from titiler_covjson.wkt import (
    _COORDINATE_TOKEN,
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
    "token",
    [
        "0",
        "-5.0",
        "+1",
        "1e2",
        "1E2",
        "1e-3",
        "1.5e+10",
        ".5",
        "5.",
        "1e400",
        "nan",
        "NaN",
        "inf",
        "-inf",
        "+Infinity",
    ],
)
def test_coordinate_token_never_lets_float_raise(token: str) -> None:
    """Every token the pattern admits converts, so the parsers need no try/except.

    The parsers check with the pattern instead of catching ``float``'s error, so
    a token it admits but ``float`` rejects would surface as a 500 rather than a
    400. This pins the pattern as the narrower of the two.
    """
    assert _COORDINATE_TOKEN.fullmatch(token) is not None
    float(token)


@pytest.mark.parametrize(
    "token",
    ["1_000", "١٢", "１２", "0x10", "1e", "--1", "1d5", "infin", "nanny", ""],
    ids=[
        "underscore",
        "arabic-indic",
        "fullwidth",
        "hex",
        "bare-exponent",
        "double-sign",
        "d-exponent",
        "partial-inf",
        "partial-nan",
        "empty",
    ],
)
def test_coordinate_token_rejects_non_wkt_syntax(token: str) -> None:
    """WKT's number grammar is narrower than what ``float`` reads."""
    assert _COORDINATE_TOKEN.fullmatch(token) is None


def test_parse_point_wkt_accepts_an_underflowing_coordinate() -> None:
    """A coordinate too small to represent is accepted, silently becoming zero.

    Recorded as a decision rather than left to be rediscovered: ``float``
    underflows ``1e-400`` to ``0.0`` before any check runs, and the difference
    between the two is orders of magnitude finer than a coordinate in any CRS
    this serves can express, so no location is lost. Overflow is not the mirror
    of this: it reaches infinity, which names no location at all, and is
    rejected.
    """
    assert parse_point_wkt("POINT(1e-400 0)") == Position(0.0, 0.0)


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
    ("wkt", "expected"),
    [
        ("POINT EMPTY", "expected WKT POINT(x y)"),
        ("MULTIPOINT(0 0)", "expected WKT POINT(x y)"),
        ("LINESTRING(0 0, 1 1)", "expected WKT POINT(x y)"),
        ("not-wkt", "expected WKT POINT(x y)"),
        ("", "expected WKT POINT(x y)"),
        ("POINT()", "expected two coordinates"),
        ("POINT(0)", "expected two coordinates"),
        ("POINT(1, 2)", "coordinates must be numbers"),
        ("POINT(1 , 2)", "coordinates must be numbers"),
        ("POINT(x 0)", "coordinates must be numbers"),
        ("POINT(a b c)", "coordinates must be numbers"),
        ("POINT(1_000 2)", "coordinates must be numbers"),
        ("POINT(١٢ 2)", "coordinates must be numbers"),
        ("POINT(１２ 2)", "coordinates must be numbers"),
        ("POINT(0x10 2)", "coordinates must be numbers"),
        ("POINT(nan 0)", "got x=nan, y=0.0."),
        ("POINT(1 inf)", "got x=1.0, y=inf."),
        ("POINT(1e400 0)", "got x=inf, y=0.0."),
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
        "spaced-comma",
        "non-numeric",
        "non-numeric-3-token",
        "underscore-separator",
        "arabic-indic-digits",
        "fullwidth-digits",
        "hex-literal",
        "nan",
        "inf",
        "overflow",
    ],
)
def test_parse_point_wkt_rejects_malformed_or_invalid(wkt: str, expected: str) -> None:
    # The fragment pins which fault each message names, and the non-finite rows
    # carry the whole tail: `got x=` alone matches whichever axis prints first.
    parsed = parse_point_wkt(wkt)

    assert isinstance(parsed, InvalidCoords)
    assert "Invalid position" in parsed.message
    assert expected in parsed.message


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
    ("wkt", "expected"),
    [
        ("not-wkt", "expected WKT POLYGON"),
        ("", "expected WKT POLYGON"),
        ("POLYGON EMPTY", "expected WKT POLYGON"),
        ("POLYGON(())", "at least four vertices (a closed triangle); in ring 0 got 0."),
        ("POLYGON((0 0, 1 0, 1 1, 0 0) JUNK (2 2, 3 2, 3 3, 2 2))", "malformed ring"),
        ("POLYGON((0 0, 1 0, 1 1, 0 0) (2 2, 3 2, 3 3, 2 2))", "malformed ring"),
        ("POLYGON((0 0, 1 0, 1 1, 0 0) ,, (2 2, 3 2, 3 3, 2 2))", "malformed ring"),
        ("POLYGON((0 0, 4 0, 4 4, 0 4, 0 0), (1 1, x 1, 2 2, 1 1))", "in ring 1,"),
        ("POLYGON((0 0 1 0, 1 1, 0 0))", "check for a missing comma"),
        ("POLYGON(0 0, 1 1)", "expected at least one parenthesized ring"),
        ("MULTIPOLYGON(((0 0, 1 0, 1 1, 0 0)))", "expected WKT POLYGON"),
        ("LINESTRING(0 0, 1 1)", "expected WKT POLYGON"),
        ("POLYGON((0 0, 1 0, 1 1, 0 1))", "must be closed"),
        ("POLYGON((0 0, 1 0, 0 0))", "in ring 0 got 3."),
        ("POLYGON((nan 0, 1 0, 1 1, nan 0))", "in ring 0 vertex 0 got: (nan, 0.0)."),
        ("POLYGON((1 inf, 1 0, 1 1, 1 inf))", "in ring 0 vertex 0 got: (1.0, inf)."),
        (
            "POLYGON((0 0, 4 0, 4 4, 0 4, 0 0), (1 1, 2 1, nan 2, 1 2, 1 1))",
            "in ring 1 vertex 2 got: (nan, 2.0).",
        ),
        ("POLYGON((0 0, x 0, 1 1, 0 0))", "each vertex coordinate must be a number"),
        (
            "POLYGON((0 0, a b c, 1 1, 0 0))",
            "each vertex coordinate must be a number",
        ),
    ],
    ids=[
        "garbage",
        "blank",
        "empty-geom",
        "empty-ring",
        "junk-between-rings",
        "missing-comma-between-rings",
        "doubled-comma-between-rings",
        "non-numeric-in-hole",
        "missing-comma",
        "no-ring",
        "multipolygon",
        "linestring",
        "unclosed-ring",
        "too-few-vertices",
        "nan",
        "inf",
        "nan-in-hole",
        "non-numeric",
        "non-numeric-3-token",
    ],
)
def test_parse_polygon_wkt_rejects_malformed_or_invalid(
    wkt: str, expected: str
) -> None:
    parsed = parse_polygon_wkt(wkt)

    assert isinstance(parsed, InvalidCoords)
    assert "Invalid polygon" in parsed.message
    assert expected in parsed.message


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


@pytest.mark.parametrize(
    "wkt",
    [
        "MULTIPOINT((1 2), 3 4)",
        "MULTIPOINT(1 2, (3 4))",
        "MULTIPOINT((1 2), 3 4, (5 6))",
    ],
    ids=("parenthesized-first", "bare-first", "parenthesized-outer"),
)
def test_parse_multipoint_wkt_rejects_mixed_parenthesization(wkt: str) -> None:
    """One spelling per multipoint: the two may not be mixed within one list.

    Each spelling is accepted on its own, but a list mixing them is refused by
    every WKT grammar and by GEOS, so accepting it here would be a leniency no
    producer needs and no other reader shares.
    """
    parsed = parse_multipoint_wkt(wkt)

    assert isinstance(parsed, InvalidCoords)
    assert "must not mix" in parsed.message


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
    assert "not supported" in parsed.message


@pytest.mark.parametrize(
    ("wkt", "expected"),
    [
        ("MULTIPOINT EMPTY", "expected WKT MULTIPOINT"),
        ("POINT(0 0)", "expected WKT MULTIPOINT"),
        ("not-wkt", "expected WKT MULTIPOINT"),
        ("", "expected WKT MULTIPOINT"),
        ("MULTIPOINT()", "at least one position"),
        ("MULTIPOINT((0 0), (x 1))", "each vertex coordinate must be a number"),
        ("MULTIPOINT((0 0) (1 1))", "malformed point list"),
        ("MULTIPOINT((0 0, (1 1))", "malformed point list"),
        ("MULTIPOINT((0 0)), (1 1))", "malformed point list"),
        ("MULTIPOINT(((0 0)), ((1 1)))", "malformed point list"),
        ("MULTIPOINT((0 0), (1 1)", "malformed point list"),
        ("MULTIPOINT(0 0 1 1)", "check for a missing comma"),
        ("MULTIPOINT((0 0), (1 1),)", "malformed point list"),
        ("MULTIPOINT(,(0 0))", "malformed point list"),
        ("MULTIPOINT((0 0),,(1 1))", "malformed point list"),
    ],
    ids=(
        "empty-geom",
        "point",
        "garbage",
        "blank",
        "no-points",
        "non-numeric",
        "missing-comma-parenthesized",
        "unbalanced-open-paren",
        "unbalanced-close-paren",
        "doubled-parens",
        "unclosed-point-list",
        "missing-comma-flat",
        "trailing-comma",
        "leading-comma",
        "doubled-comma",
    ),
)
def test_parse_multipoint_wkt_rejects_malformed_or_invalid(
    wkt: str, expected: str
) -> None:
    parsed = parse_multipoint_wkt(wkt)

    assert isinstance(parsed, InvalidCoords)
    assert "Invalid multipoint" in parsed.message
    assert expected in parsed.message


@pytest.mark.parametrize(
    ("parse", "coords"),
    [
        (parse_point_wkt, "POINT(" + " " * 4000),
        (parse_point_wkt, "POINT(" + "9" * 16000 + "x 0)"),
        (parse_polygon_wkt, "POLYGON((" + " " * 4000),
        (parse_polygon_wkt, "POLYGON((0 0, " + "9" * 16000 + "x 0))"),
        (parse_multipoint_wkt, "MULTIPOINT(" + " " * 4000 + "()"),
        (parse_multipoint_wkt, "MULTIPOINT(" + "9" * 16000 + "x 0)"),
        (parse_polygon_wkt, "POLYGON(" + "(0 0) , " * 16000 + "x)"),
        (parse_multipoint_wkt, "MULTIPOINT(" + "(0 0), " * 16000 + "(x)"),
    ],
    ids=[
        "point-body-padding",
        "point-long-token",
        "polygon-ring-padding",
        "polygon-long-token",
        "multipoint-list-padding",
        "multipoint-long-token",
        "polygon-many-rings-then-junk",
        "multipoint-many-points-then-junk",
    ],
)
def test_parsers_reject_long_malformed_coords_promptly(
    parse: Callable[[str], object], coords: str
) -> None:
    """Rejecting a long malformed value stays linear, so it cannot pin a worker.

    ``coords`` arrives straight off a public query string with no length bound,
    so a pattern whose quantifiers can split one run of characters more than one
    way turns a rejection into a denial of service: the engine walks every split
    before reporting. Every payload here is refused either way, and only the time
    taken tells a sound pattern from a ruinous one. Four of them did take that
    long once, from about two seconds to about eleven. The rest aim at list
    patterns that are sound today, whose repetition a later edit could make
    ambiguous without any other case noticing.
    """
    start = time.perf_counter()
    parsed = parse(coords)
    elapsed = time.perf_counter() - start

    assert isinstance(parsed, InvalidCoords)
    assert elapsed < 1.0, f"rejection took {elapsed:.3f}s"


def test_parse_multipoint_wkt_reports_duplicate_and_non_finite() -> None:
    """A duplicate or non-finite position surfaces MultiPoint's own message."""
    dup = parse_multipoint_wkt("MULTIPOINT((0 0), (0 0))")
    nan = parse_multipoint_wkt("MULTIPOINT((nan 0))")

    assert isinstance(dup, InvalidCoords)
    assert "unique" in dup.message
    # MultiPoint's indices must survive the wrapper: restating the rule here, as
    # parse_point_wkt does for Position, would drop them from every 400.
    assert "position 1 (0.0, 0.0) repeats position 0 (0.0, 0.0)" in dup.message
    assert isinstance(nan, InvalidCoords)
    assert "finite" in nan.message
    assert "at position 0 got: (nan, 0.0)" in nan.message


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
