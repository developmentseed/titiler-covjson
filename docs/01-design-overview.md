# titiler-covjson - Design Overview

> **Superseded in direction.** The architecture below (CoverageInput -> modeler
> -> covjson-pydantic) remains accurate, but the API direction predates
> [ADR-0001](adr/0001-covjson-http-api-direction.md). The supported query
> patterns in Section 3 (notably bbox `format=aggregated` and a separate
> overview) are superseded: see ADR-0001 for the current direction (an OGC API -
> Environmental Data Retrieval (EDR) aligned vocabulary on a dedicated
> `BaseFactory` subclass) and [doc 08](08-bbox-endpoint-spec.md) for the first
> endpoint specified under it.

## 1. Context & Motivation

### 1.1 Background

[CoverageJSON](https://github.com/opengeospatial/CoverageJSON) (CovJSON) is an OGC Community Standard for representing geospatial coverage data in JSON. It is well-suited for serving raster and time series data to web clients, with native support in visualization libraries like Leaflet-CovJSON.

**titiler-covjson** adds CoverageJSON as a new output format to [TiTiler](https://developmentseed.org/titiler/), a dynamic tile server built on FastAPI + rio-tiler. This enables any TiTiler deployment to serve coverage data in a standards-compliant, interoperable format alongside existing PNG/WebP tile output.

### 1.2 What CoverageJSON Is

| Concept | Description |
|---------|-------------|
| **Coverage** | Top-level object binding a Domain, Parameters, and Ranges |
| **CoverageCollection** | Container for multiple Coverage objects sharing common parameters/references |
| **Domain** | Defines the spatial/temporal structure (axes + coordinate reference systems) |
| **Range** | N-dimensional data array (`NdArray` or `TiledNdArray`) with typed values |
| **Parameter** | Metadata describing an observed property (name, unit, description) |
| **Reference** | Links coordinate axes to CRS (spatial or temporal) |

### 1.3 Scope of This Document Set

1. **Design Overview** (this document) - Architecture and key decisions
2. **API Definition** - Endpoint specifications
3. **Data Model Reference** - CovJSON models via covjson-pydantic
4. **Modeler/Converter Design** - How raster data maps to CovJSON
5. **Implementation Roadmap** - EPIC breakdown with stories
6. **Existing Libraries Analysis** - covjson-pydantic and covjson-validator assessment

---

## 2. Architecture

```
Client Request
    |
    v
FastAPI Router (TiTiler extension endpoints)
    |
    v
TiTiler/rio-tiler Reader (COG, Zarr, NetCDF...)
    |
    v
numpy arrays + metadata (bands, CRS, bounds, timestamps)
    |
    v
CoverageInput (intermediate representation)
    |
    v
Modeler functions (to_coverage; conversion logic)
    |
    v
covjson-pydantic Models (Coverage, Domain, Range, Parameter...)
    |
    v
JSON Response (application/prs.coverage+json)
```

### Key Design Decisions

- **TiTiler extension**: Implemented as a FastAPI router that plugs into TiTiler, not a standalone service
- **Data-agnostic modeler**: Stateless module-level functions (`to_coverage`) convert an intermediate `CoverageInput` to CovJSON, decoupled from specific readers
- **covjson-pydantic**: Uses the [KNMI covjson-pydantic](https://github.com/KNMI/covjson-pydantic) library (Pydantic v2) for spec-compliant model serialization
- **Domain type auto-detection**: Geometry type determines CovJSON domain type (Grid for raster, Point/PointSeries for point queries, Trajectory for transects, etc.)
- **Grid-native**: Raster data naturally maps to CovJSON Grid domains, the most common case
- **Band metadata drives Parameters**: Band names, units, and descriptions map to CovJSON Parameter objects
- **Null for nodata**: Masked array values serialize as JSON `null` in CovJSON value arrays

---

## 3. Supported Query Patterns

These query patterns go beyond simply serializing rasters and provide spatial/temporal extraction capabilities:

1. **Point Query** - Retrieve coverage values at a specific coordinate
2. **Bounding Box Query** - Full raster grid or aggregated statistics over an extent
3. **Transect / Line Profile** - Coverage values sampled along a polyline
4. **Tile Query** - Single map tile as CovJSON (alternative to image tiles)
5. **Coverage Info** - Metadata-only response (domain + parameters, no ranges)
6. **Time Series** - Temporal extraction from STAC collection items
7. **Overview** - Low-resolution downsampled grid using COG overviews

---

## 4. Content Type & Negotiation

### MIME Type
```
application/prs.coverage+json
```

The CovJSON specification defines `application/prs.coverage+json`. The implementation:
- Registers this as a custom response type in FastAPI
- Supports content negotiation via `Accept` header
- Defaults to CovJSON when the endpoint is a CovJSON endpoint

---

## 5. Key Technical Decisions

| Decision | Recommendation | Rationale |
|---|---|---|
| Model library | `covjson-pydantic` (KNMI) | Spec-compliant Pydantic v2 models; eliminates custom model code |
| Primary domain type | `Grid` | Raster data naturally maps to Grid domains |
| Temporal handling | `t` axis from STAC temporal extent or band metadata | Time series rasters have datetime per band/asset |
| CRS handling | Read from rasterio, output as OGC CRS URI | e.g., `http://www.opengis.net/def/crs/EPSG/0/4326` |
| Null/nodata | Use `null` in values array | CovJSON supports null for missing values |
| Large datasets | `TiledNdArray` with URL templates | Avoid sending full arrays for large coverages |
| Validation | covjson-pydantic built-in + covjson-validator in tests | Ensure spec compliance at serialization and testing |
