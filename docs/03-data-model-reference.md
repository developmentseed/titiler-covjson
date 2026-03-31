# Data Model Reference

## 1. Overview

titiler-covjson uses [`covjson-pydantic`](https://github.com/KNMI/covjson-pydantic) (v0.7.0+, KNMI) as its CoverageJSON model layer. This document describes how those models are used and supplemented with project-specific helpers.

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

These project-specific helpers wrap covjson-pydantic for common patterns:

### 4.1 Reference System Factories

```python
from covjson_pydantic.reference_system import (
    ReferenceSystem, ReferenceSystemConnectionObject
)

def create_spatial_2d_ref(
    crs_id: str = "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
) -> ReferenceSystemConnectionObject:
    return ReferenceSystemConnectionObject(
        coordinates=["x", "y"],
        system=ReferenceSystem(type="GeographicCRS", id=crs_id),
    )

def create_temporal_ref() -> ReferenceSystemConnectionObject:
    return ReferenceSystemConnectionObject(
        coordinates=["t"],
        system=ReferenceSystem(type="TemporalRS", calendar="Gregorian"),
    )

def crs_to_ogc_uri(crs) -> str:
    """Convert rasterio CRS to OGC CRS URI."""
    if crs.to_epsg() == 4326:
        return "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
    epsg = crs.to_epsg()
    if epsg:
        return f"http://www.opengis.net/def/crs/EPSG/0/{epsg}"
    return "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
```

### 4.2 Data Type Mapping

```python
import numpy as np

DTYPE_MAP = {
    np.float16: "float", np.float32: "float", np.float64: "float",
    np.int8: "integer", np.int16: "integer", np.int32: "integer",
    np.int64: "integer", np.uint8: "integer", np.uint16: "integer",
    np.uint32: "integer", np.uint64: "integer",
}

def get_covjson_datatype(dtype: np.dtype) -> str:
    return DTYPE_MAP.get(dtype.type, "float")
```

### 4.3 Unit Mapping (UCUM)

```python
from covjson_pydantic.unit import Unit, Symbol

UNIT_MAP = {
    "cm/a":  {"label": "cm/year",  "symbol": "cm/a"},
    "cm":    {"label": "cm",       "symbol": "cm"},
    "mm":    {"label": "mm",       "symbol": "mm"},
    "mm/a":  {"label": "mm/year",  "symbol": "mm/a"},
    "m/d":   {"label": "m/day",    "symbol": "m/d"},
    "m":     {"label": "meters",   "symbol": "m"},
    "K":     {"label": "Kelvin",   "symbol": "K"},
    "1":     {"label": "unitless", "symbol": "1"},
    "dB":    {"label": "decibels", "symbol": "dB"},
    "deg":   {"label": "degrees",  "symbol": "deg"},
}

def create_unit(unit_str: str) -> Unit:
    mapping = UNIT_MAP.get(unit_str, {"label": unit_str, "symbol": unit_str})
    return Unit(
        label={"en": mapping["label"]},
        symbol=Symbol(value=mapping["symbol"]),
    )
```

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
| **Polygon** | **No** | Not yet in covjson-pydantic (issue #25) |
| **MultiPolygon** | **No** | Not yet in covjson-pydantic |
| **MultiPolygonSeries** | **No** | Not yet in covjson-pydantic |
| **Section** | **No** | Not yet in covjson-pydantic |

**Workaround for Polygon**: Use `PolygonSeries` without a `t` axis, or use Grid with masking.
