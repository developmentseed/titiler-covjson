# API Design Alternatives -- Analysis & Comparison

> **Status:** Draft for review. Purpose: give the original API designer a clear,
> grounded comparison of the API-surface options for `titiler-covjson` so we can
> agree on a direction before we wire the first endpoint end-to-end. This
> document does **not** change [02-api-definition.md](02-api-definition.md); it
> proposes how that document might evolve.

> [!NOTE]
> **How to review this (if you're short on time).** Exactly one decision is yours
> to make -- **is EDR conformance a project goal?** Everything else already carries
> a recommendation.
>
> - **~2 min (just decide):** read Section 8 (Recommendation), the Section 8.1
>   table (your *yes / maybe / no* answer -> recommended option and first
>   endpoint), and the five questions in Section 9. Answering Q1 (conformance:
>   yes / maybe / no) is the one input we actually need; the rest you can confirm
>   or tweak.
> - **~10 min (sanity-check the call):** add Section 6 (comparison matrix) and skim
>   Section 5 (the three options, A / B / C).
> - **Full read (the *why*):** Sections 2-3 (what TiTiler and OGC API-EDR already
>   give us) and Section 7 (cross-cutting decisions, each with a recommendation).
>
> **Bottom line:** the recommendation is **Option B** (an EDR-vocabulary TiTiler
> factory) with a first slice that is an **honest 2-D bounding-box `Grid`** route
> (`/bbox/{minx},{miny},{maxx},{maxy}` -> `Grid`), *not* `/cube`. The EDR `/cube`
> verb (bbox + z + datetime) is deferred until a real z/t axis backs it (Section
> 7.6). The only answer that changes the option choice is an explicit *"no"* to
> conformance (see Section 8.1), which points to Option A.
>
> **Status update (decided since first draft).** The conformance question (Q1) has
> been answered **"maybe"** -- open but explicitly *not* a "no" -- which confirms
> **Option B**. Two reviewer-driven shifts are now folded in: the surface is
> delivered as a **dedicated TiTiler `BaseFactory` subclass**, not a
> `FactoryExtension` grafted onto `TilerFactory` (Section 2.2); and the first slice
> is the honest 2-D `/bbox` `Grid` above rather than `/cube` (Section 7.6). This
> analysis is the supporting study for **ADR-0001** (`docs/adr/`), which records the
> chosen direction.

## 1. Why we are revisiting the API shape

The model layer (`helpers.py`, `input.py`, `modeler.py`) is implemented and
tested for the Grid domain, but there is still **no HTTP layer** --
[`routes.py`](../src/titiler_covjson/routes.py) and
[`router.py`](../src/titiler_covjson/router.py) are empty stubs. Before we add an
HTTP surface (and a Docker container to exercise it end-to-end), it is far
cheaper to settle the endpoint design now than after clients depend on it.

Throughout, an **end-to-end slice** (or *vertical slice*) means a single endpoint
wired through every layer at once -- HTTP request -> rio-tiler read ->
`CoverageInput` -> modeler -> a serialized `application/prs.coverage+json`
response -- so the whole path is proven before we broaden it. The slice we intend
to build **first** is one **Grid** endpoint -- an honest 2-D bounding-box grid
(`/bbox/{minx},{miny},{maxx},{maxy}`; see Sections 8, 8.1, and 7.6), *not* the EDR
`/cube` verb, whose bbox + z + datetime semantics we cannot yet honor (Section
7.6). It is chosen because the Grid model layer is already implemented and tested
-- so the only thing between us and a working container is the HTTP shape this
document exists to settle. Picking that shape well, before any code, is the whole
motivation for this comparison.

The current design ([02-api-definition.md](02-api-definition.md)) defines a
**parallel, bespoke API** (`/covjson/point`, `/covjson/bbox`, `/covjson/tiles`,
`/covjson/transect`, `/covjson/info`, `/covjson/timeseries`, `/covjson/overview`).
The tension worth examining: the project framing is *"CoverageJSON as
an **output format** for TiTiler,"* yet a parallel verb tree re-creates query
patterns TiTiler already exposes, rather than treating CovJSON as a format on the
existing surface.

This document compares the three non-bespoke directions. The bespoke design is
summarized only as a baseline for contrast (Section 4).

## 2. Shared substrate -- what TiTiler already provides

All three options build on the same substrate, stated once here.

### 2.1 `TilerFactory` already implements the query taxonomy

Introspected from the installed `titiler.core` **0.24.2** (project pins
`titiler.core>=0.18.0,<1.0`), `TilerFactory` registers, among others:

| TiTiler endpoint | Read path | Natural CovJSON domain |
| --- | --- | --- |
| `GET /point/{lon},{lat}` | `Reader.point()` | Point / PointSeries |
| `GET /bbox/{minx},{miny},{maxx},{maxy}.{format}` | `Reader.part()` | Grid |
| `GET /bbox/{minx},{miny},{maxx},{maxy}/{width}x{height}.{format}` | `Reader.part()` | Grid |
| `GET /preview.{format}` | `Reader.preview()` | Grid (overview) |
| `POST /feature.{format}` (GeoJSON body) | `Reader.feature()` | Grid (polygon-masked) |
| `GET /statistics`, `POST /statistics` | zonal stats | aggregated (Polygon) |
| `GET /tiles/{tileMatrixSetId}/{z}/{x}/{y}.{format}` | `Reader.tile()` | Grid |
| `GET /info`, `GET /bounds` | `Reader.info()` | metadata-only |

The doc-02 endpoints map almost one-to-one onto these. The factory also supplies
reusable **dependency injectors** -- band/index selection, `expression`, `nodata`,
`rescale`, `resampling`, `colormap` (not all meaningful for CovJSON), CRS, and
the parameterized `tileMatrixSetId` (TMS). The reuse we want is of these
*dependency injectors* (importable, wired into our own endpoints), **not** of the
factory's route definitions -- a distinction that drives the mechanism choice in
Section 2.2.

### 2.2 Delivery mechanism: a dedicated `BaseFactory` subclass, not a `FactoryExtension`

There are two ways to add routes in TiTiler, and an earlier draft of this
document picked the wrong one. The two are:

- **`FactoryExtension`.** Its `register(factory)` adds routes onto an *existing*
  factory's router, inheriting that host factory's dependencies and route shape.
  This is how TiTiler's own first-party extensions attach (e.g.,
  `cogValidateExtension`, `wmsExtension`, `wmtsExtension`).
