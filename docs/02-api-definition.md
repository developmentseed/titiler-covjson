# CoverageJSON API Definition

> **Superseded in direction.** This is the earlier bespoke API definition.
> [ADR-0001](adr/0001-covjson-http-api-direction.md) supersedes its overall
> direction (an OGC API - Environmental Data Retrieval (EDR) aligned vocabulary
> on a dedicated `BaseFactory` subclass), and
> [doc 08](08-bbox-endpoint-spec.md) supersedes its `/bbox` treatment. Of the
> remaining endpoints below, point has shipped as `/position`; tile is dropped
> ([ADR-0004](adr/0004-non-temporal-surface-edr-query-verbs.md)); transect is
> reclassified as the temporal `/trajectory`, its spatial-only case served by
> `/position` with a `MULTIPOINT`
> ([ADR-0005](adr/0005-trajectory-temporal-multipoint-non-temporal.md)); and
> time series and info have not yet been redesigned. All are retained here for
> reference.

## 1. API Design Principles

- **TiTiler Extension**: Implemented as a FastAPI router extension to TiTiler, not a standalone service
- **STAC-first**: All data references use STAC item/collection identifiers or direct asset URLs
- **CovJSON-native**: Responses conform to CoverageJSON specification with registered MIME type
- **OGC-aligned**: Where applicable, follow OGC API - Coverages patterns
- **Backwards-compatible**: Existing TiTiler endpoints remain unchanged; CovJSON is additive

---

## 2. Base Path & Versioning

```
/api/v1/covjson/
```

Or integrated into TiTiler's existing router structure:
```
/cog/covjson/...      (for COG sources)
/stac/covjson/...     (for STAC sources)
/mosaic/covjson/...   (for MosaicJSON sources)
```

---

## 3. Endpoint Specifications

### 3.1 Point Query

Retrieve coverage values at a specific geographic coordinate.

```
GET /covjson/point
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | string | yes | URL to COG/asset or STAC item |
| `lon` | float | yes | Longitude (WGS84) |
| `lat` | float | yes | Latitude (WGS84) |
| `bands` | int[] | no | Band indices to extract (default: all) |
| `band_names` | string[] | no | Band names to extract (alternative to indices) |
| `expression` | string | no | Band math expression |
| `rescale` | string | no | Min,max rescale values |
| `nodata` | float | no | Override nodata value |
| `collection` | string | no | STAC collection ID (for multi-item queries) |
| `datetime` | string | no | ISO 8601 datetime or interval filter |

**Response**: `Coverage` with `DomainType: Point` or `PointSeries`

```json
{
  "type": "Coverage",
  "domain": {
    "type": "Domain",
    "domainType": "Point",
    "axes": {
      "x": { "values": [12.345] },
      "y": { "values": [45.678] }
    },
    "referencing": [
      {
        "coordinates": ["x", "y"],
        "system": {
          "type": "GeographicCRS",
          "id": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
        }
      }
    ]
  },
  "parameters": {
    "B04": {
      "type": "Parameter",
      "observedProperty": {
        "id": "B04",
        "label": { "en": "Red" },
        "description": { "en": "Red band (665nm)" }
      },
      "unit": {
        "label": { "en": "reflectance" },
        "symbol": { "type": "http://www.opengis.net/def/uom/UCUM/", "value": "1" }
      }
    }
  },
  "ranges": {
    "B04": {
      "type": "NdArray",
      "dataType": "float",
      "axisNames": ["x", "y"],
      "shape": [1],
      "values": [0.1234]
    }
  }
}
```

**Notes**:
- Point query performs exact pixel lookup via rio-tiler.
- If `datetime` spans multiple timestamps and multiple STAC items are resolved, response becomes `PointSeries` with a `t` axis.

---

### 3.2 Bounding Box / Area Query

Retrieve full or aggregated coverage values within a spatial extent.

```
GET /covjson/bbox
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | string | yes | URL to COG/asset or STAC item |
| `bbox` | float[4] | yes | minX,minY,maxX,maxY (WGS84) |
| `bands` | int[] | no | Band indices |
| `band_names` | string[] | no | Band names |
| `width` | int | no | Output width in pixels (default: native) |
| `height` | int | no | Output height in pixels (default: native) |
| `max_size` | int | no | Max dimension limit (default: 1024) |
| `format` | string | no | "full" or "aggregated" (default: "full") |
| `aggregation` | string | no | For aggregated: "mean", "median", "min", "max", "std" |
| `nodata` | float | no | Override nodata value |
| `collection` | string | no | STAC collection ID |
| `datetime` | string | no | ISO 8601 datetime or interval |

