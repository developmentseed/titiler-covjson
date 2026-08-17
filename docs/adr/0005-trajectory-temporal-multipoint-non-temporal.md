# ADR-0005: The Trajectory domain is temporal; MultiPoint is the non-temporal multi-position query

## Status

Accepted

Amends [ADR-0004](0004-non-temporal-surface-edr-query-verbs.md) (reclassifies
`/trajectory`).

## Context

[ADR-0004](0004-non-temporal-surface-edr-query-verbs.md) defined the
non-temporal CoverageJSON surface as the OGC API - Environmental Data Retrieval
(EDR) non-temporal query verbs and listed `/trajectory` (a Trajectory-domain
coverage) among them, alongside `/position` (Point) and `/area` (Polygon), with
`/bbox` standing in for `cube`.

The CoverageJSON standard (OGC Community Standard 21-069r2) constrains the
Trajectory domain: its single `composite` axis carries tuples whose
`coordinates` are exactly `["t", "x", "y"]` or `["t", "x", "y", "z"]` (three or
four values each), referenced by a temporal reference system for `t` and a
spatial one for `x`, `y` (and `z`). A tuple without a time value is not a valid
Trajectory. Verified against the vendored CoverageJSON JSON Schema: an
`["x", "y"]` composite is rejected for `domainType: "Trajectory"` and accepted
only for `domainType: "MultiPoint"`.

Two facts compound this and shape how it is enforced:

- `covjson-pydantic` does not enforce the rule. It will serialize a `t`-less
  Trajectory that no conformant consumer accepts, so the guard is the schema
  test, not the model.
- The vendored schema expresses its per-`domainType` constraints with the
  draft-07 `dependencies` keyword, which a newer JSON Schema validator silently
  ignores. Validating a coverage against the named `"domain"` definition
  therefore skips the `t` check; only validating the full `Coverage` enforces
  it.

It follows that a spatially sampled path with no time is a MultiPoint coverage,
not a Trajectory. The EDR query verb that carries multiple discrete positions is
Position: `/position` accepts a `MULTIPOINT` geometry as well as a `POINT`, and
its CoverageJSON representation is the MultiPoint domain. A Trajectory needs a
time per vertex, which makes `/trajectory` a temporal verb rather than a
non-temporal one.

## Decision

`/trajectory` produces a Trajectory (temporal) domain and belongs to the
Temporal endpoint surface, not the non-temporal one. Its time coordinate is
supplied per vertex in the request (a `LINESTRING M` measure interpreted as
time, or a datetime list). This is honest over a static 2-D raster: the `t`
records when a platform was at each position, not a claim that the raster itself
varies in time, and it needs no temporal dataset backing.

The non-temporal multi-position capability is MultiPoint, delivered through the
EDR Position verb: `/position` accepts a `MULTIPOINT` and returns a
MultiPoint-domain coverage. This is the non-temporal slice, and it precedes
`/trajectory`.

The composite-tuple modeler (a `composite` axis with `dataType: "tuple"` and
per-band ranges indexed over `composite`) is built once by the MultiPoint slice.
Trajectory reuses it, adding only the `t` element to each tuple and a temporal
reference system.

Coverages that assert a Trajectory (or any composite-tuple domain) are validated
against the full `Coverage` schema, not the named `"domain"` definition, so the
`t` requirement is actually enforced.

## Alternatives considered

- **Keep `/trajectory` non-temporal, emitting a spatial-only MultiPoint under
  the `/trajectory` route.** Schema-valid, but an endpoint named `/trajectory`
  that never returns a Trajectory is misleading, and MultiPoint's EDR home is
  `/position`, which already accepts `MULTIPOINT`. Rejected: it misnames the
  capability and duplicates the Position verb on a second route.
- **Synthesize a `t` (a vertex index or a constant) to satisfy the schema.**
  Rejected as dishonest, on the same reasoning
  [ADR-0001](0001-covjson-http-api-direction.md) used to reject shipping `/cube`
  with unhonored `z`/`datetime`: naming a coverage for a dimension its data does
  not carry misleads clients.
- **Leave the classification as ADR-0004 wrote it.** Rejected: it contradicts
  the schema, and a `/trajectory` built as non-temporal would emit coverages no
  conformant consumer accepts.

## Consequences

- `/trajectory` (#57) moves from the non-temporal to the Temporal endpoint
  surface; its scope is rewritten to a `t`-bearing Trajectory sourced from
  request-supplied per-vertex timestamps, independent of the deferred temporal
  dataset backing.
- A MultiPoint slice (#65, extend `/position` to accept `MULTIPOINT`) is added
  to the non-temporal surface and precedes `/trajectory`; it introduces the
  composite-tuple modeler that Trajectory reuses.
- ADR-0004's enumeration of `/trajectory` as a non-temporal verb is amended, and
  ADR-0004 gains an "Amended by ADR-0005" note.
- Early design notes that show a spatial-only Trajectory (an `["x", "y"]`
  composite labeled Trajectory) are corrected: a spatial-only line is
  MultiPoint; a Trajectory carries `t`.
- `corridor` (a buffered trajectory) remains a deferred EDR remainder that
  depends on `/trajectory`.
- Revisit gate: if a temporal dataset backing lands, `/trajectory` could
  additionally source `t` from the data. That is a superset of this decision,
  not a reversal.
