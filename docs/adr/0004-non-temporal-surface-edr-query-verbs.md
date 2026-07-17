# ADR-0004: The non-temporal CoverageJSON surface is the EDR non-temporal query verbs (drop XYZ `/tiles`)

## Status

Accepted

Amended by [ADR-0005](0005-trajectory-temporal-multipoint-non-temporal.md): the
CoverageJSON Trajectory domain requires a `t` coordinate, so `/trajectory` is a
temporal verb and moves to the Temporal endpoint surface. The non-temporal
multi-position capability is MultiPoint, delivered through the Position verb
(`/position` accepting `MULTIPOINT`). The non-temporal surface below should be
read with that correction.

## Context

[ADR-0001](0001-covjson-http-api-direction.md) set the API direction to Option B:
an OGC API - Environmental Data Retrieval (EDR) aligned request vocabulary
delivered as CoverageJSON, with full EDR conformance kept reachable. The
value-returning endpoints that follow from that direction are EDR query verbs:
`/position` (Point), `/area` (Polygon zonal reduction), and `/trajectory`
(Trajectory). The 2-D `/bbox` -> Grid slice is a deliberate, documented exception
standing in for the EDR `cube` verb until a z/t backing lands (ADR-0001).

The "Non-temporal endpoint surface" milestone, however, also carried a
`/tiles/{z}/{x}/{y}` -> Grid endpoint (issue #58), described alongside the others
as an "EDR-aligned endpoint." It is not one. XYZ tiling is OGC API - Tiles, a
separate standard with its own path and tiling model; EDR defines no tile query
type. Including it reintroduces the two-taxonomies problem that
[docs/07 Section 4](../07-api-design-alternatives.md) used to reject the bespoke
baseline: a single surface that documents and maintains two overlapping request
vocabularies.

The design study had already reached this conclusion.
[docs/07 Section 7.3](../07-api-design-alternatives.md) separates the two distinct
ideas that a "tiles" endpoint conflates: a self-contained Grid `Coverage` per XYZ
tile (map-tile-as-CovJSON), versus `TiledNdArray`, the CoverageJSON range encoding
that delivers one `Coverage`'s values as tiles. It recommends dropping the former
from v1 (its open question 4 answers "Recommend yes"). That recommendation predates
ADR-0001 and was never propagated: the roadmap kept Story 7, and the milestone and
issue #58 kept `/tiles`. This record applies it.

Two facts make the drop cheap:

- A tile is a bounding box. A client that wants tile `z/x/y` computes its bounds
  from the TileMatrixSet and calls `/bbox/{minx},{miny},{maxx},{maxy}`. The `/bbox`
  slice already covers full-resolution extraction and, via output sizing,
  downsampling.
- No CoverageJSON client renders an XYZ pyramid of per-tile `Coverage`s, so the one
  ergonomic a `/tiles` route would add (XYZ URLs for a tile renderer) serves a
  consumer that does not exist for this format.

## Decision

The non-temporal CoverageJSON surface is the set of EDR non-temporal query verbs:
`/position` (shipped), `/area`, and `/trajectory`, with `/bbox` standing in for
`cube` per ADR-0001. Drop the XYZ `/tiles` endpoint: close issue #58 and retire
roadmap Story 7.

Two use cases a tiles endpoint might have served are covered elsewhere:

- Map-tile extraction: `/bbox` with client-computed tile bounds.
- Tiled delivery of a large coverage: `TiledNdArray` (roadmap Story 12), the
  standard CoverageJSON mechanism, which delivers one `Coverage`'s range as tiles
  behind a URL template rather than exposing a separate XYZ route.

The remaining EDR non-temporal query verbs not yet built, `radius` and `corridor`,
are recorded here as the deliberate deferred remainder. They are not filed as
issues or built until a concrete need arises: `radius` is closely approximated by
`/area` with a circular polygon, and `corridor` (a buffered trajectory) depends on
`/trajectory` landing first.

## Alternatives considered

- **Build `/tiles` anyway.** It is nearly free to implement (it reuses the Grid
  read and modeler path entirely) and was already milestoned. Rejected: low
  implementation cost is not the test. It widens the public surface into a second
  (OGC Tiles) taxonomy for a niche capability with no CoverageJSON client, and its
  extraction use case is already met by `/bbox`.
- **Keep `/tiles` but park it as an explicit non-EDR convenience.** Rejected in
  favor of a clean drop: parking preserves the surface-widening and the
  maintenance and documentation burden while signaling the feature is unwanted
  anyway. If a real XYZ-CovJSON consumer or a `TiledNdArray` backing store ever
  needs a per-tile route, it can be reintroduced deliberately then (docs/07
  Section 7.3 notes a per-tile endpoint is a legitimate backing store for a
  `TiledNdArray` URL template).
- **File `radius`/`corridor` now to enumerate the surface.** Rejected as
  speculative: documenting the deferred remainder here gives the coherence without
  carrying open issues for work that has no consumer and, for `corridor`, no
  prerequisite yet.

## Consequences

- The non-temporal surface reads as one vocabulary (EDR query verbs) plus the
  single, documented `/bbox` -> `cube` exception, rather than EDR verbs plus an OGC
  Tiles route.
- A future need for tiled delivery is served by `TiledNdArray` (Story 12); a future
  need for an XYZ route, if one ever materializes, is a deliberate reopen rather
  than an implicit commitment.
- Roadmap Story 7 is retired and issue #58 is closed. The "Non-temporal endpoint
  surface" milestone is reframed to the EDR verbs.
- The gate to revisit: a concrete CoverageJSON consumer that requires per-tile XYZ
  `Coverage`s, or a `TiledNdArray` implementation that chooses a per-tile endpoint
  as its backing store.
