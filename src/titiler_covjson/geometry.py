"""The geometry a coverage is built over: a position, a polygon, a set of points.

Read these as this service's own types, not as the shapes their names usually
name. :class:`Polygon` is not an OGC polygon, and it does not become one by
looking like one. What each type admits is fixed by what the service can
faithfully turn into a coverage, which is neither a subset nor a superset of any
standard's notion: this ``Polygon`` never checks self-intersection or that holes
fall inside the exterior, both of which OGC validity requires, while
:class:`Position` refuses a non-finite coordinate that a geometry library would
hold without complaint.

That containment is deliberate, and it is what lets these be plain values. A
value that exists here is one the service can serve, so no layer downstream
re-validates and no second "checked geometry" type is needed. The cost is that a
rule from any source may land in a type here, whether it comes from the geometry,
from CoverageJSON, or from this service's own reach, so a constraint's provenance
belongs in a comment where it is enforced.

:class:`Position`, :class:`Polygon`, and :class:`MultiPoint` are frozen value
objects. A request's Well-Known Text (WKT) is parsed into one, and a coverage
input then pairs it with the data read across it. Both of those layers depend on
this module and this module depends on nothing, so the geometry has a single
definition that neither side owns.

Each type validates itself at construction, rejecting whatever can be judged from
the value alone: a non-finite coordinate, a ring that is unclosed or too short to
bound an area, or a position repeated within a set. An invalid value therefore
cannot be constructed. Broader rules stay out: whether a request's text parsed at
all is the parser's business, and which geometries an endpoint will serve is the
endpoint's.

The coordinate reference system is not stored here. It lives alongside, on the
coverage input holding the geometry, so these coordinates are bare numbers in that
CRS.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Position:
    """A point location in a coordinate reference system.

    Carries the horizontal coordinates of a single position and, optionally, a
    vertical coordinate. The coordinate reference system is not stored here: it
    lives alongside on the :class:`CoverageInput` variant that holds the position
    (as ``crs``), so ``x``, ``y``, and ``z`` are bare numbers expressed in that
    CRS.

    Attributes:
        x: Easting or longitude, in the holder's CRS.
        y: Northing or latitude, in the holder's CRS.
        z: Vertical coordinate (e.g., height or depth), or ``None`` when the
            position is purely horizontal.
    """

    x: float
    y: float
    z: float | None = None

    def __post_init__(self) -> None:
        """Reject non-finite coordinates at construction.

        A NaN or infinite coordinate names no location on the ground and would
        serialize to a JSON ``null`` in a coverage domain axis, silently
        corrupting the output rather than failing, so it is rejected here where
        the value becomes a :class:`Position`.

        Raises:
            ValueError: If ``x``, ``y``, or a non-``None`` ``z`` is not finite
                (NaN or infinity).
        """
        finite = (self.x, self.y) if self.z is None else (self.x, self.y, self.z)

        if not all(map(math.isfinite, finite)):
            msg = (
                "Position coordinates must be finite (not NaN or infinity); got "
                f"x={self.x}, y={self.y}, z={self.z}."
            )
            raise ValueError(msg)


@dataclass(frozen=True)
class Polygon:
    """A polygon geometry: one exterior ring and zero or more interior rings.

    Carries the polygon's linear rings as ``(exterior, *holes)``. Each ring is a
    sequence of ``(x, y)`` vertices, closed so the first and last vertex coincide.
    The coordinate reference system is not stored here: it lives alongside on the
    :class:`CoverageInput` variant that holds the polygon (as ``crs``), so the
    vertex coordinates are bare numbers expressed in that CRS.

    Attributes:
        rings: The linear rings as ``(exterior, *holes)``. The first ring is the
            exterior boundary; any further rings are interior boundaries (holes).
            Each ring is a tuple of ``(x, y)`` vertices, closed (first vertex
            equal to last).
    """

    rings: tuple[tuple[tuple[float, float], ...], ...]

    def __post_init__(self) -> None:
        """Reject a structurally invalid or non-finite polygon at construction.

        A ring that is unclosed, too short to bound an area, or carries a NaN or
        infinite coordinate names no region on the ground and would produce an
        invalid clip or a silently corrupt domain axis, so it is rejected here
        where the value becomes a :class:`Polygon`.

        Raises:
            ValueError: If there are no rings; if any coordinate is not finite
                (NaN or infinity); if any ring has fewer than four vertices; or if
                any ring is not closed (its first vertex differs from its last).
        """
        if not self.rings:
            msg = "A polygon must have at least one ring (the exterior ring)."
            raise ValueError(msg)

        coordinates = [
            coordinate
            for ring in self.rings
            for vertex in ring
            for coordinate in vertex
        ]

        if not all(map(math.isfinite, coordinates)):
            msg = "Polygon coordinates must be finite (not NaN or infinity)."
            raise ValueError(msg)

        for ring in self.rings:
            if len(ring) < 4:
                msg = (
                    "Each polygon ring must have at least four vertices "
                    f"(a closed triangle); got {len(ring)}."
                )
                raise ValueError(msg)

            if ring[0] != ring[-1]:
                msg = (
                    "Each polygon ring must be closed (first vertex equal to last); "
                    f"got {ring[0]} != {ring[-1]}."
                )
                raise ValueError(msg)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """The ``(minx, miny, maxx, maxy)`` bounding box spanning every ring.

        Spans all rings, not just the exterior. Construction is permissive and
        does not enforce that holes lie inside the exterior, and the read a
        polygon drives (rio-tiler's ``feature``) bounds every ring, so an interior
        ring reaching past the exterior must widen this box too; otherwise it
        could slip a large read past a cell-count ceiling checked here. For a
        well-formed polygon, whose holes lie inside the exterior, this equals the
        exterior's box. A degenerate polygon (a point or an axis-aligned line) has
        ``minx == maxx`` or ``miny == maxy``.

        Returns:
            tuple[float, float, float, float]: The bounding box, in the holder's
                CRS.
        """
        vertices = [vertex for ring in self.rings for vertex in ring]
        xs = [x for x, _ in vertices]
        ys = [y for _, y in vertices]

        return min(xs), min(ys), max(xs), max(ys)


@dataclass(frozen=True)
class MultiPoint:
    """A set of point locations sampled together.

    Carries one or more ``(x, y)`` positions, in the order given. Bare pairs, not
    :class:`Position` values: a :class:`Position` admits an optional vertical
    coordinate, and this service samples a single 2-D raster with no vertical
    level to honor one. Storing positions as pairs leaves nowhere to put a ``z``,
    so the unservable state cannot be constructed rather than being built and then
    rejected. The coordinate reference system is not stored here: it lives
    alongside on the :class:`CoverageInput` variant that holds the multipoint
    (as ``crs``), so the coordinates are bare numbers expressed in that CRS.

    Attributes:
        positions: The sampled ``(x, y)`` positions, in request order. At least
            one, all finite, and all distinct.
    """

    # 2-D only. Vertical selection is deferred, not rejected outright (see
    # docs/adr/0001-covjson-http-api-direction.md): the day a z-backed dataset
    # lands, CoverageJSON already permits an ["x", "y", "z"] MultiPoint composite,
    # so widen this representation here. Position carries a z field for the same
    # future; this type does not, to keep an unservable z unconstructible today.
    positions: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        """Reject an empty, non-finite, or duplicate-bearing multipoint.

        Raises:
            ValueError: If there are no positions; if any coordinate is not finite
                (NaN or infinity); or if any position repeats.
        """
        if not self.positions:
            msg = "A multipoint must have at least one position."
            raise ValueError(msg)

        if not all(
            math.isfinite(coordinate)
            for position in self.positions
            for coordinate in position
        ):
            msg = "MultiPoint coordinates must be finite (not NaN or infinity)."
            raise ValueError(msg)

        # Distinct positions. A MultiPoint domain lists these as a coverage axis,
        # whose values index the range: a repeated position would leave "which
        # range element does this refer to?" ambiguous. The CoverageJSON schema
        # (OGC 21-069r2) makes this concrete. Its `valuesAxisBase.values` declares
        # `uniqueItems: true`, so a duplicate serializes to a schema-invalid
        # document. Note that -0.0 and 0.0 collide here exactly as they do under
        # the schema's uniqueness test (both compare equal), so this check and the
        # schema agree on every pair.
        if len(set(self.positions)) != len(self.positions):
            msg = (
                "MultiPoint positions must be unique; "
                "a coverage axis cannot index a value twice."
            )
            raise ValueError(msg)
