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
> **Bottom line:** the recommendation is **Option B** (a hybrid EDR-vocabulary
> TiTiler extension) with a first **`/cube` Grid** slice. The only answer that
> changes that is an explicit *"no"* to conformance (see Section 8.1), which points
> to Option A.

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
to build **first** is one **Grid** endpoint (the recommendation lands on `/cube`;
see Sections 8 and 8.1), chosen because the Grid model layer is already
implemented and tested -- so the only thing between us and a working container is
the HTTP shape this document exists to settle. Picking that shape well, before any
code, is the whole motivation for this comparison.

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
reusable dependency injectors -- band/index selection, `expression`, `nodata`,
`rescale`, `resampling`, `colormap` (not all meaningful for CovJSON), CRS, and
the parameterized `tileMatrixSetId` (TMS) -- so reusing the factory avoids
re-implementing parameter parsing.

### 2.2 `FactoryExtension` is the idiomatic attach point

```python
@define
class FactoryExtension(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def register(self, factory: "BaseFactory"): ...
```

An extension's `register()` adds routes onto an existing factory's router, so the
new routes inherit that factory's dependencies. This is the same mechanism
TiTiler's own first-party extensions use -- e.g., `cogValidateExtension`,
`cogViewerExtension`, `stacViewerExtension`, `wmsExtension`, and `wmtsExtension`
in the `titiler.extensions` package (see Sources). All three options below are
delivered as a `FactoryExtension`;
they differ only in the **endpoint names/vocabulary and the conformance
commitment**, not in the attach mechanism or the format selector (which is shared
-- Section 7.1).

### 2.3 One practical wrinkle: `.{format}` is not free real estate

TiTiler's image endpoints select output via a `.{format}` path suffix bound to
the `ImageType` enum and its render pipeline (`png`, `tif`, `jpeg`, `npy`, ...).
**CovJSON is not an `ImageType`** and is not produced by the render pipeline, so
it cannot ride that suffix machinery directly. Whichever option we pick, the
extension therefore registers its **own** routes and selects the format itself
rather than through the image `.{format}` suffix -- the selection mechanism is
settled in Section 7.1 (`f=CoverageJSON` or `Accept`). This is a small but real
constraint that rules out "just add `.covjson` to the existing image route for
free."

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
server's choice. EDR clients already know how to request CovJSON from these query
types -- aligning to them is interoperability we get for free; inventing
`/covjson/*` verbs forgoes it.

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
  statistic" behind one endpoint's `format` flag -- two different operations with
  different output domains and shapes (detailed in Section 7.4).

We are leaning away from this baseline; it is retained here only so the
comparison has a reference point.

## 5. The three options

### Option A -- TiTiler-native output format

CovJSON as a selectable format on TiTiler's **own** endpoint shapes. The
extension adds CovJSON-emitting siblings to the factory routes, reusing the
factory's dependencies verbatim.

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
- **Reuse:** maximal -- inherits every factory dependency; almost no new
  parameter code.
- **Pros:** lowest friction; most familiar to existing TiTiler users; smallest
  new concept count; trivially co-located with the image endpoints.
- **Cons:** the endpoint vocabulary is TiTiler's, not a recognized standard, so
  OGC/EDR/CovJSON clients do not auto-discover or auto-consume it; "coverage as a
  format" is a local convention we invent and document ourselves.
- **Effort:** lowest. First slice and full set both cheapest.
- **Growth path:** can later add an EDR facade in front of these without
  rework, but that facade is net-new work if we ever want conformance.

### Option B -- Hybrid: EDR vocabulary + extension mechanism (recommended)

EDR-aligned **endpoint names and request vocabulary**, delivered through the same
`FactoryExtension` + factory-dependency reuse as Option A, but **without**
committing to full EDR Core/Collections conformance yet.

Illustrative surface:

```
GET /position?coords=POINT(lon lat)&parameter-name=...&datetime=...&f=CoverageJSON -> Point/PointSeries
GET /area?coords=POLYGON((...))&f=CoverageJSON                                     -> Grid
GET /cube?bbox=minx,miny,maxx,maxy&z=...&datetime=...&f=CoverageJSON               -> Grid
GET /trajectory?coords=LINESTRING(...)&f=CoverageJSON                              -> Trajectory
```