- **A dedicated factory** -- a subclass of `titiler.core.factory.BaseFactory`
  that owns its router and defines its own routes in `register_routes()`. This is
  how `titiler-stacapi` builds its OGC surface (`OGCEndpointsFactory(BaseFactory)`;
  see Sources).

**We deliver the CovJSON surface as a dedicated `BaseFactory` subclass** (the
direction both PR reviewers converged on). The reason an extension is the wrong
fit: a `FactoryExtension` inherits the *host* `TilerFactory`'s route shape and
single-dataset `url=` model, which actively fights an EDR-style surface -- in
particular the collection-scoped paths (`/collections/{collectionId}/...`) that a
future conformance step (Option C) needs. A dedicated factory instead lets us own
the path structure, choose exactly which dependency injectors to wire in (Section
2.1), and set the mount prefix (Section 7.7). The cost is that we do **not**
inherit ready-made route definitions: we copy/wire the endpoints we want -- a
modest, well-understood cost (`titiler-stacapi` does exactly this).

`BaseFactory` already carries the attributes this direction relies on: a
configurable `router_prefix` (Section 7.7), an `extensions` list (we can still
host our *own* `FactoryExtension`s later), and a `conforms_to` set -- the literal
hook for advertising EDR conformance classes on the B -> C path (Section 7.8). So
"reuse" here means **dependency-injector reuse** (Section 2.1), not route
inheritance; the format selector is shared across options (Section 7.1).

> **Dependency-floor note.** `BaseFactory` (under that name) and `conforms_to`
> entered `titiler.core` at **0.19.0** and **0.22.0** respectively; the project's
> current floor is `titiler.core>=0.18.0`. Because this factory is the *first* real
> `titiler.core` import in the codebase, the floor must rise before the slice is
> built -- tracked as issue #27 (which proposes `titiler.core` 2.x + Python 3.11).
> See Section 7.8.

### 2.3 One practical wrinkle: `.{format}` is not free real estate

TiTiler's image endpoints select output via a `.{format}` path suffix bound to
the `ImageType` enum and its render pipeline (`png`, `tif`, `jpeg`, `npy`, ...).
The blocker is **not** that "CovJSON is not an image" -- `npy` rides that suffix
and is not an image either. The narrower, accurate blocker is that **CovJSON is
not a member of the `ImageType` enum / render pipeline**, so it cannot ride that
suffix machinery directly: it is a format-registration gap, not an
image-semantics one. Either way the conclusion is the same and is reinforced by
the dedicated-factory choice (Section 2.2): our factory registers its **own**
routes and selects the format itself (Section 7.1, `f=CoverageJSON` or `Accept`)
rather than through the image `.{format}` suffix. This rules out "just add
`.covjson` to the existing image route for free."

## 3. Shared reference -- what OGC API-EDR provides

CoverageJSON is a standard response encoding of **OGC API - Environmental Data
Retrieval (EDR)** (OGC standard 19-086r6, v1.1). EDR defines a small family of
query types whose semantics line up with the doc-02 verbs:

| doc-02 bespoke verb | EDR query type | CovJSON domain |
| --- | --- | --- |
| `/point` | `position` | Point / PointSeries |
| `/bbox` (full grid) | `cube` (bbox + z/t) or `area` (polygon) | Grid |
| `/transect` | `trajectory` | Trajectory |
| `/transect` + buffer | `corridor` | Trajectory / Grid |
| `/timeseries` | `position` + `datetime`, or `cube` | PointSeries |
| `/info` | collection metadata | metadata-only |

The CovJSON-domain column is the *natural* mapping, grounded in the CovJSON
domain-types specification: Grid requires `x`/`y` axes, Point a single `x`/`y`,
PointSeries adds a `t` axis, and Trajectory a `composite` path axis -- so a 2-D
raster is a Grid, a single location a Point, a location through time a PointSeries,
and a sampled line a Trajectory. EDR itself does not mandate a
query-type-to-domain mapping; the domain follows from the data a query returns.

EDR also standardizes the request vocabulary: `coords` (geometry as Well-Known
Text, e.g., `POINT(lon lat)`, `POLYGON(...)`, `LINESTRING(...)`),
`parameter-name` (variable/band selection), `datetime`, `z`, `bbox`, `crs`, and
`f` (output format). Per OGC API conventions the `f` value is *server-defined* --
the set of formats a server advertises -- not a fixed token; the conventional
value for CovJSON output is `f=CoverageJSON`, though the exact spelling is each
server's choice.

