# ADR-0001: CoverageJSON HTTP API direction -- EDR-vocabulary surface on a dedicated `BaseFactory` subclass

## Status

Accepted

## Context

The model layer (`helpers.py`, `input.py`, `modeler.py`) is implemented and tested
for the Grid domain, but there is no HTTP layer yet (`routes.py` / `router.py` are
stubs). Before wiring the first endpoint end-to-end -- and a Docker container to
exercise it -- the endpoint shape needs to be settled, because it is far cheaper to
choose now than after clients depend on it.

[07-api-design-alternatives.md](../07-api-design-alternatives.md) compared three
non-bespoke directions against the current bespoke design in doc 02: (A) CovJSON as
a format on TiTiler's own endpoint shapes; (B) a hybrid that adopts OGC API - EDR
(Environmental Data Retrieval) request vocabulary without committing to full
conformance; (C) full EDR conformance. That study, and the PR #26 reviews from the
original API designer and a TiTiler maintainer, are the basis for this record; the
study remains the supporting analysis and this ADR captures only the chosen
direction.

The one product question the study isolated -- *is EDR conformance a project
goal?* -- was answered **"maybe"**: open and uncommitted, but explicitly not a
"no". Anchoring on the OGC standard is expected to make the implementation more
robust and better specified even if the project never certifies.

## Decision

Adopt **Option B**: an EDR-aligned request vocabulary delivered as CoverageJSON,
with Option C (full conformance) kept reachable. Concretely:

- **Mechanism.** Deliver the surface as a **dedicated `titiler.core`
  `BaseFactory` subclass** that owns its routes, not as a `FactoryExtension`
  grafted onto `TilerFactory`. Reuse is at the **dependency-injector** level
  (band/index selection, `expression`, `nodata`, `rescale`, resampling, CRS), not
  route inheritance. This follows titiler-stacapi's `OGCEndpointsFactory(BaseFactory)`
  pattern.
- **First slice.** An honest 2-D `GET /bbox/{minx},{miny},{maxx},{maxy}` ->
  `Grid`, reusing the existing `GridInput` + modeler path. The EDR `/cube` verb
  (bbox + z + datetime) is **deferred** until a real z/t axis backs it, because
  shipping a hypercube verb whose dimensionality we cannot honor would mislead EDR
  clients.
- **Resolver seam.** The factory turns a request into a `CoverageInput` through a
  single backing-agnostic seam. Today it is single-dataset (`url=` /
  `DatasetParams`), 2-D, no `t`. A temporal backing (which gives `/cube` and
  PointSeries a real `t` source, and lets STAC collection IDs double as EDR
  `collectionId`s) can be added later; titiler-stacapi is the recommended,
  STAC-first option and a time-dimensioned NetCDF/Zarr via xarray is another.
- **Vocabulary stability.** Adopt EDR `parameter-name` as an alias for TiTiler's
  `band_names` from day one (with `bands`/`bidx` and `expression` retained), so the
  EDR *parameter* vocabulary is stable immediately.
- **Format selection.** `f=CoverageJSON` (or the `Accept` header), no path suffix,
  reusing TiTiler's `f`-else-`Accept` idiom.
- **Mount prefix.** A factory-level setting via `BaseFactory.router_prefix`, not a
  hard-coded path.
- **Aggregation.** Treat full-resolution extraction, resolution downsampling
  (reduced-resolution `Grid` via `part(width,height)`/`max_size`), and statistical
  zonal reduction (scalar `Polygon`) as three distinct operations; downsampling is
  first-class and subsumes doc 02's separate `/overview` endpoint.

## Alternatives considered

- **Option A (fully TiTiler-native vocabulary).** Lowest friction, but forgoes EDR
  interoperability and discoverability. Only an explicit "no" to conformance would
  select it; the answer was "maybe".
- **Option C (full EDR conformance now).** Most interoperable, but its
  Collections/conformance model is a large design effort that would block the
  near-term goal of proving the Grid path in a container. Deferred, not rejected --
  Option B's growth path reaches it, and a STAC-backed resolver seam shrinks its
  remaining cost.
- **`FactoryExtension` mechanism.** Rejected: it inherits the host `TilerFactory`'s
  single-dataset route shape, which fights the collection-scoped paths a future
  Option C needs.
- **Ship `/cube` with `z`/`datetime` as no-op parameters.** Rejected as dishonest
  for as long as the dimensionality is unhonored; naming the 2-D slice for what it
  returns is clearer.
- **Depend on titiler-stacapi now.** Rejected as premature coupling. The pattern is
  adopted now; the package is deferred behind the resolver seam and named as one
  option rather than taken as a dependency.

## Consequences

- **One planned verb transition.** The EDR *parameter* vocabulary carries over
  rename-free, but `/bbox` -> `/cube` is a deliberate, bounded exception to
  "rename-free" once a z/t backing lands. This is accepted in exchange for not
  shipping a misleading verb.
- **We copy/wire routes** rather than inheriting them from `TilerFactory` -- a
  modest, well-understood cost (titiler-stacapi does the same).
- **Standalone, the surface delivers EDR *parameter* interop but not *path-level*
  discoverability;** that arrives only with collection-scoped mounting / Option C.
  We must not claim `/conformance`-level conformance until it is real.
- **Dependency-floor prerequisite.** The factory is the first real `titiler.core`
  import, so the `>= 0.18.0` floor must rise first (`BaseFactory` name needs
  >= 0.19.0; `conforms_to` needs >= 0.22.0). This is tracked as issue #27
  (`titiler.core` 2.x + Python 3.11), to be resolved before the slice. A separate
  ADR may record that bump when #27 is decided.
- **Follow-on doc updates.** doc 02 (bespoke API definition) and doc 05
  (implementation roadmap) will be rewritten to this direction when the endpoint
  spec is written; they are out of scope for this record.

The gate to revisit: an explicit "no" to conformance (would favor Option A), or a
committed "yes" with resources to build the Collections model (would accelerate
Option C).
