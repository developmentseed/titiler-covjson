# Implementation Roadmap (EPIC)

## EPIC: CoverageJSON Output Format for TiTiler

### Description

Add CoverageJSON (CovJSON) as a new output format to TiTiler via the `titiler-covjson` extension package. This enables any TiTiler deployment to serve geospatial raster and time series data in the OGC CoverageJSON format, supporting interoperability with CovJSON-aware visualization tools (e.g., Leaflet-CovJSON) and OGC API - Coverages consumers.

### Acceptance Criteria

- All endpoints return valid CoverageJSON conforming to the OGC specification
- Content type `application/prs.coverage+json` is properly served
- Point, bbox, transect, tile, time series, and metadata endpoints are operational
- Integration with existing TiTiler readers (COG, STAC, MosaicJSON) works
- Unit tests cover all domain types (Grid, Point, PointSeries, Polygon, Trajectory)
- Performance is acceptable for typical raster sizes (< 2s for a 256x256 tile)

## Stories

### Story 1: CovJSON Model Layer Setup

**Priority**: P0 (Foundation)

**Description**: Adopt [`covjson-pydantic`](https://github.com/KNMI/covjson-pydantic) (v0.7.0, KNMI) as the CovJSON model layer. Create helper utilities on top.

> **NOTE**: See [06-existing-libraries-analysis.md](06-existing-libraries-analysis.md) for full analysis of this library.

**Tasks**:

- [x] Add `covjson-pydantic>=0.7.0` as a project dependency
- [x] Create `titiler_covjson/helpers.py` with convenience factories:
  - `create_spatial_2d_ref(crs_uri)` -> `ReferenceSystemConnectionObject`
  - `create_temporal_ref()` -> `ReferenceSystemConnectionObject`
  - `crs_to_ogc_uri(rasterio.CRS)` -> OGC CRS URI string
  - `create_unit(unit_str)` -> `Unit` (UCUM mapping)
  - `numpy_dtype_to_ndarray(dtype, ...)` -> `NdArrayFloat` / `NdArrayInt` / `NdArrayStr`
- [x] Evaluate gap: Polygon domain type not supported in covjson-pydantic (see gap analysis in doc 06)
- [x] Write unit tests validating helper output and serialization against CovJSON spec examples
- [x] Vendor `covjson-validator` JSON schemas for use in integration tests (Story 13)

**Deliverables**: `titiler_covjson/helpers.py`, `tests/test_helpers.py`, updated `pyproject.toml`

**Estimated effort**: XS (0.5-1 day)

### Story 2: CoverageInput Intermediate Representation

**Priority**: P0 (Foundation)

**Description**: Create the `CoverageInput` dataclass that decouples TiTiler/rio-tiler data from the CovJSON conversion.

**Tasks**:

- [x] Create `titiler_covjson/input.py` with CoverageInput and BandInfo dataclasses
- [x] Implement `imagedata_to_coverage_input()` converter from rio-tiler ImageData
- [x] Handle nodata/mask propagation from ImageData to masked arrays
- [x] Handle CRS extraction and band metadata extraction from reader info
- [x] Write unit tests

**Deliverables**: `titiler_covjson/input.py`, `tests/test_input.py`

**Estimated effort**: S (1-2 days)

### Story 3: RasterCovJSONModeler - Core Conversion Logic

**Priority**: P0 (Foundation)

**Description**: Implement the modeler that converts CoverageInput to CovJSON Coverage objects.

**Tasks**:

- [ ] Create `titiler_covjson/modeler.py` with RasterCovJSONModeler class
- [ ] Implement domain type detection (`_get_domain_type`)
- [ ] Implement axis creation for all domain types (`_create_axes`)
  - Grid (start/stop/num)
  - Point/PointSeries (x, y, optional z, optional t)
  - Polygon/PolygonSeries (composite polygon + optional t)
  - MultiPoint (composite tuple)
  - Trajectory (composite tuple)
- [ ] Implement reference system creation with CRS URI conversion
- [ ] Implement parameter creation from band metadata
- [ ] Implement range creation (NdArray with null for nodata)
- [ ] Implement CoverageCollection creation for multi-result responses
- [ ] Write unit tests for each domain type conversion path

**Deliverables**: `titiler_covjson/modeler.py`, `tests/test_modeler.py`

**Estimated effort**: M (3-5 days)

### Story 4: Point Query Endpoint

**Priority**: P1

**Description**: Implement the `/covjson/point` endpoint for pixel-level value extraction.

**Tasks**:

- [ ] Create FastAPI route `GET /covjson/point`
- [ ] Accept parameters: url, lon, lat, bands, band_names, nodata
- [ ] Use rio-tiler Reader.point() for value extraction
- [ ] Convert to CoverageInput and then to Coverage (DomainType: Point)
- [ ] Return with `application/prs.coverage+json` content type
- [ ] Write integration tests

**Deliverables**: Route in `titiler_covjson/routes.py`

**Estimated effort**: S (1-2 days)

### Story 5: Bounding Box Query Endpoint

**Priority**: P1

**Description**: Implement the `/covjson/bbox` endpoint for area-based coverage retrieval.

**Tasks**:

- [ ] Create FastAPI route `GET /covjson/bbox`
- [ ] Accept parameters: url, bbox, bands, width, height, max_size, format, aggregation, nodata
- [ ] For `format=full`: use Reader.part() for raster extract -> Grid domain
- [ ] For `format=aggregated`: compute stats over bbox -> Polygon domain
- [ ] Enforce max_size limits
- [ ] Write integration tests

**Deliverables**: Route in `titiler_covjson/routes.py`

**Estimated effort**: M (2-3 days)

### Story 6: Transect / Line Profile Endpoint

**Priority**: P1

**Description**: Implement the `/covjson/transect` endpoint for line-based sampling.

**Tasks**:

- [ ] Create FastAPI route `GET /covjson/transect`
- [ ] Accept parameters: url, coordinates (pipe-separated), bands, resolution, buffer, nodata
- [ ] Parse coordinate string into Shapely LineString
- [ ] Sample values along line at resolution intervals using Reader.point()
- [ ] If buffer > 0, buffer line and aggregate values within buffer zone
- [ ] Convert to CoverageInput (Trajectory domain) -> Coverage
- [ ] Write integration tests

**Deliverables**: Route in `titiler_covjson/routes.py`

**Estimated effort**: M (2-3 days)

### Story 7: CovJSON Tile Endpoint

**Priority**: P1

**Description**: Implement the `/covjson/tiles/{z}/{x}/{y}` endpoint serving tiles as CovJSON.

**Tasks**:

- [ ] Create FastAPI route `GET /covjson/tiles/{z}/{x}/{y}`
- [ ] Accept parameters: url, bands, tile_size, nodata
- [ ] Use Reader.tile(x, y, z) for tile data access
- [ ] Convert ImageData to Coverage (DomainType: Grid, 256x256)
- [ ] Set appropriate cache headers
- [ ] Write integration tests

**Deliverables**: Route in `titiler_covjson/routes.py`

**Estimated effort**: S (1-2 days)

### Story 8: Coverage Info / Metadata Endpoint

**Priority**: P2

**Description**: Implement the `/covjson/info` endpoint returning metadata without data values.

**Tasks**:

- [ ] Create FastAPI route `GET /covjson/info`
- [ ] Accept parameters: url, collection
- [ ] Use Reader.info() for metadata extraction
- [ ] Build Coverage with domain and parameters but empty ranges
- [ ] Include full extent bounds and band metadata
- [ ] Write integration tests

**Deliverables**: Route in `titiler_covjson/routes.py`

**Estimated effort**: S (1 day)

### Story 9: Time Series Endpoint (STAC Collection)

**Priority**: P2

**Description**: Implement the `/covjson/timeseries` endpoint for temporal queries across STAC collection items.

**Tasks**:

- [ ] Create FastAPI route `GET /covjson/timeseries`
- [ ] Accept parameters: collection, lon, lat (or bbox), bands, datetime, aggregation, limit
- [ ] Query STAC catalog to resolve items within datetime range
- [ ] For each item, extract point or aggregated values using Reader
- [ ] Build temporal axis from item datetimes
- [ ] Return Coverage with PointSeries or PolygonSeries domain
- [ ] Handle pagination/limit for large collections
- [ ] Write integration tests

**Deliverables**: Route in `titiler_covjson/routes.py`

**Estimated effort**: L (5-8 days)

### Story 10: Overview / Downsampled Grid Endpoint

**Priority**: P2

**Description**: Implement the `/covjson/overview` endpoint for low-resolution overview data.

**Tasks**:

- [ ] Create FastAPI route `GET /covjson/overview`
- [ ] Accept parameters: url, bbox, bands, width, height
- [ ] Use COG overview levels for fast access at reduced resolution
- [ ] Return Coverage with Grid domain at overview resolution
- [ ] Write integration tests

**Deliverables**: Route in `titiler_covjson/routes.py`

**Estimated effort**: S (1-2 days)

### Story 11: TiTiler Router Integration

**Priority**: P1

**Description**: Package all CovJSON endpoints as a TiTiler extension router.

**Tasks**:

- [ ] Create `titiler_covjson/router.py` as a TiTiler `FactoryExtension` or standalone `APIRouter`
- [ ] Register custom response class for `application/prs.coverage+json`
- [ ] Add OpenAPI documentation (tags, descriptions, examples)
- [ ] Wire into TiTiler application startup
- [ ] Add configuration options (max_size, default_tile_size, etc.)
- [ ] Write integration test with TiTiler test app

**Deliverables**: `titiler_covjson/router.py`, updated TiTiler app configuration

**Estimated effort**: M (2-3 days)

### Story 12: TiledNdArray Support for Large Coverages

**Priority**: P3

**Description**: Support `TiledNdArray` range type for large coverages where sending the full values array is impractical.

**Tasks**:

- [ ] Implement TileSet URL template generation
- [ ] When bbox coverage exceeds a threshold (e.g., > 1024x1024), use TiledNdArray instead of NdArray
- [ ] URL template points to CovJSON tile endpoint: `/covjson/tiles/{z}/{x}/{y}?url=...`
- [ ] Client can progressively fetch tiles
- [ ] Write tests

**Deliverables**: Updated modeler, tiled range support

**Estimated effort**: M (3-4 days)

### Story 13: Documentation & Validation

**Priority**: P2

**Description**: Create user-facing documentation and add CovJSON schema validation using [`covjson-validator`](https://github.com/covjson/covjson-validator) schemas.

**Tasks**:

- [ ] Write user documentation (endpoint usage, examples, supported formats)
- [ ] Vendor `covjson-validator` JSON Schemas into test fixtures for offline validation
- [ ] Create integration tests that validate API responses against the official CovJSON JSON Schema (`covjson.org/schema/dev/coveragejson.json`)
- [ ] Port useful runtime checks from `covjson-validator` (axis/shape consistency, monotonicity) into test helpers
- [ ] Create example Jupyter notebook demonstrating CovJSON API usage
- [ ] Validate output against CovJSON playground
- [ ] Document content negotiation and MIME types

**Deliverables**: docs, notebook, validation test suite

**Estimated effort**: M (2-3 days)

## Dependency Graph

```plain
Story 1 (covjson-pydantic + helpers) ──┐
                                        ├──> Story 3 (Modeler) ──┐
Story 2 (CoverageInput)             ──┘                         │
                                                                  ├──> Story 4 (Point)
                                                                  ├──> Story 5 (Bbox)
                                                                  ├──> Story 6 (Transect)
                                                                  ├──> Story 7 (Tile)
                                                                  ├──> Story 8 (Info)
                                                                  ├──> Story 9 (TimeSeries)
                                                                  ├──> Story 10 (Overview)
                                                                  │
                                                                  v
                                                        Story 11 (Router Integration)
                                                                  │
                                                                  v
                                                        Story 12 (TiledNdArray)
                                                        Story 13 (Docs + covjson-validator)
```

## Suggested Sprint Plan

| Sprint | Stories | Focus |
| -------- | --------- | ------- |
| Sprint 1 | 1, 2, 3 | Foundation: models, input, modeler |
| Sprint 2 | 4, 5, 7, 11 | Core endpoints: point, bbox, tile + router |
| Sprint 3 | 6, 8, 10 | Extended endpoints: transect, info, overview |
| Sprint 4 | 9, 12, 13 | Advanced: time series, tiled ranges, docs |

## Technical Risks & Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Large CovJSON payloads for high-res rasters | Performance, memory | Use max_size limits; implement TiledNdArray (Story 12); gzip compression |
| CovJSON spec compliance edge cases | Interop with CovJSON clients | Use `covjson-pydantic` built-in validators + `covjson-validator` schemas in tests |
| `covjson-pydantic` missing Polygon domain type | Cannot represent aggregated bbox results as Polygon | Wait for upstream [PR #30](https://github.com/KNMI/covjson-pydantic/pull/30) (approved 2026-06-11); until released, the modeler raises `NotImplementedError` for polygon-without-time. NOTE: "PolygonSeries without `t` axis" is NOT viable -- both the CovJSON spec and `covjson-pydantic` require a `t` axis on PolygonSeries (verified: `ValidationError`) |
| `covjson-pydantic` pre-1.0 API stability | Breaking changes on minor version bump | Pin to `>=0.7.0,<1.0`; monitor releases |
| Time series across many STAC items | Slow response times | Limit parameter; async fetching; COG overview usage |
| CRS handling complexity | Incorrect coordinates | Default to WGS84; validate with rasterio; test with projected CRS |
| numpy dtype serialization (NaN, inf) | Invalid JSON | Replace NaN/inf with null; use masked arrays |

## Resources & References

- [CoverageJSON Specification (OGC)](https://github.com/opengeospatial/CoverageJSON)
- [covjson-pydantic (KNMI)](https://github.com/KNMI/covjson-pydantic) - Pydantic v2 models for CovJSON (**adopted as dependency**)
- [covjson-validator](https://github.com/covjson/covjson-validator) - JSON Schema + runtime validator (**used for test validation**)
- [CovJSON Playground](https://covjson.org/playground/)
- [TiTiler Documentation](https://developmentseed.org/titiler/)
- [rio-tiler Documentation](https://cogeotiff.github.io/rio-tiler/)
- [OGC API - Coverages](https://ogcapi.ogc.org/coverages/)
- [Existing libraries analysis](06-existing-libraries-analysis.md) - Detailed assessment of covjson-pydantic and covjson-validator
