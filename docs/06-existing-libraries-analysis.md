# Existing CoverageJSON Libraries Analysis

## 1. Overview

Two existing open-source libraries accelerate this project by eliminating custom model code and providing spec validation:

| Library | Role | Install |
|---|---|---|
| [KNMI/covjson-pydantic](https://github.com/KNMI/covjson-pydantic) | Pydantic v2 models for CovJSON (FastAPI-ready) | `pip install covjson-pydantic` |
| [covjson/covjson-validator](https://github.com/covjson/covjson-validator) | JSON Schema + runtime validator | Clone repo (no pip package) |

---

## 2. covjson-pydantic (KNMI)

### 2.1 What It Provides

A pip-installable package (`v0.7.0`, Apache-2.0) of **Pydantic v2 models** that fully represent the CoverageJSON specification. Designed explicitly for FastAPI integration.

**Dependencies**: Only `pydantic>=2.3,<3` -- extremely lightweight.

**Maintenance**: Active. 7 releases since 2023, used in production at KNMI for their OGC EDR API. Python 3.8-3.13 supported.

### 2.2 Model Hierarchy

```
covjson_pydantic
├── coverage
│   ├── Coverage           # Top-level coverage document
│   └── CoverageCollection # Collection of coverages
├── domain
│   ├── Domain             # Spatial/temporal domain
│   ├── Axes               # Container for axis definitions
│   ├── ValuesAxis[T]      # Generic typed axis (float, AwareDatetime, tuple, polygon)
│   ├── CompactAxis        # Regular-spaced axis (start, stop, num)
│   └── DomainType         # Enum (Grid, Point, PointSeries, MultiPoint, etc.)
├── ndarray
│   ├── NdArray            # Abstract base (raises TypeError)
│   ├── NdArrayFloat       # Float-typed range
│   ├── NdArrayInt         # Integer-typed range
│   ├── NdArrayStr         # String-typed range
│   ├── TiledNdArrayFloat  # Tiled float range
│   └── TileSet            # Tile shape + URL template
├── parameter
│   ├── Parameter          # Observable property metadata
│   └── Parameters         # RootModel wrapping Dict[str, Parameter]
├── observed_property
│   ├── ObservedProperty   # What is measured
│   └── Category           # Category definitions
├── reference_system
│   ├── ReferenceSystem
│   └── ReferenceSystemConnectionObject
├── unit
│   ├── Unit
│   └── Symbol
└── i18n
    └── i18n               # Type alias for Dict[str, str]
```

### 2.3 Key Design Features

- **Generic typed axes**: `ValuesAxis[float]`, `ValuesAxis[AwareDatetime]`, `ValuesAxis[Tuple]` etc.
- **Discriminated unions**: `NdArrayTypes` selects `NdArrayFloat`/`NdArrayInt`/`NdArrayStr` based on `dataType` field
- **Built-in validators**: axis consistency per domain type, shape/values product matching, bounds length matching
- **FastAPI-native**: `.model_dump_json(exclude_none=True)` produces spec-compliant CoverageJSON
- **Extensible**: Coverage/Domain use `extra="allow"` for custom properties

### 2.4 Supported Domain Types

| Domain Type | Supported |
|---|---|
| Grid | Yes |
| Point | Yes |
| PointSeries | Yes |
| MultiPoint | Yes |
| MultiPointSeries | Yes |
| Trajectory | Yes |
| PolygonSeries | Yes |
| VerticalProfile | Yes |
| **Polygon** | **No** (issue #25) |
| **MultiPolygon** | **No** |
| **MultiPolygonSeries** | **No** |
| **Section** | **No** |

### 2.5 Limitations

1. **Missing domain types**: Polygon, MultiPolygon, MultiPolygonSeries, Section not supported (issue #25)
2. **TiledNdArray**: Only float variant (`TiledNdArrayFloat`), no int/string tiled types
3. **Composite axis**: Basic `ValuesAxis[Tuple]` support, but polygon composite axes may need extension
4. **Pre-1.0**: API may change between minor versions
5. **No data-loading utilities**: Models only, no IO or conversion helpers

---

## 3. covjson-validator

### 3.1 What It Provides

A JSON Schema-based validator with a Python CLI tool and runtime validation logic beyond what JSON Schema can express.

**Not a pip package** -- must clone the repository.

**Maintenance**: Infrequent. v1.0.0 released January 2023. Uses deprecated `jsonschema.RefResolver` (issue #41).

### 3.2 Components

1. **JSON Schema files** (`/schemas/`): ~25+ JSON Schema documents (Draft 2020-12) covering all 12 CovJSON domain types
2. **Runtime validator** (`runtime_validator(obj)`): Python checks for axis/shape consistency, monotonicity, categoryEncoding, parameterGroup validation, tiled URL template resolution
3. **CLI tool**: `python -m tools.validator my.covjson`
4. **Published schema**: `https://covjson.org/schema/dev/coveragejson.json`

### 3.3 Limitations

- Not pip-installable (no pyproject.toml)
- Uses deprecated `jsonschema.RefResolver`
- No Pydantic integration
- Cannot be used as FastAPI response models

---

## 4. Impact on Project Design

### 4.1 What This Means for titiler-covjson

**No custom Pydantic models needed.** Add `covjson-pydantic` as a dependency and use its models directly. Custom code is limited to:

1. **`CoverageInput` dataclass** (bridge between rio-tiler and covjson-pydantic)
2. **`RasterCovJSONModeler`** (conversion logic: numpy arrays -> covjson-pydantic objects)
3. **FastAPI routes** (endpoint definitions, parameter handling)
4. **TiTiler integration** (router extension, content type registration)
5. **Unit/CRS mapping helpers** (UCUM symbols, EPSG->OGC URI conversion)
6. **Polygon domain type extension** (if needed, until covjson-pydantic adds support)

### 4.2 Gap: Missing Polygon Domain Types

**Options:**
1. **Contribute upstream** to KNMI/covjson-pydantic (issue #25 exists)
2. **Extend locally** by subclassing Domain with additional domain type support
3. **Use PolygonSeries** (which IS supported) for single-polygon cases by omitting the `t` axis
4. **Avoid polygon domains** for the initial release and use Grid domain with masking instead

**Recommendation**: Option 3 or 4 for MVP, with upstream contribution (option 1) for long-term.

---

## 5. Dependency Stack

```
covjson-pydantic >= 0.7.0, < 1.0   # CovJSON Pydantic models (runtime)
pydantic >= 2.3                     # Transitive via covjson-pydantic
titiler.core >= 0.18.0              # TiTiler base
rio-tiler >= 7.0.0                  # Raster data access
rasterio                            # CRS handling, GDAL bindings
shapely >= 2.0                      # Geometry operations (transect, buffer)
numpy                               # Array operations

# Test dependencies
covjson-validator (vendored schemas) # Deep CovJSON spec validation
pytest
httpx                                # FastAPI test client
```

---

## 6. Recommendations

| # | Recommendation | Rationale |
|---|---|---|
| 1 | **Adopt `covjson-pydantic` as primary model layer** | Eliminates ~300 lines of custom models; spec-compliant; FastAPI-native; maintained by KNMI |
| 2 | **Use `covjson-validator` in integration tests** | Catches spec violations that Pydantic alone might miss (axis/shape consistency, monotonicity) |
| 3 | **Vendor validator schemas, don't depend on the repo** | The repo isn't pip-installable and has deprecated deps; extract the JSON Schema files |
| 4 | **Contribute Polygon domain type upstream** | Benefits the community; reduces local maintenance burden |
| 5 | **Keep `CoverageInput` + `RasterCovJSONModeler` as custom code** | Neither library provides the raster->CovJSON conversion bridge |
| 6 | **Pin `covjson-pydantic` minor version** | Pre-1.0 library; avoid surprise breaking changes |