**Two levels of interoperability, which it is important not to conflate.** EDR
buys interop at two distinct levels, and Option B standalone delivers only the
first:

- **Parameter-level** -- the request vocabulary (`coords`, `parameter-name`, `f`,
  `bbox`, `datetime`, `z`). Adopting these spellings is interop a client gets from
  any route that uses them, with no collections plumbing. **Option B delivers this
  standalone.**
- **Path-level** -- EDR query types are resources *under* a collection
  (`/collections/{collectionId}/{queryType}`), so a bare top-level `/cube` or
  `/position` is **not** a path any EDR client discovers. Path-level interop
  requires the Core/Collections plumbing -- **only Option C delivers it**, or
  Option B once it is mounted collection-scoped (Section 7.6).

So aligning to EDR's *vocabulary* is interop we get for free; aligning to EDR's
*discoverable paths* is not free standalone. Inventing `/covjson/*` verbs forgoes
both.

EDR conformance is modular: a server must implement **at least one** query type,
plus the Core/Collections plumbing (a landing page, `/conformance`, and a
`/collections` model with per-collection `data_queries`, `parameter_names`, and
extents). That plumbing is the line between "EDR-flavored" and "EDR-conformant"
-- the key axis separating Options B and C below.

## 4. Baseline (for contrast): the current bespoke design

`/covjson/{point,bbox,tiles,transect,info,timeseries,overview}` with CovJSON-only
responses. Known issues, independent of which option we choose:

- **Duplicates TiTiler's surface** under a new prefix -- two taxonomies, two sets
  of parameter parsing to maintain and document.
- **Phantom content negotiation** -- doc 02 Section 5 describes `Accept`-based
  negotiation defaulting to CovJSON, but every endpoint is CovJSON-only, so there
  is nothing to negotiate.
- **`?bbox=` querystring** diverges from TiTiler's path convention
  (`/bbox/{minx},{miny},{maxx},{maxy}`).
- **`/tiles` -> CovJSON Grid is niche** -- no standard client we know of renders
  XYZ CovJSON tiles; the standard "tiled coverage" mechanism is `TiledNdArray`
  (roadmap Story 12).
- **`format=aggregated` -> Polygon** conflates "extract a grid" with "reduce to a
  statistic" behind one endpoint's `format` flag -- distinct operations with
  different output domains and shapes. (Section 7.4 disentangles these into *three*
  operations: full-resolution extraction, resolution downsampling, and statistical
  reduction.)

We are leaning away from this baseline; it is retained here only so the
comparison has a reference point.

## 5. The three options

> **Note on mechanism.** All three options are delivered by the same mechanism --
> a dedicated `BaseFactory` subclass (Section 2.2), reusing TiTiler's dependency
> injectors (Section 2.1). They differ only in the **endpoint names/vocabulary and
> the conformance commitment**, not in the attach mechanism or the format selector
> (Section 7.1). (The illustrative `/coverage`-suffixed paths shown for Option A
> below are kept only to contrast vocabularies; the actual selector is
> `f=CoverageJSON`/`Accept` -- Section 7.1.)

### Option A -- TiTiler-native output format

CovJSON as a selectable format on TiTiler's **own** endpoint shapes. Our factory
defines CovJSON-emitting routes that mirror the factory query taxonomy (Section
2.1) and wire in the same dependency injectors.

Illustrative surface (extension-owned routes):

```
GET /bbox/{minx},{miny},{maxx},{maxy}/coverage    -> Grid
GET /preview/coverage                             -> Grid (overview)
GET /point/{lon},{lat}/coverage                   -> Point
POST /feature/coverage         (GeoJSON body)     -> Grid (masked)
GET /tiles/{tileMatrixSetId}/{z}/{x}/{y}/coverage -> Grid (optional)
GET /info/coverage                                -> metadata-only
```

(Equivalently, an `f=CoverageJSON` query parameter on a single extension route per
read type. Suffix vs query parameter is a sub-decision -- Section 7.1.)

- **Format selection:** `f=CoverageJSON` or `Accept` (Section 7.1), not the
  illustrative `/coverage` segments above; `application/prs.coverage+json` content
  type.
- **Reuse:** maximal -- wires in every factory dependency injector; almost no new
  parameter code (the routes themselves are still ours to define -- Section 2.2).
- **Pros:** lowest friction; most familiar to existing TiTiler users; smallest
  new concept count; trivially co-located with the image endpoints.
- **Cons:** the endpoint vocabulary is TiTiler's, not a recognized standard, so
  OGC/EDR/CovJSON clients do not auto-discover or auto-consume it; "coverage as a
  format" is a local convention we invent and document ourselves.
- **Effort:** lowest. First slice and full set both cheapest.
- **Growth path:** can later add an EDR facade in front of these without
  rework, but that facade is net-new work if we ever want conformance.

### Option B -- Hybrid: EDR vocabulary + extension mechanism (recommended)

EDR-aligned **request vocabulary** (and, eventually, EDR endpoint names),
delivered through the same dedicated-factory mechanism + dependency-injector reuse
as Option A (Section 2.2), but **without** committing to full EDR
Core/Collections conformance yet.

Illustrative *eventual* surface (the EDR query types, once their dimensionality is
honored):