**Response** (`format=full`): `Coverage` with `DomainType: Grid`

```json
{
  "type": "Coverage",
  "domain": {
    "type": "Domain",
    "domainType": "Grid",
    "axes": {
      "x": { "start": 12.0, "stop": 13.0, "num": 256 },
      "y": { "start": 45.0, "stop": 46.0, "num": 256 }
    },
    "referencing": [
      {
        "coordinates": ["x", "y"],
        "system": {
          "type": "GeographicCRS",
          "id": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
        }
      }
    ]
  },
  "parameters": { "...": "..." },
  "ranges": {
    "B04": {
      "type": "NdArray",
      "dataType": "float",
      "axisNames": ["y", "x"],
      "shape": [256, 256],
      "values": [0.12, 0.13, null, "..."]
    }
  }
}
```

**Response** (`format=aggregated`): `Coverage` with `DomainType: Polygon`

Returns a single aggregated value (mean, median, etc.) over the bounding box polygon.

**Notes**:
- `max_size` constrains the maximum pixel dimension to prevent oversized responses.
- `format=full` returns the full raster grid. `format=aggregated` computes statistics.

---

### 3.3 Transect / Line Profile

Extract coverage values along a line transect.

```
GET /covjson/transect
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | string | yes | URL to COG/asset or STAC item |
| `coordinates` | string | yes | Line coordinates as `lon1,lat1\|lon2,lat2[\|lon3,lat3...]` |
| `bands` | int[] | no | Band indices |
| `band_names` | string[] | no | Band names |
| `resolution` | float | no | Sampling resolution in meters (default: native pixel) |
| `buffer` | float | no | Buffer distance in meters for aggregation along line |
| `nodata` | float | no | Override nodata value |

**Response**: `Coverage` with `DomainType: Trajectory`

> **Correction
> ([ADR-0005](adr/0005-trajectory-temporal-multipoint-non-temporal.md)).**
> The example below is schema-invalid: a CoverageJSON Trajectory requires a `t`
> coordinate in every composite tuple (`["t", "x", "y"]`), so a spatial-only
> line like this one is a **MultiPoint** coverage, not a Trajectory. A
> `t`-bearing Trajectory is the temporal `/trajectory` verb; a spatial
> multi-position sample is the Position verb with a `MULTIPOINT` geometry.

```json
{
  "type": "Coverage",
  "domain": {
    "type": "Domain",
    "domainType": "Trajectory",
    "axes": {
      "composite": {
        "dataType": "tuple",
        "coordinates": ["x", "y"],
        "values": [
          [12.0, 45.0], [12.1, 45.05], [12.2, 45.1], [12.3, 45.15]
        ]
      }
    },
    "referencing": [
      {
        "coordinates": ["x", "y"],
        "system": {
          "type": "GeographicCRS",
          "id": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
        }
      }
    ]
  },
  "ranges": {
    "elevation": {
      "type": "NdArray",
      "dataType": "float",
      "axisNames": ["composite"],
      "shape": [4],
      "values": [120.5, 135.2, 142.8, 138.1]
    }
  }
}
```

**Notes**:
- Supports multi-vertex polylines. Samples values at `resolution` intervals along the line.
- If `buffer > 0`, values are aggregated (mean) within the buffer distance.

---

### 3.4 Tile Query (CovJSON Tile)

Retrieve a single map tile as CovJSON (alternative to PNG/WebP tiles).

```
GET /covjson/tiles/{z}/{x}/{y}
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | string | yes | URL to COG/asset or STAC item |
| `bands` | int[] | no | Band indices |
| `band_names` | string[] | no | Band names |
| `tile_size` | int | no | Tile size in pixels (default: 256) |
| `nodata` | float | no | Override nodata value |

