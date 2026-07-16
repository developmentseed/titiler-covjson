"""Parse the Well-Known Text (WKT) a request names its geometry with.

Hand-rolled and deliberately dependency-free. The grammars accepted here are
small, and a geometry library backed by GEOS would be a heavy dependency to add
for two floats and a ring list; confining WKT handling to this module keeps that
a contained decision, since only these function bodies would change if one ever
became worthwhile.

What is accepted is narrower than WKT itself. These parsers refuse well-formed
input the grammar allows whenever accepting it would promise something this
service cannot deliver: a 3-D or measured geometry parses as WKT but names a
vertical level no single 2-D raster can sample. Refusals of both kinds report the
same way, so a caller does not have to distinguish "not WKT" from "not something
we serve".

Each parser hands its coordinates to a geometry type, which owns the invariants
of the value itself (finiteness, ring closure). This module owns only the
grammar.
"""

from __future__ import annotations

import re

from titiler.core.errors import BadRequestError

from titiler_covjson.geometry import Polygon, Position


def parse_point_wkt(coords: str) -> Position:
    """Parse a 2-D WKT ``POINT(x y)`` into a :class:`Position`.

    Accepts a plain 2-D ``POINT(x y)`` with whitespace-separated coordinates;
    everything else is rejected with ``BadRequestError``:

    - a 3-D or measured geometry (a ``Z`` / ``M`` / ``ZM`` tag, or three or four
      coordinates): the 2-D raster backing cannot sample a vertical level, so
      echoing the coordinate back or dropping it would both be dishonest;
    - a non-POINT geometry, ``POINT EMPTY``, the wrong coordinate count, a
      comma-separated ``POINT(1, 2)`` (the comma is the ``MULTIPOINT`` separator,
      not an intra-point one), or any other malformed input;
    - a non-finite coordinate (NaN or infinity), which would otherwise serialize
      to a silent ``null`` domain axis.

    Args:
        coords: The raw ``coords`` query value.

    Returns:
        Position: The parsed 2-D position.

    Raises:
        BadRequestError: If ``coords`` is not a finite 2-D WKT point. The host
            application's titiler exception handlers render this as a 400
            response.

    Examples:
        >>> parse_point_wkt("POINT(0 0)")
        Position(x=0.0, y=0.0, z=None)
        >>> parse_point_wkt("  point ( -5.0   2.5 ) ")
        Position(x=-5.0, y=2.5, z=None)

        A 3-D point is rejected (vertical selection is unsupported here):

        >>> parse_point_wkt("POINT Z (0 0 5)")
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: Vertical or measured coordinates ...

        A malformed point is rejected:

        >>> parse_point_wkt("POINT(0)")
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: Invalid position 'POINT(0)': ...
    """
    if (match := _POINT_WKT.match(coords)) is None:
        msg = f"Invalid position {coords!r}: expected WKT POINT(x y), e.g., POINT(0 0)."
        raise BadRequestError(msg)

    tokens = match["coords"].split()

    # Rejecting rather than ignoring a vertical coordinate is the direction set in
    # docs/adr/0001-covjson-http-api-direction.md: a 2-D raster cannot honor one,
    # so accepting it would promise a selection the response does not make.
    if match["tag"] or len(tokens) in (3, 4):
        msg = (
            "Vertical or measured coordinates are not supported: this endpoint "
            f"samples a single 2-D raster. Provide a 2-D POINT(x y); got {coords!r}."
        )
        raise BadRequestError(msg)

    if len(tokens) != 2:
        msg = (
            f"Invalid position {coords!r}: expected two coordinates, e.g., POINT(0 0)."
        )
        raise BadRequestError(msg)

    # float() rejects non-numeric tokens and Position rejects non-finite ones
    # (NaN/infinity), so one handler covers both: Position owns the finiteness
    # invariant as the single source of truth (mirroring _validate_label_crs,
    # which likewise turns a helper's ValueError into a BadRequestError).
    try:
        x, y = (float(token) for token in tokens)

        return Position(x, y)
    except ValueError:
        msg = (
            f"Invalid position {coords!r}: coordinates must be finite numbers "
            "(not NaN or infinity), e.g., POINT(0 0)."
        )
        raise BadRequestError(msg) from None


