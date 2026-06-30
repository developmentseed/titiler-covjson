# CoverageJSON `/bbox` Endpoint Specification (2-D Grid slice)

## 1. Purpose and scope

This document specifies the first end-to-end endpoint of `titiler-covjson`:
an honest two-dimensional `GET /bbox/{minx},{miny},{maxx},{maxy}` that returns
a CoverageJSON (CovJSON, OGC Community Standard 21-069r2) **Grid**-domain
coverage. It realizes the direction set in
[ADR-0001](adr/0001-covjson-http-api-direction.md) (Option B: an OGC API -
Environmental Data Retrieval (EDR) aligned request vocabulary delivered as
CoverageJSON, with full EDR conformance kept reachable) and it gates the
implementation issue (#33).

The slice is deliberately small. It reads a single dataset through rio-tiler,
extracts a bounded rectangular region, and serializes it as a Grid coverage
with an inline `NdArray` range. It is "honest" in the ADR's sense: it is named
for the two-dimensional thing it returns, rather than borrowing EDR's `/cube`
verb for a hypercube whose extra axes are not yet backed by data.

What this slice does **not** cover (see ADR-0001):

- The EDR `/cube` verb (deferred until a real `z`/`t` axis backs it).
- Polygon / zonal statistical aggregation.
- The multi-dataset / collection resolver seam (single dataset via `url=`
  only).
- A separate `/overview` endpoint: reduced-resolution Grid is first-class
  here via output sizing (Section 5), which subsumes it.

## 2. Endpoint and mechanism

The surface is delivered as a dedicated `titiler.core` **`BaseFactory`
subclass** that owns its routes, reusing TiTiler at the **dependency-injector**
level (not by inheriting `TilerFactory` routes). This follows the
titiler-stacapi `OGCEndpointsFactory(BaseFactory)` pattern named in ADR-0001.

A single route is registered:

```text
GET {router_prefix}/bbox/{minx},{miny},{maxx},{maxy}
```

- The four bounds are floats in one comma-delimited path segment (the EDR /
  TiTiler `part` convention), interpreted in the request Coordinate Reference
  System (CRS). See Section 4 for the CRS model.
- The mount prefix is a factory-level setting via `BaseFactory.router_prefix`,
  not a hard-coded path.
- This slice makes **no EDR conformance claim**: it delivers the EDR
  *parameter* vocabulary, not path-level discoverability. `conforms_to` is left
  empty until a collection-scoped surface exists. We must not advertise
  `/conformance`-level conformance until it is real.

## 3. Request parameters

All parameters are reused from `titiler.core` dependency-injectors except the
EDR `parameter-name` alias, which is layered on top of band selection.

| Parameter | Source | Default | Description |
| --- | --- | --- | --- |
| `url` | `DatasetPathParams` | required | Dataset URL (single dataset). |
| `crs` | `CRSParams` | CRS84 | Single CRS knob: interprets the path bbox coordinates **and** the output coverage CRS (Section 4). |
| `parameter-name` | EDR alias -> `BidxExprParams` | none | Band selection by name, comma-delimited (Section 6). Mutually exclusive with `bidx` and `expression`. |
| `bidx` | `BidxExprParams` | all bands | Band selection by 1-based index. Mutually exclusive with `parameter-name` and `expression`. |
| `expression` | `BidxExprParams` | none | rio-tiler band-math expression producing derived bands. Mutually exclusive with `parameter-name` and `bidx`. |
| `nodata` | `DatasetParams` | dataset value | Override the dataset nodata value; masked pixels serialize as `null`. |
| `unscale` | `DatasetParams` | `false` | Apply the dataset's internal scale/offset to recover true physical values. |
| `resampling` | `DatasetParams` | `nearest` | RasterIO resampling algorithm for the read. |
| `reproject` | `DatasetParams` | `nearest` | Warp resampling algorithm, used only when reprojection occurs. |
| `max_size` | `PartFeatureParams` | `1024` | Longest output dimension when `width`/`height` are absent (Section 5). |
| `width`, `height` | `PartFeatureParams` | none | Force exact output grid dimensions; when set, `max_size` does not apply. |
| `f` | format selector | `CoverageJSON` | Output format selection (Section 7). |

`rescale` is intentionally **not** offered (Section 8).

## 4. CRS model: a single `crs` knob

TiTiler exposes two independent CRS knobs (`coord-crs`, which interprets the
input coordinates, and `dst-crs`, which reprojects the output), so a caller can
describe a region in one CRS and receive a grid in another. EDR deliberately
collapses these into a single `crs`: the X and Y coordinates of a query are
values in the CRS named by `crs`, and the returned data comes back in that same
`crs`. When `crs` is absent, EDR assumes CRS84 (WGS84 longitude/latitude).

This slice adopts EDR's single-knob model:

- `crs` interprets the path bbox coordinates **and** is the CRS the returned
  Grid coverage is expressed in. Internally it feeds both rio-tiler's
  `bounds_crs` (input) and `dst_crs` (output).
- The default is **CRS84**. This is both what TiTiler's own `part` endpoint
  already defaults to (rio-tiler's `Reader.part` sets `dst_crs = bounds_crs`,
  and TiTiler defaults `bounds_crs` to WGS84) and what CovJSON consumers
  overwhelmingly expect (geographic longitude/latitude). TiTiler's WGS84
  default is the `epsg:4326` CRS, so the emitted referencing URI is
  `http://www.opengis.net/def/crs/EPSG/0/4326` (as in the Section 9 example),
  not the distinct CRS84 URI.

The output CRS is reflected in three places in the response (Section 9): the
`domain.referencing` system identifier (an OGC CRS Uniform Resource Identifier
(URI); geographic CRSs render as `GeographicCRS`, projected CRSs as
`ProjectedCRS`), the numeric values on `domain.axes.x` / `domain.axes.y` (in the
output CRS's units, e.g., degrees or meters), and the `Content-Crs` response
header.

**Deferred refinement.** Independent input-versus-output CRS (TiTiler's
`coord-crs` / `dst-crs` pair) is a power-user combination with no demonstrated
need for this slice, and EDR does not offer it. Optional `coord-crs` / `dst-crs`
overrides of `crs` may be added later if a real need appears; they are out of
scope here (You Aren't Gonna Need It).

## 5. Output sizing and limits

The Grid range is a **bounded inline `NdArray`**: every cell value is emitted
inline in one JSON array, so output size is a first-class concern.

- When the caller supplies no `width`, `height`, or `max_size`, a default
  `max_size` of **1024** (longest output dimension, preserving the bounds
  aspect ratio) is applied, so a full-extent read does not emit an unbounded
  JSON document. This matches the 1024 default TiTiler uses for previews.
- `width` / `height` force exact output dimensions; when either is set,
  `max_size` does not apply (the `PartFeatureParams` rule).
- A factory-configurable **hard ceiling** bounds the resulting grid cell count
  (`width * height`). A request whose resolved output grid would exceed the
  ceiling is rejected with `400` and a message naming the limit. The ceiling is
  enforced after the `width`/`height`/`max_size` resolution above, so it guards
  explicit oversizing as well as large native reads.

Reduced-resolution Grid output is thus first-class: a caller downsamples simply
by constraining `max_size` (or `width`/`height`).

## 6. Band selection

A raster has one or more bands; rio-tiler numbers them 1-based and names them
`b1`, `b2`, .... Each selected band becomes one CovJSON `Parameter` (keyed by
band name) and a matching `NdArray` range.

Three selectors are accepted, but they fall into two distinct operations:

- **Subset existing bands** (two spellings of the same operation):
  - `bidx` selects by 1-based index (TiTiler-native), e.g., `bidx=1&bidx=3`.
  - `parameter-name` selects by name (the EDR vocabulary), comma-delimited,
    e.g., `parameter-name=b1,b3`. The names are the rio-tiler band identifiers
    (`b1`, `b3`, ...), which are also the CovJSON parameter keys; they resolve
    to indices and feed the same underlying selection. Band descriptions, when
    present, populate a parameter's label but are not selection keys.
- **Compute new bands** (a different operation):
  - `expression` is a rio-tiler band-math expression (e.g., `expression=b4/b3`,
    or semicolon-delimited for multiple derived bands). The output bands are
    *derived*, not the originals; each is named for its expression and carries
    no source unit.

Because `parameter-name` and `bidx` are two spellings of one operation (and
both feed rio-tiler's single `indexes` argument), and `expression` is its own
lane, the three are **mutually exclusive**: supplying more than one is a `400`
(see Section 10) with a message directing the caller to supply only one.

Band metadata (descriptions, units) is carried from the reader's `info()` and
**aligned to the selected bands**: it must be subset to match the bands the
read actually returns, so its count matches the data's band axis. Bands derived
by `expression` have no source metadata.

## 7. Format selection and response

- Format is chosen by `f` first, then the `Accept` header (TiTiler's
  `f`-else-`Accept` idiom); there is **no** path suffix.
- `f=CoverageJSON` is the supported value for this slice; an unsupported `f`
  value is a `400`. When `f` is absent, the `Accept` header is consulted, and
  the default and fallback is CoverageJSON (this endpoint is CovJSON-native and
  produces nothing else).
- The success response uses media type
  **`application/prs.coverage+json`** (the registered CovJSON MIME type), set by
  a dedicated response class.
- A `Content-Crs` header carries the output CRS as an OGC CRS URI, e.g.,
  `Content-Crs: <http://www.opengis.net/def/crs/EPSG/0/4326>`.

## 8. On `rescale` (intentional omission)

TiTiler's `rescale` linearly maps data values into the 0-255 display range; it
is a *rendering* transform tied to image output and is not a core raster-read
dependency. This endpoint returns **data values** in a data format, not a
rendered image, so applying `rescale` would corrupt the physical values and
invalidate the Unified Code for Units of Measure (UCUM) units attached to each
parameter. `rescale` is therefore omitted from this slice. The data-correct
counterpart, `unscale` (apply the dataset's internal scale/offset to recover
true physical values), is supported via `DatasetParams`.

## 9. Response body

The body is a CovJSON Grid `Coverage`, built by the existing model layer
(`imagedata_to_coverage_input` -> `to_coverage`) and serialized with
`model_dump_json(exclude_none=True)`. Its shape:

- `domain.domainType` is `Grid`.
- `domain.axes.x` runs west -> east and `domain.axes.y` runs north -> south
  (raster row 0 is the north edge), each a `CompactAxis` of cell **centers**
  (`start`, `stop`, `num`), inset half a cell from the bounds edges.
- `domain.referencing` carries the output CRS (Section 4).
- `parameters` holds one entry per selected band, keyed by band name, with its
  observed-property label and, when known, its UCUM unit.
- `ranges` holds one `NdArray` per band, `axisNames` `["y", "x"]`, shaped
  `[height, width]`. Masked (nodata) pixels serialize as `null` elements.

```jsonc
{
  "type": "Coverage",
  "domain": {
    "type": "Domain",
    "domainType": "Grid",
    "axes": {
      "x": { "start": -105.498, "stop": -104.502, "num": 256 },
      "y": { "start":   40.498, "stop":   39.502, "num": 256 }
    },
    "referencing": [{
      "coordinates": ["x", "y"],
      "system": {
        "type": "GeographicCRS",
        "id": "http://www.opengis.net/def/crs/EPSG/0/4326"
      }
    }]
  },
  "parameters": {
    "b1": {
      "type": "Parameter",
      "observedProperty": { "label": { "en": "b1" } }
    }
  },
  "ranges": {
    "b1": {
      "type": "NdArray",
      "dataType": "float",
      "axisNames": ["y", "x"],
      "shape": [256, 256],
      "values": [0.12, 0.13, null, "..."]
    }
  }
}
```

## 10. Errors

| Code | Condition |
| --- | --- |
| `400` | Unsupported `f` value; bbox exceeds the hard cell-count ceiling; more than one of `parameter-name` / `bidx` / `expression`; invalid or unsupported CRS; degenerate bbox (`minx >= maxx` or `miny >= maxy`). |
| `404` | `url` (or its asset) not found. |
| `422` | Malformed path bbox (non-numeric segment) or other FastAPI / Pydantic validation failure. |
| `500` | Unexpected internal processing error. |

Error bodies follow TiTiler's convention (a JSON object with a `detail`
message).

## 11. Acceptance

An integration test reads a sample Cloud-Optimized GeoTIFF (COG) through the
endpoint and validates the response against the vendored CoverageJSON JSON
Schema (`tests/fixtures/schemas/coveragejson.json`), asserting the
`application/prs.coverage+json` content type. This is the model layer's first
real HTTP exercise; the model layer itself is already implemented and tested
for the Grid domain.

## 12. Relationship to other documents

- [ADR-0001](adr/0001-covjson-http-api-direction.md) sets the direction this
  spec realizes.
- [doc 07](07-api-design-alternatives.md) is the supporting analysis behind
  ADR-0001.
- [doc 02](02-api-definition.md) is the earlier bespoke API definition;
  ADR-0001 supersedes its direction, and this document supersedes its `/bbox`
  treatment. doc 02's remaining endpoints (point, transect, time series, tile,
  info) have not yet been redesigned under the ADR-0001 direction.