```
GET /position?coords=POINT(lon lat)&parameter-name=...&datetime=...&f=CoverageJSON -> Point/PointSeries
GET /area?coords=POLYGON((...))&f=CoverageJSON                                     -> Grid
GET /cube?bbox=minx,miny,maxx,maxy&z=...&datetime=...&f=CoverageJSON               -> Grid
GET /trajectory?coords=LINESTRING(...)&f=CoverageJSON                              -> Trajectory
```

The **first slice is not one of these EDR verbs.** EDR has no 2-D bounding-box
query type (`area` is polygon/WKT; `cube` is bbox + z + datetime), and we cannot
yet honor z/t (Section 7.6). So the first slice is a TiTiler-native
`GET /bbox/{minx},{miny},{maxx},{maxy}` -> `Grid`, with EDR-aligned *parameters*
(`parameter-name`, `f`); `/cube` arrives when a z/t backing does. The EDR
*parameter* vocabulary is therefore stable from day one; one *verb* transition
(`/bbox` -> `/cube`) is the deliberate, bounded exception (Section 7.6).

- **Format selection:** `f=CoverageJSON` (EDR convention) or the `Accept:
  application/prs.coverage+json` header; CovJSON is the default for these routes.
- **Reuse:** high -- wires in the factory's reader/layer dependency injectors; the
  main net-new work is parsing EDR `coords` (WKT) into the bounds / point / line
  the readers expect. The first `/bbox` slice avoids even this -- `bbox` is native
  to both TiTiler and EDR, so no WKT parser is needed (Section 7.6).
- **Pros:** recognizable, standards-aligned **parameter** vocabulary;
  interoperable with EDR / CovJSON tooling at the *parameter* level (Section 3);
  the clearest additive path to full conformance (Option C); does not force the
  heavy Collections model on day one.
- **Cons:** more up-front concept mapping than Option A (WKT parsing, EDR param
  names); standalone it delivers EDR *parameter* interop but **not** EDR
  *path-level* discoverability (Section 3) -- so "EDR-flavored but not conformant"
  can mislead clients that probe `/conformance`, and we must be explicit that
  conformance is not yet claimed; the bounded `/bbox` -> `/cube` verb transition
  (Section 7.6) is a one-time exception to "rename-free".
- **Effort:** the first `/bbox` slice is on par with Option A (no WKT); the full
  set is moderate -- slightly above Option A due to WKT/coords handling for the
  `coords`-based query types.
- **Growth path:** strongest -- becomes Option C by adding the
  landing-page/`/conformance`/`/collections` plumbing. The EDR *parameter*
  vocabulary carries over rename-free; the only verb churn is the planned
  `/bbox` -> `/cube` promotion when z/t lands.

### Option C -- Full OGC API-EDR conformance

Commit to EDR as the API contract: landing page, `/conformance`, a
`/collections` model with per-collection `data_queries`, `parameter_names`,
spatial/temporal/vertical extents, `/instances` for time-versioned data, and the
query types as conformant resources, with CovJSON as the primary `f` encoding.

- **Format selection:** the standard OGC API `f`/`Accept` mechanism; plus full
  content negotiation and the collection-description machinery.
- **Reuse:** moderate -- dependency-injector reuse as in B, plus net-new EDR
  plumbing (collections, conformance, metadata). That plumbing is **smaller than
  it looks if the collection identity comes from a STAC backing** (Section 7.6's
  resolver seam): STAC collection IDs serve directly as EDR `collectionId`s, so
  the collections model is not bespoke. This is why the B -> C distance is shorter
  than the matrix's raw "High" suggests.
- **Pros:** the most interoperable and discoverable outcome; a real, certifiable
  OGC API that EDR clients consume with zero custom glue; clearest long-term
  story.
- **Cons:** largest scope and slowest to a first end-to-end slice; the
  Collections/conformance model is a meaningful design effort in its own right;
  arguably over-scoped for a TiTiler *extension* whose first job is simply to
  emit CovJSON; risks blocking the near-term goal (prove the Grid path in a
  container).
- **Effort:** highest.
- **Growth path:** terminal -- this *is* the conformant end state.

## 6. Comparison matrix

Scores are relative (Low / Med / High); "High" is not always "better" -- read
with the column meaning in mind.

| Dimension | A: TiTiler-native | B: Hybrid (rec.) | C: Full EDR |
| --- | --- | --- | --- |
| TiTiler idiomaticity | High | Med-High | Med |
| EDR interop -- **parameter** level (Section 3) | Low | High | High |
| EDR interop -- **path** level / discoverability (Section 3) | None | None standalone (Partial collection-scoped) | Full |
| Reuse of TiTiler dependency injectors | High | High | Med |
| Net-new concepts to build/maintain | Low | Med | High (Med if STAC-backed -- Section 7.6) |
| Effort to first end-to-end slice | Low | Low (the `/bbox` slice needs no WKT) | High |
| Effort to full feature set | Low | Med | High |
| Future-proofing / growth path | Med | High (one planned `/bbox`->`/cube` verb churn) | High (terminal) |
| Risk to the near-term container goal | Low | Low | Med-High |
| Conformance/maintenance burden | Low | Med | High |

## 7. Cross-cutting decisions (apply to any option)

These should be settled regardless of A/B/C; calling them out so they are not
relitigated per-option.