**Response**: `Coverage` with `DomainType: Grid`

---

### 3.5 Coverage Metadata (Info)

Retrieve coverage metadata without data values.

```
GET /covjson/info
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | string | yes | URL to COG/asset or STAC item |
| `collection` | string | no | STAC collection ID |

**Response**: `Coverage` with domain and parameters but empty ranges.

---

### 3.6 Time Series Query

Retrieve a time series of coverage values at a point or area from a STAC collection.

```
GET /covjson/timeseries
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `collection` | string | yes | STAC collection URL or ID |
| `lon` | float | cond. | Longitude (required for point query) |
| `lat` | float | cond. | Latitude (required for point query) |
| `bbox` | float[4] | cond. | Bounding box (alternative to point) |
| `bands` | int[] | no | Band indices |
| `band_names` | string[] | no | Band names |
| `datetime` | string | no | ISO 8601 interval (default: full extent) |
| `aggregation` | string | no | For bbox: "mean", "median", "min", "max" |
| `limit` | int | no | Max number of temporal steps (default: 100) |

**Response (point)**: `Coverage` with `DomainType: PointSeries`

```json
{
  "type": "Coverage",
  "domain": {
    "type": "Domain",
    "domainType": "PointSeries",
    "axes": {
      "x": { "values": [12.345] },
      "y": { "values": [45.678] },
      "t": { "values": ["2023-01-15T00:00:00Z", "2023-02-14T00:00:00Z", "2023-03-16T00:00:00Z"] }
    },
    "referencing": [
      {
        "coordinates": ["x", "y"],
        "system": { "type": "GeographicCRS", "id": "http://www.opengis.net/def/crs/OGC/1.3/CRS84" }
      },
      {
        "coordinates": ["t"],
        "system": { "type": "TemporalRS", "calendar": "Gregorian" }
      }
    ]
  },
  "parameters": { "NDVI": { "...": "..." } },
  "ranges": {
    "NDVI": {
      "type": "NdArray",
      "dataType": "float",
      "axisNames": ["t"],
      "shape": [3],
      "values": [0.45, 0.62, 0.71]
    }
  }
}
```

---

### 3.7 Overview / Downsampled Grid

Return a low-resolution overview of coverage data, leveraging COG overviews.

```
GET /covjson/overview
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | string | yes | URL to COG/asset or STAC item |
| `bbox` | float[4] | no | Bounding box (default: full extent) |
| `bands` | int[] | no | Band indices |
| `band_names` | string[] | no | Band names |
| `width` | int | no | Output grid width (default: 64) |
| `height` | int | no | Output grid height (default: 64) |

**Response**: `Coverage` with `DomainType: Grid` at overview resolution.

---

## 4. Error Responses

All endpoints return standard HTTP error codes with a JSON body:

```json
{
  "detail": "Bounding box exceeds maximum allowed size of 4096x4096 pixels",
  "status_code": 400
}
```

| Code | Condition |
|------|-----------|
| 400 | Invalid parameters, bbox too large, unsupported CRS |
| 404 | Resource not found (URL, collection, band) |
| 422 | Validation error (FastAPI/Pydantic) |
| 500 | Internal processing error |

---

## 5. Content Negotiation

| Accept Header | Response Format |
|---|---|
| `application/prs.coverage+json` | CoverageJSON |
| `application/json` | CoverageJSON (same content, generic type) |
| Not specified | CoverageJSON (default for these endpoints) |