- **Format selection:** `f=CoverageJSON` (EDR convention) or the `Accept:
  application/prs.coverage+json` header; CovJSON is the default for these routes.
- **Reuse:** high -- still reuses the factory's reader/layer dependencies under
  the hood; the main net-new work is parsing EDR `coords` (WKT) into the bounds /
  point / line the readers expect. (The recommended `/cube` slice avoids even
  this -- it takes a `bbox`, not WKT; see Section 7.6.)
- **Pros:** recognizable, standards-aligned vocabulary; interoperable with EDR /
  CovJSON tooling at the query-shape level; future-proof -- a clean, additive
  path to full conformance (Option C) without renaming endpoints; does not force
  the heavy Collections model on day one.
- **Cons:** more up-front concept mapping than Option A (WKT parsing, EDR param
  names); "EDR-flavored but not conformant" can mislead clients that probe
  `/conformance` -- we must be explicit that conformance is not yet claimed.
- **Effort:** the first `/cube` slice is on par with Option A (no WKT); the full
  set is moderate -- slightly above Option A due to WKT/coords handling for the
  `coords`-based query types.
- **Growth path:** strongest -- becomes Option C by adding the
  landing-page/`/conformance`/`/collections` plumbing; no endpoint churn.

### Option C -- Full OGC API-EDR conformance

Commit to EDR as the API contract: landing page, `/conformance`, a
`/collections` model with per-collection `data_queries`, `parameter_names`,
spatial/temporal/vertical extents, `/instances` for time-versioned data, and the
query types as conformant resources, with CovJSON as the primary `f` encoding.

- **Format selection:** the standard OGC API `f`/`Accept` mechanism; plus full
  content negotiation and the collection-description machinery.