1. **Format-selection mechanism. (Recommendation: `f=CoverageJSON` or `Accept`,
   no path suffix.)** Three candidates:
   - `f=CoverageJSON` query parameter -- the conventional EDR value for CovJSON.
     The `f` value is server-defined, not a mandated token; `CoverageJSON`
     is the conventional spelling, and we can accept it case-insensitively and
     alias `f=covjson`. Keeps one route per query type and is trivial to test.
   - `Accept: application/prs.coverage+json` -- HTTP content negotiation; the most
     "correct" REST mechanism and what OGC/EDR clients send by default.
   - An extension-owned path suffix/segment (e.g., `.../coverage`) -- mirrors
     TiTiler's `.png`/`.tif` feel and is cache-key-friendly, but adds a parallel
     route per query type and re-introduces the surface duplication we are trying
     to avoid.

   **Recommend supporting both selectors -- `f=CoverageJSON` and the `Accept`
   header -- as alternatives (a request uses one, not both), defaulting to CovJSON
   when neither is supplied on a coverage route, and *not* adding a path suffix.**
   Rationale: offering `f=` or `Accept` is exactly the EDR convention (free
   interoperability), keeps one route per query type, and sidesteps the Section
   2.3 constraint that we cannot reuse TiTiler's `.{format}`/`ImageType` suffix
   anyway. This supersedes the illustrative `/coverage` path segments shown for
   Option A in Section 5 -- those were drawn that way only to contrast
   vocabularies; even under Option A, `f=`/`Accept` is the cleaner selector.

   *What the `Accept` half is, how it relates to `f=`, and why it is nearly free
   here.* `f=` and `Accept` are two **alternative** ways to ask for the same thing:
   a given request uses one or the other, **not both at once**. The server resolves
   them by a strict precedence -- **`f` query parameter, else the `Accept` header,
   else the endpoint default**. If `f` is present it decides outright and the
   `Accept` header is *not consulted*; `Accept` is consulted *only* when `f` is
   absent; if neither selects a supported type, the endpoint default (CovJSON, on a
   coverage route) applies. (A client that happens to send both does not get a
   conflict -- `f` simply wins.) Which to reach for:
   - **`f=CoverageJSON` (query parameter)** -- for humans and ad-hoc tools: a URL
     typed in a browser, `curl`, a shared or bookmarked link, debugging, or the
     OpenAPI "try it out" form. It is explicit, URL-visible, gives each format a
     distinct cache key, and needs no control over request headers.
   - **`Accept: application/prs.coverage+json` (header)** -- for programmatic
     clients and libraries (EDR/CovJSON tooling, HTTP client code) that negotiate by
     header and keep the URL canonical. This is the standards-preferred REST
     mechanism and what OGC/EDR clients send by default.

   Crucially, this `f`-else-`Accept` scheme is *already TiTiler's own idiom for its
   non-image endpoints*, so we reuse it rather than build it:
   `titiler.core.utils.accept_media_type(accept, mediatypes)` parses the `Accept`
   header (quality values, `*` wildcard, preference ordering) and returns the best
   match from a server-supplied list, and TiTiler's OGC/JSON endpoints already
   select output with this pattern:

   ```python
   if f:                          # explicit ?f= wins; Accept is not consulted
       output_type = MediaType[f]
   else:                          # only when f is absent, negotiate on Accept
       output_type = (
           accept_media_type(request.headers.get("accept", ""), accepted_media)
           or default_media_type
       )
   ```

   So TiTiler has *two* selection systems: the `.{format}`/`ImageType` path-suffix
   machinery for rendered images (the Section 2.3 wrinkle), and this
   `f`/`Accept`/`MediaType` machinery for data/OGC endpoints -- the one our
   coverage routes belong on. The only net-new bit is registering CovJSON's media
   type (`application/prs.coverage+json`), which is not in TiTiler's `MediaType`
   enum; the negotiation helper and precedence pattern are reused as-is, so format
   selection barely moves the effort needle for any of Options A/B/C.
2. **Content type.** The CovJSON spec mandates `application/prs.coverage+json`
   (its Section 10, which also fixes the file extension `covjson`) and defines
   **no alias** -- in particular, *not* `application/json`. A coverage route *may*
   optionally also honor a generic `application/json` request as a convenience for
   non-CovJSON-aware clients, but that is a local server choice, not a spec
   equivalence. (doc 02's content-negotiation table listed `application/json` as
   CovJSON on exactly this pragmatic basis, not a spec one.)
3. **`/tiles` as CovJSON, and `TiledNdArray`.** Two related but distinct ideas
   that are easy to conflate:
   - *Map-tile-as-CovJSON* (doc 02 Section 3.4): return one self-contained `Grid`
     `Coverage` per XYZ tile, like PNG tiles but with a CovJSON payload.
     **Recommend dropping from v1.** Image-tile clients stitch an XYZ pyramid; the
     CovJSON clients we know of (the covjson.org playground, the `leaflet-coverage`
     plugin, EDR clients) work the other way -- they load *one* `Coverage` and read
     values from it. We are not aware of a standard client that consumes an XYZ
     pyramid of separate `Coverage` documents as a map.
   - *`TiledNdArray`* (a spec range encoding; roadmap Story 12): *one* `Coverage`
     still describes the whole region, but its range is split into chunks the
     client fetches on demand via a `urlTemplate`, instead of inlining every value
     in an `NdArray`. This is the spec-sanctioned way to stream a large coverage,
     and clients understand it natively because it is still one logical
     `Coverage`.

   These compose: a per-tile endpoint is a legitimate *backing store* for a
   `TiledNdArray` `urlTemplate` (Story 12 even says the template points at a tile
   endpoint), but that is a large-coverage optimization, not a day-one feature.
   The minimal slice should emit a bounded, inline-`NdArray` `Grid`; defer both
   the tile endpoint and `TiledNdArray` until payload size is an actual concern.