def parse_polygon_wkt(coords: str) -> Polygon:
    """Parse a 2-D WKT ``POLYGON((x y, ...), ...)`` into a :class:`Polygon`.

    Splits the parenthesized ring list and reads each vertex as two floats,
    handing the rings to :class:`Polygon`, which owns the ring invariants (closed,
    at least four vertices, finite coordinates). Accepts a single 2-D ``POLYGON``
    with one exterior ring and zero or more interior rings (holes); everything
    else is rejected with ``BadRequestError``:

    - a 3-D or measured geometry (a ``Z`` / ``M`` / ``ZM`` tag, or a vertex with
      three or four coordinates): the 2-D raster backing has no vertical level to
      reduce over, so echoing the coordinate back or dropping it would both be
      dishonest;
    - a non-POLYGON geometry (including ``MULTIPOLYGON``, whose ``MULTI`` prefix
      fails the pattern), ``POLYGON EMPTY``, an empty ring, a non-finite or
      non-numeric coordinate, an unclosed ring, a ring with fewer than four
      vertices, or any other malformed input.

    Args:
        coords: The raw ``coords`` query value.

    Returns:
        Polygon: The parsed 2-D polygon.

    Raises:
        BadRequestError: If ``coords`` is not a valid 2-D WKT polygon. The host
            application's titiler exception handlers render this as a 400
            response.

    Examples:
        >>> parse_polygon_wkt("POLYGON((0 0, 1 0, 1 1, 0 0))").rings
        (((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)),)

        A 3-D polygon is rejected (vertical selection is unsupported here):

        >>> parse_polygon_wkt("POLYGON Z ((0 0 1, 1 0 1, 1 1 1, 0 0 1))")
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: Vertical or measured coordinates ...

        An unclosed ring is rejected:

        >>> parse_polygon_wkt("POLYGON((0 0, 1 0, 1 1, 0 1))")
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: Invalid polygon 'POLYGON((0 0, ...
    """
    if (match := _POLYGON_WKT.match(coords)) is None:
        msg = (
            f"Invalid polygon {coords!r}: expected WKT POLYGON((x y, x y, ...)), "
            "e.g., POLYGON((0 0, 1 0, 1 1, 0 0))."
        )
        raise BadRequestError(msg)

    # Rejected for the reason given at the same guard in parse_point_wkt.
    if match["tag"]:
        msg = (
            "Vertical or measured coordinates are not supported: this endpoint "
            f"reduces a single 2-D raster. Provide a 2-D POLYGON; got {coords!r}."
        )
        raise BadRequestError(msg)

    if not (ring_strings := _POLYGON_RING.findall(match["rings"])):
        msg = (
            f"Invalid polygon {coords!r}: expected at least one parenthesized ring, "
            "e.g., POLYGON((0 0, 1 0, 1 1, 0 0))."
        )
        raise BadRequestError(msg)

    # _parse_xy_pairs rejects a non-2-D or non-numeric vertex and Polygon rejects a
    # non-finite, unclosed, or too-short ring; one handler turns every ValueError
    # into a 400. Polygon owns the ring invariants as the single source of truth
    # (mirroring parse_point_wkt delegating finiteness to Position).
    try:
        return Polygon(rings=tuple(map(_parse_xy_pairs, ring_strings)))
    except ValueError as exc:
        msg = f"Invalid polygon {coords!r}: {exc}"
        raise BadRequestError(msg) from exc


# WKT for a point: `POINT`, an optional Z/M/ZM tag, then whitespace-separated
# coordinates in parentheses. parse_point_wkt inspects the tag and coordinate
# count to reject 3-D/measured geometries; the comma (the MULTIPOINT coordinate
# separator) is deliberately not allowed inside a single point.
_POINT_WKT = re.compile(
    r"^\s*POINT\s*(?P<tag>Z|M|ZM)?\s*\(\s*(?P<coords>[^()]*?)\s*\)\s*$",
    re.IGNORECASE,
)

# WKT for a polygon: `POLYGON`, an optional Z/M/ZM tag, then the parenthesized
# ring list `((x y, ...), (x y, ...))`. parse_polygon_wkt inspects the tag and
# splits the ring list with _POLYGON_RING (each parenthesized group is one ring);
# MULTIPOLYGON fails this pattern (the leading `MULTI`), keeping a single polygon.
_POLYGON_WKT = re.compile(
    r"^\s*POLYGON\s*(?P<tag>Z|M|ZM)?\s*\((?P<rings>.*)\)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_POLYGON_RING = re.compile(r"\(([^()]*)\)")


def _parse_xy_pairs(ring: str) -> tuple[tuple[float, float], ...]:
    """Parse a WKT ring body (``x y, x y, ...``) into a tuple of ``(x, y)`` vertices.

    Args:
        ring: A single ring's comma-separated ``x y`` vertices (the text inside
            one ring's parentheses).

    Returns:
        tuple[tuple[float, float], ...]: The parsed vertices, in order.

    Raises:
        ValueError: If a vertex is not a 2-D ``x y`` pair (including a 3-D or
            measured vertex), or a coordinate is not a number. The caller turns
            this into a ``BadRequestError``.
    """
    vertices: list[tuple[float, float]] = []

    for pair in ring.split(","):
        tokens = pair.split()

        if len(tokens) in {3, 4}:
            msg = (
                "vertical or measured coordinates are not supported: each vertex "
                f"must be a 2-D 'x y' pair; got {pair.strip()!r}."
            )
            raise ValueError(msg)

        if len(tokens) != 2:
            msg = f"each ring vertex must be an 'x y' pair; got {pair.strip()!r}."
            raise ValueError(msg)

        # float() rejects a non-numeric token; a non-finite one (NaN/infinity)
        # parses here and is rejected by Polygon, as in parse_point_wkt.
        x, y = (float(token) for token in tokens)
        vertices.append((x, y))

    return tuple(vertices)
