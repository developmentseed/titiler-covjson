# Data Model Reference

> **Early design reference.** This document predates much of the
> implementation and is kept for its conceptual overview: covjson-pydantic as
> the model layer, supplemented by a set of project helpers. For exact
> signatures and behavior, treat the source modules as authoritative:
> [`helpers.py`](../src/titiler_covjson/helpers.py),
> [`input.py`](../src/titiler_covjson/input.py), and
> [`modeler.py`](../src/titiler_covjson/modeler.py), each of which carries full
> docstrings and runnable doctests.

## 1. Overview

titiler-covjson uses [`covjson-pydantic`](https://github.com/KNMI/covjson-pydantic) (>=0.8.0, KNMI) as its CoverageJSON model layer. This document describes how those models are used and supplemented with project-specific helpers.

See [06-existing-libraries-analysis.md](06-existing-libraries-analysis.md) for a full assessment of the library.

---

## 2. covjson-pydantic Model Mapping

All CovJSON types are provided by `covjson-pydantic`:

| CovJSON Concept | covjson-pydantic Class | Import Path |
|---|---|---|
| Coverage | `Coverage` | `covjson_pydantic.coverage` |
| CoverageCollection | `CoverageCollection` | `covjson_pydantic.coverage` |
| Domain | `Domain` | `covjson_pydantic.domain` |
| Axis (values) | `ValuesAxis[T]` | `covjson_pydantic.domain` |
| Axis (regular-spaced) | `CompactAxis` | `covjson_pydantic.domain` |
| DomainType | `DomainType` enum | `covjson_pydantic.domain` |
| NdArray (float) | `NdArrayFloat` | `covjson_pydantic.ndarray` |
| NdArray (int) | `NdArrayInt` | `covjson_pydantic.ndarray` |
| NdArray (string) | `NdArrayStr` | `covjson_pydantic.ndarray` |
| TiledNdArray | `TiledNdArrayFloat` | `covjson_pydantic.ndarray` |
| TileSet | `TileSet` | `covjson_pydantic.ndarray` |
| Parameter | `Parameter` | `covjson_pydantic.parameter` |
| ObservedProperty | `ObservedProperty` | `covjson_pydantic.observed_property` |
| Unit | `Unit` | `covjson_pydantic.unit` |
| Symbol | `Symbol` | `covjson_pydantic.unit` |
| ReferenceSystem | `ReferenceSystem` | `covjson_pydantic.reference_system` |
| Reference connection | `ReferenceSystemConnectionObject` | `covjson_pydantic.reference_system` |

---

## 3. Axis Usage Patterns

| Domain Type | Axes Created |
|---|---|
| Point | `x: ValuesAxis[float]`, `y: ValuesAxis[float]`, optionally `z: ValuesAxis[float]` |
| PointSeries | Same as Point + `t: ValuesAxis[AwareDatetime]` |
| Grid | `x: CompactAxis(start, stop, num)`, `y: CompactAxis(start, stop, num)` |
| MultiPoint | `composite: ValuesAxis[Tuple]` with `coordinates: ["x", "y"]` |
| Polygon | `composite: ValuesAxis` with polygon coordinates |
| PolygonSeries | Same as Polygon + `t: ValuesAxis[AwareDatetime]` |
| Trajectory | `composite: ValuesAxis[Tuple]` with sampled `[lon, lat]` values |

---

## 4. Helper Utilities (titiler_covjson.helpers)

These project-specific helpers wrap covjson-pydantic for common patterns. The
signatures and summaries below describe the public surface; the full docstrings
and runnable doctests in `helpers.py` are authoritative.

### 4.1 Reference System Factories

- `create_spatial_2d_reference(crs: rasterio.CRS)` returns a
  `ReferenceSystemConnectionObject` for a 2-D spatial reference. Its
  `coordinates` follow the CRS's declared axis order: a latitude/northing-first
  CRS (e.g., `EPSG:4326`) yields `["y", "x"]`, while a longitude/easting-first
  CRS (`CRS84`, projected CRSs) yields `["x", "y"]`. The system `type` is
  `GeographicCRS` or `ProjectedCRS`, and its `id` comes from `crs_to_ogc_uri`.
- `create_temporal_reference()` returns a `ReferenceSystemConnectionObject` for
  an ISO 8601 `TemporalRS` on the Gregorian calendar.
- `crs_to_ogc_uri(crs: rasterio.CRS)` maps a CRS to its OGC URI string via the
  authority code: EPSG yields `http://www.opengis.net/def/crs/EPSG/0/{code}`
  and OGC yields `http://www.opengis.net/def/crs/OGC/1.3/{code}`. It raises
  `ValueError` on an unrecognized authority rather than defaulting to CRS84.
  Because TiTiler's WGS84 default is the `EPSG:4326` CRS, that CRS emits the
  `.../EPSG/0/4326` URI, not the distinct CRS84 URI.

### 4.2 Data Type Mapping

- `numpy_to_covjson_dtype(dtype)` returns the CoverageJSON data type string
  (`"float"`, `"integer"`, or `"string"`), selected from the numpy dtype's kind,
  and raises `ValueError` on an unsupported dtype.
- `numpy_dtype_to_ndarray(data, dtype, axis_names)` converts a masked numpy
  array for a single band into the matching NdArray range object
  (`NdArrayFloat`, `NdArrayInt`, or `NdArrayStr`), choosing the subtype from the
  declared band `dtype`. It accepts any rank (0-D scalar, 1-D profile, or 2-D
  grid), takes `shape` from the array, and represents masked entries as missing
  values (`NaN` for float, `null` for integer / string).

### 4.3 Unit Mapping (UCUM)

- `create_unit(ucum_code: str)` returns a CoverageJSON `Unit` for a valid UCUM
  (Unified Code for Units of Measure) code, or `None` for an invalid one. A
  curated table supplies preferred English labels for common codes; any other
  valid UCUM code falls back to a label derived from pint's canonical unit name
  (via a `ucumvert` registry). Each `Unit` pairs an English label with a UCUM
  `Symbol` typed `http://www.opengis.net/def/uom/UCUM/`.

---

## 5. Supported Domain Types

| Domain Type | covjson-pydantic | Notes |
|---|---|---|
| Grid | Yes | Primary for raster data |
| Point | Yes | |
| PointSeries | Yes | |
| MultiPoint | Yes | |
| MultiPointSeries | Yes | |
| Trajectory | Yes | |
| PolygonSeries | Yes | |
| VerticalProfile | Yes | |
| Polygon | Yes | Added in covjson-pydantic 0.8.0 |
| **MultiPolygon** | **No** | Not yet in covjson-pydantic |
| **MultiPolygonSeries** | **No** | Not yet in covjson-pydantic |
| **Section** | **No** | Not yet in covjson-pydantic |

`PolygonSeries` requires a `t` axis, so a single-geometry Polygon coverage uses
the `Polygon` domain type directly (rather than a `PolygonSeries` workaround).