4. **Three operations, not two -- disentangling "aggregation".** doc 02 Section
   3.2 overloads one `/bbox` endpoint with a `format` flag, and an earlier reading
   of that flag collapsed *three* distinct operations into a false binary. Naming
   them explicitly (a PR reviewer flagged that the intended "aggregation" was being
   mistaken for statistical reduction):
   - **(a) Full-resolution extraction** -- read the raster cells as-is into a
     `Grid`, shape `[H, W]`.
   - **(b) Resolution downsampling** -- read a *reduced-resolution* `Grid`, shape
     `[h, w]` with `h, w < H, W`, for a large extent we cannot or should not pull
     at full resolution (e.g., ground-motion / Persistent Scatterer Interferometry
     (PSI) over a wide bbox with millions of points). This is still a `Grid`
     (array), **not** a scalar -- it is TiTiler's `part(width, height)` / `max_size`
     / overview path, **not** `/statistics`. It is a **first-class** capability, not
     a statistics mode.
   - **(c) Statistical zonal reduction** -- *reduce* the cells to a single
     statistic (mean/median/...) returned as a `Polygon`, shape `[1]`.

   Operations (a) and (b) are the *same* endpoint distinguished only by
   `width`/`height`/`max_size` parameters (rio-tiler resamples on read); operation
   (c) is a *separate* endpoint (its output domain and shape differ). This also
   **subsumes doc 02's separate `/overview` endpoint** (Section 3.7): a reduced
   grid is just the bbox grid with small `width`/`height` (or a `/preview` route
   for the full-extent case), so `/overview` is not a distinct verb. Both TiTiler
   (`/bbox` vs `/statistics`) and EDR (an `area`/`cube` query vs a separate
   aggregation concern) keep extraction and statistical reduction apart; we do too.

   *Vector point-cloud reduction is a different pipeline.* For PSI and similar
   vector point clouds, reducing to a grid is **spatial binning**, not rio-tiler
   raster resampling -- a separate pipeline, out of scope for the raster `Grid`
   path here, and most naturally **our own EDR extension** ("tile-as-coverage
   aggregation") rather than core EDR. We name it so it is not silently assumed to
   fall out of (b).

   Note: operation (c) is **no longer an upstream blocker.** `Polygon` and
   `PolygonSeries` landed in covjson-pydantic 0.8.0 (now the project's pinned floor
   and installed version), so the statistical path is buildable today; it should
   simply be a *separate* endpoint rather than a `format` mode on Grid extraction.
   (`Section`, `MultiPolygon`, and `MultiPolygonSeries` remain absent from the
   upstream `DomainType` enum, but none are needed here.)
5. **CRS output.** Continue emitting OGC CRS URIs -- `helpers.py`
   (`crs_to_ogc_uri` / `create_spatial_2d_reference`) already produces them for any
   input CRS and imposes no default of its own. Defaulting the API layer to WGS84 /
   CRS84 for unprojected sources is a reasonable convention to adopt at the
   endpoint, not something `helpers.py` does today.
6. **Request vocabulary vs reader reuse -- resolving an apparent tension in
   Option B. (Recommendation: EDR vocabulary on the outside, TiTiler reader
   dependencies on the inside.)** Option A's selling point is reusing TiTiler's
   request parameters *verbatim*; Option B's is an EDR-aligned vocabulary
   (`coords`, `parameter-name`, `f`). These are reconcilable, and conflating them
   is the main thing that makes Option B look more expensive than it is. Keep
   TiTiler's reader/layer dependency injectors doing the actual work (band/index
   selection, `expression`, `nodata`, `rescale`, resampling, CRS) and add a thin
   translation from EDR geometry/format parameters into the inputs those
   dependencies already expect. Several consequences worth making explicit:
   - **Geometry input differs per EDR query type.** `cube` takes a `bbox`;
     `position`/`area`/`trajectory` take Well-Known Text `coords`. Because `bbox`
     is native to *both* TiTiler and EDR, the first `/bbox` slice needs **no WKT
     parser at all** -- that cost is deferred until the `coords`-based query types
     arrive. This is a concrete reason the bbox grid is the cheapest first slice.
   - **Adopt `parameter-name` as an alias now, not later.** A reviewer's catch:
     deferring `parameter-name` undercuts the "stable vocabulary" claim, because a
     later conformance step would then introduce a *new* public selector -- exactly
     the churn Option B exists to avoid. It is cheap (a thin alias over the existing
     band-selection dependency), so we adopt it **from day one**: `parameter-name`
     aliases TiTiler's **`band_names`** (name-based selection), with `bands`/`bidx`
     retained for index selection and `expression` for band math. The EDR
     *parameter* vocabulary is thus stable immediately, independent of the verb.
   - **The `/cube` naming trap, and the chosen exit.** EDR `cube` is semantically
     bbox + z + datetime (a 3-D/4-D hypercube), but the implemented `GridInput` is
     `(bands, height, width)` -- 2-D + bands, no z/t axis. Shipping `/cube` for a
     2-D slice would mislead EDR-aware clients that reasonably expect `z`/`datetime`
     to do something. **Decision: name the first slice for what it returns** -- a
     TiTiler-native 2-D `GET /bbox/{minx},{miny},{maxx},{maxy}` -> `Grid` -- and
     **promote to `/cube` only when a real z/t axis backs it.** (The alternative,
     accepting `z`/`datetime` as documented no-ops under `/cube`, was rejected as
     dishonest for as long as the dimensionality is unhonored.)
   - **Resolver seam -- where the z/t axis will come from.** The factory turns a
     request into a `CoverageInput` through a single **resolver seam**. Today that
     resolver is single-dataset (`url=` / `DatasetParams`), 2-D, no `t`. The seam is
     deliberately backing-agnostic so a *temporal* backing can be added later
     without reworking the endpoints: the recommended, STAC-first option is
     **titiler-stacapi** (a collection resolves to STAC items carrying `datetime`,
     giving `/cube` z/t and PointSeries a real source, and making STAC collection
     IDs double as EDR `collectionId`s -- Section 3, path-level interop). It is
     **named as one option, not a dependency we take now**; a time-dimensioned
     NetCDF/Zarr (via xarray) is another. The first slice takes **no new
     dependency**: it runs against a single COG via `url=`. The `url=`
     single-dataset path is **permanent**, not transitional: when a collection
     backing is later added it sits *alongside* `url=` as an additional resolver,
     so a plain single-dataset request keeps working with no STAC API required --
     the escape hatch a reviewer asked us to preserve.