- **Reuse:** moderate -- reader reuse as in B, but a substantial amount of
  net-new EDR plumbing (collections, conformance, metadata) that has no TiTiler
  equivalent to inherit.
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
| OGC/CovJSON-client interop | Low | Med-High | High |
| Standards discoverability (`/conformance`) | None | Partial (flavored) | Full |
| Reuse of TiTiler deps | High | High | Med |
| Net-new concepts to build/maintain | Low | Med | High |
| Effort to first end-to-end slice | Low | Low (the `/cube` slice needs no WKT) | High |
| Effort to full feature set | Low | Med | High |
| Future-proofing / growth path | Med | High | High (terminal) |
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
4. **Aggregation vs extraction (the doc-02 `format=aggregated` conflation).**
   doc 02 Section 3.2 overloads one `/bbox` endpoint with a `format` flag that
   switches between two unrelated operations: `format=full` *extracts* the raster
   cells (a `Grid`, shape `[H, W]`), while `format=aggregated` *reduces* them to a
   single statistic (mean/median/...) returned as a `Polygon` (shape `[1]`). They
   differ in operation (extraction vs reduction), output domain (`Grid` vs
   `Polygon`), shape (array vs scalar), and which parameters apply -- so bundling
   them behind one `format` flag forces a caller to read `format` to know whether
   they get an array or a scalar. Both TiTiler (`/bbox` vs `/statistics`) and EDR
   (an `area`/`cube` query vs a separate aggregation concern) keep these apart; we
   should too: make aggregation its own endpoint, and do not let it gate the Grid
   slice.

   Note: this is **no longer an upstream blocker.** `Polygon` and `PolygonSeries`
   landed in covjson-pydantic 0.8.0 (now the project's pinned floor and the
   installed version), so the aggregated path is buildable today; it should simply
   be a *separate* endpoint rather than a `format` mode on Grid extraction.
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
   dependencies already expect. Two consequences worth making explicit:
   - **Geometry input differs per EDR query type.** `cube` takes a `bbox`;
     `position`/`area`/`trajectory` take Well-Known Text `coords`. Because `bbox`
     is native to *both* TiTiler and EDR, the recommended `/cube` slice needs **no
     WKT parser at all** -- that cost is deferred until the `coords`-based query
     types arrive. This is a concrete reason `/cube` is the cheapest first slice.
   - **Band selection can stay TiTiler-native for now.** Adopting EDR's
     `parameter-name` as the selector (vs TiTiler's `bands`/`bidx`/`expression`)
     is not required for the slice; keep the TiTiler parameters and revisit
     `parameter-name` only if/when full EDR conformance (Option C) is pursued.

## 8. Recommendation

**Adopt Option B (Hybrid): EDR-aligned vocabulary and `f=CoverageJSON`/`Accept`
selection, delivered as a `FactoryExtension` that reuses TiTiler's reader
dependencies.** Rationale:

- It captures the interoperability of EDR's recognized query vocabulary without
  paying the full Collections/conformance cost up front.
- It is the only option with a **rename-free** growth path to full conformance
  (Option C), so we are not betting against the standard.
- It does **not** enlarge the near-term goal: the first vertical slice is a
  single endpoint either way, and under B that endpoint is `/cube` -> Grid
  (recommended; see open question 2), reusing the existing `GridInput` + modeler
  path unchanged. We throw nothing away because the name and selection mechanism
  are already "right."

### 8.1 Decision guide -- keyed to the conformance question

The one decision only the designer can make is whether **EDR conformance is a
project goal** (Section 9, question 1). That single answer selects the path:

| Answer to "is EDR conformance a goal?" | Recommended option | First Grid endpoint | What the answer changes |
| --- | --- | --- | --- |
| **Yes** -- a committed goal | **B now, on a planned road to C** | `/cube` | Build the EDR-vocabulary slice first, then add the Collections / `/conformance` plumbing as committed follow-on work, and start sketching collection metadata sooner. Do *not* build all of C before the first slice. |
| **Maybe** -- open but uncommitted | **B** (the default recommendation) | `/cube` | Nothing extra now. B keeps C reachable later without renaming endpoints, so the decision is deferrable at no cost. |
| **No** -- an explicit non-goal | **A** (TiTiler-native) | `/bbox/{minx},{miny},{maxx},{maxy}` via `?f=CoverageJSON` | Drop the EDR vocabulary (its interop / conformance payoff is moot) and use TiTiler-native endpoint names, with the `f=CoverageJSON` / `Accept` selection from Section 7.1 (no `/coverage` path suffix). |

In **every** branch the model-layer work is identical -- `Reader.part()` ->
`GridInput` -> `to_coverage` -> a bounded inline-`NdArray` `Grid`. Only the route
name and request vocabulary differ. So this answer blocks only the endpoint's
*name and parameter spelling*, not the slice's implementation: deciding now avoids
a later rename and costs nothing in slice scope.

## 9. Open questions for the designer

Each question notes a recommendation where there is a clear pull; the one
genuine product/strategy call is question 1.

1. **Should eventual EDR conformance be an explicit project goal?** This is the
   real decision, and it selects the path -- see the decision guide in Section 8.1
   for the recommended option per answer (yes / maybe / no). *Lean:* name
   conformance a **stated future goal** (the "yes" or "maybe" branch), which keeps
   Option B optimal; only an explicit "no" tips the choice to Option A.
2. **First Grid endpoint = `/cube`?** *Recommend yes.* `/cube` (bbox + optional
   z/t) maps directly onto the existing bbox `Reader.part()` -> `GridInput` path
   and parses a plain `bbox` rather than Well-Known Text geometry (Section 7.6).
   `/area` (polygon via `coords`) is the more general EDR primitive and can follow
   via `Reader.feature()`.
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

- TiTiler endpoint surface, the `FactoryExtension` base class, and the
  `f`-else-`Accept` content-negotiation idiom (`titiler.core.utils.accept_media_type`
  plus the `MediaType` selection pattern in `titiler.core.factory`): introspected
  from the installed `titiler.core` 0.24.2 in this project's environment.
- [TiTiler Extensions documentation](https://developmentseed.org/titiler/advanced/Extensions/)
  -- first-party extension class names (`cogValidateExtension`, `wmsExtension`, ...).
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