7. **Mount prefix is a factory-level setting, not a baked-in path.** A reviewer's
   ask: for a pure-EDR deployment you want the surface unprefixed (or at `/edr`);
   inside a standard TiTiler deploy you want a prefix (`/coverage`, `/edr`, ...) so
   it sits alongside the image endpoints. `BaseFactory` already exposes
   `router_prefix` (Section 2.2), so we expose the mount prefix as a setting on our
   factory (with a sensible default) rather than hard-coding a path.
8. **Dependency floor: `titiler.core` must be bumped first.** Our factory is the
   *first* real `titiler.core` import in the codebase, so its version pin stops
   being theoretical the moment the slice is built. `BaseFactory` (the name) needs
   **>= 0.19.0** and `conforms_to` needs **>= 0.22.0**; the current floor is
   `>= 0.18.0`. We resolve this via issue #27 (raise to `titiler.core` 2.x +
   Python 3.11) **before** the slice, consistent with #27's own rationale of
   writing new endpoints against the newest API rather than re-migrating shortly
   after. `conforms_to` is then the concrete hook for advertising EDR conformance
   classes on the B -> C path -- even Option B can populate the classes it does
   meet.

## 8. Recommendation

**Adopt Option B (Hybrid): EDR-aligned request vocabulary and
`f=CoverageJSON`/`Accept` selection, delivered as a dedicated `BaseFactory`
subclass (Section 2.2) that reuses TiTiler's dependency injectors.** Rationale:

- It captures the interoperability of EDR's recognized **parameter** vocabulary
  (Section 3) without paying the full Collections/conformance cost up front.
- It has the cleanest growth path to full conformance (Option C): the EDR
  *parameter* vocabulary carries over rename-free, and the only verb churn is the
  single planned `/bbox` -> `/cube` promotion when a z/t axis lands (Section 7.6).
  So we are not betting against the standard.
- It does **not** enlarge the near-term goal: the first vertical slice is a
  single endpoint either way, and under B that endpoint is the honest 2-D
  `/bbox/{minx},{miny},{maxx},{maxy}` -> Grid (Section 7.6), reusing the existing
  `GridInput` + modeler path unchanged. We throw nothing away because the
  *parameter* vocabulary and selection mechanism are already "right"; only the
  verb is deliberately deferred from `/cube`.

### 8.1 Decision guide -- keyed to the conformance question

The one decision only the designer can make is whether **EDR conformance is a
project goal** (Section 9, question 1). That single answer selects the path:

| Answer to "is EDR conformance a goal?" | Recommended option | First Grid endpoint | What the answer changes |
| --- | --- | --- | --- |
| **Yes** -- a committed goal | **B now, on a planned road to C** | `/bbox/{minx},{miny},{maxx},{maxy}` (2-D; `/cube` when z/t lands -- Section 7.6) | Build the EDR-*parameter* slice first, then add the Collections / `/conformance` plumbing as committed follow-on work (shorter if STAC-backed -- Section 7.6), and start sketching collection metadata sooner. Do *not* build all of C before the first slice. |
| **Maybe** -- open but uncommitted *(this is the answer given -- Q1)* | **B** (the default recommendation) | `/bbox/{minx},{miny},{maxx},{maxy}` (2-D; `/cube` later) | Nothing extra now. B keeps C reachable later with only the one planned `/bbox` -> `/cube` verb promotion, so the decision is deferrable at near-zero cost. |
| **No** -- an explicit non-goal | **A** (TiTiler-native) | `/bbox/{minx},{miny},{maxx},{maxy}` via `?f=CoverageJSON` | Drop the EDR *parameter* vocabulary too (its interop / conformance payoff is moot) and stay fully TiTiler-native, with the `f=CoverageJSON` / `Accept` selection from Section 7.1 (no `/coverage` path suffix). |

In **every** branch the model-layer work is identical -- `Reader.part()` ->
`GridInput` -> `to_coverage` -> a bounded inline-`NdArray` `Grid`, and the first
*route* is the same honest 2-D `/bbox` grid. Only the **parameter spelling** and
the eventual conformance plumbing differ by answer. So Q1 blocks only the
parameter vocabulary and the C-road commitment, not the slice's implementation:
deciding now costs nothing in slice scope.

## 9. Open questions for the designer

Each question notes a recommendation where there is a clear pull; the one genuine
product/strategy call was question 1, now answered.

1. **Should eventual EDR conformance be an explicit project goal?** **Answered:
   "maybe"** -- open but explicitly *not* a "no" (anchoring on the OGC standard
   makes the implementation more robust even if we never certify). This confirms
   **Option B** with **Option C** kept reachable (Section 8.1).
2. **First Grid endpoint = `/bbox` (2-D), not `/cube`?** **Recommend yes** -- and
   this is the chosen direction (Section 7.6). EDR has no 2-D bbox verb (`area` is
   polygon/WKT, `cube` is bbox + z + datetime), and we cannot yet honor z/t, so the
   first slice is a TiTiler-native `GET /bbox/{minx},{miny},{maxx},{maxy}` ->
   `Grid` mapping onto `Reader.part()` -> `GridInput` (no WKT parser). `/cube`
   arrives when a z/t backing does (the resolver seam -- Section 7.6); `/area`
   (polygon via `coords`) is the more general EDR primitive and can follow via
   `Reader.feature()`.
3. **Format selection = `f=CoverageJSON` or `Accept`, no path suffix?** *Recommend
   yes* (Section 7.1): it is the EDR convention, keeps one route per query type,
   and avoids the Section 2.3 `.{format}` constraint. A path segment buys only
   cache-key friendliness, at the cost of re-introducing route duplication.
4. **Drop `/tiles`-as-CovJSON from v1?** *Recommend yes* (Section 7.3): no
   standard client we know of consumes an XYZ pyramid of `Coverage` documents;
   large coverages are served by `TiledNdArray`, a later optimization rather than
   a day-one feature.
5. **Replace the bespoke verbs, or keep them as aliases?** *Recommend replacing
   them outright* and rewriting [02-api-definition.md](02-api-definition.md) to
   the chosen vocabulary. There is currently **no HTTP layer and no consumers**
   (the routes are unimplemented stubs), so transition aliases would be pure
   maintenance cost for zero migration benefit.

## 10. Sources

- TiTiler endpoint surface; the `BaseFactory` / `FactoryExtension` classes (and
  `BaseFactory`'s `router_prefix`, `extensions`, and `conforms_to` attributes); and
  the `f`-else-`Accept` content-negotiation idiom
  (`titiler.core.utils.accept_media_type` plus the `MediaType` selection pattern in
  `titiler.core.factory`): introspected from the installed `titiler.core` 0.24.2 in
  this project's environment.
- [TiTiler `CHANGES.md`](https://github.com/developmentseed/titiler/blob/main/CHANGES.md)
  -- the `BaseTilerFactory` -> `BaseFactory` rename (0.19.0, 2024-11-07) and the
  `conforms_to` attribute (0.22.0, 2025-05-06), which set the dependency floor in
  Section 7.8.
- [TiTiler Extensions documentation](https://developmentseed.org/titiler/advanced/Extensions/)
  -- first-party extension class names (`cogValidateExtension`, `wmsExtension`, ...).
- [titiler-stacapi](https://developmentseed.org/titiler-stacapi/) -- the dedicated
  `BaseFactory` subclass pattern (`OGCEndpointsFactory(BaseFactory)`) and the
  collection-scoped, STAC-API-backed model cited in Sections 2.2 and 7.6 (the
  resolver seam's recommended temporal backing). Class structure and
  `collection_id` / item-search dependencies introspected from commit
  `14cb034`.
- titiler-covjson issue #27 (raise Python floor to 3.11; upgrade to
  `titiler.core` 2.x / `rio-tiler` 9.x) -- the dependency-floor prerequisite in
  Section 7.8.
- [OGC API - Environmental Data Retrieval Standard (19-086r6, v1.1)](https://docs.ogc.org/is/19-086r6/19-086r6.html)
  -- query parameters (`coords`, `parameter-name`, `datetime`, `z`, `bbox`, `crs`,
  `f`), the "at least one query type" rule, and the landing-page / `/conformance` /
  `/collections` requirements.
- [OGC API - EDR overview](https://ogcapi.ogc.org/edr/)
- [pygeoapi -- Publishing to OGC API - EDR](https://docs.pygeoapi.io/en/stable/publishing/ogcapi-edr.html)
  -- example EDR queries and the server-defined `f` format parameter for content
  negotiation.
- [CoverageJSON specification](https://github.com/opengeospatial/CoverageJSON)
  -- Section 10 ("Media Type and File Extension") mandates the
  `application/prs.coverage+json` media type and `covjson` file extension, and
  defines no `application/json` alias; its companion domain-types specification
  defines the per-domain required axes (Grid, Point, PointSeries, Trajectory,
  Polygon) and the `TiledNdArray` range encoding.
- [`leaflet-coverage`](https://github.com/Reading-eScience-Centre/leaflet-coverage)
  and [`covjson-reader`](https://github.com/Reading-eScience-Centre/covjson-reader)
  (Reading eScience Centre) -- the CovJSON viewer plugin and reader referenced in
  Section 7.3.
