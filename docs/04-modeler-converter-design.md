# Modeler / Converter Design

## 1. Overview

The **Modeler** is the layer that converts raster data (rio-tiler ImageData, numpy arrays, STAC metadata) into CovJSON model objects (via covjson-pydantic). It follows a clean separation of concerns:

```plain
rio-tiler data  -->  CoverageInput (intermediate)  -->  modeler (to_coverage)  -->  covjson-pydantic Coverage
```

## 2. Conversion Flow

```plain
CoverageInput
    |
    +---> _get_domain_type()     # geometry/data shape -> DomainType enum
    +---> _create_axes()         # bounds/coords -> Axis objects
    +---> _get_references()      # CRS -> ReferenceSystemConnectionObject[]
    |         |
    |         v
    +---> Domain(domainType, axes, referencing)
    |
    +---> _create_parameters()   # BandInfo -> Parameter dict
    |         |
    |         +---> create_unit()              # unit string -> Unit + Symbol
    |         +---> ObservedProperty()         # band name/desc
    |
    +---> _create_ranges()       # masked arrays -> NdArray dict
    |         |
    |         +---> get_covjson_datatype()  # numpy dtype -> "float"/"integer"/"string"
    |         +---> _get_range_axis_names() # determine axis names per range
    |
    v
Coverage(domain, parameters, ranges)
```

## 3. CoverageInput Data Class

> **Status (#22).** The single `CoverageInput` shown here has been split into a
> per-domain union (Section 7.5): a shared base plus one frozen dataclass per
> domain, of which only `GridInput` exists so far, with `CoverageInput` now an
> alias for that union. On the Grid variant `bounds` is required and there is no
> `geometry`, no `timestamps`, and no 2-D data path. The sketch below records the
> original pre-union design.

The intermediate representation between TiTiler's data access and CovJSON
serialization, implemented in `src/titiler_covjson/input.py` (Story 2):

```python
from dataclasses import dataclass
import numpy as np
import numpy.typing as npt
import rasterio
from shapely.geometry.base import BaseGeometry

@dataclass(frozen=True)
class BandInfo:
    """Metadata for a single band/variable."""
    name: str
    description: str = ""
    unit: str = ""  # raw UCUM code; resolved via helpers.create_unit
    dtype: npt.DTypeLike = np.float32

@dataclass(frozen=True, eq=False)
class CoverageInput:
    """Intermediate data structure for CovJSON conversion.

    Decouples the modeler from rio-tiler specifics. Frozen with tuple
    collection fields, so instances are immutable except for the contents
    of the ``data`` array. ``eq=False``: instances compare by identity
    (value comparison of a masked array is ambiguous).
    """
    # Data values: shape (bands, height, width) for raster, or (bands, n) for points
    data: np.ma.MaskedArray

    # Spatial info
    bounds: tuple[float, float, float, float]  # (west, south, east, north)
    crs: rasterio.CRS
    geometry: BaseGeometry | None = None  # For non-grid domains

    # Band/variable metadata; resolved to one entry per band at construction
    bands: tuple[BandInfo, ...] = ()

    # Temporal info (optional)
    timestamps: tuple[str, ...] | None = None  # ISO 8601 / RFC 3339 strings

    # Source identification
    collection_id: str | None = None
    item_ids: tuple[str, ...] | None = None
```

`__post_init__` validates only what is domain-independent: `data` must be 2-D or
3-D, and a non-empty `bands` must have one entry per `data.shape[0]`.
Domain-dependent consistency (geometry vs. timestamps vs. array shape) is
deferred to the modeler -- see Section 7 for the planned evolution that removes
this split.

`__post_init__` also **resolves `bands` once, at construction**: when `bands` is
empty it synthesizes one `BandInfo` per leading-axis band (`b1, b2, ...`,
matching rio-tiler's default band naming), assigning through
`object.__setattr__` because the dataclass is frozen. So `bands` is never empty
afterwards and every consumer reads a populated tuple -- the default-band naming
convention lives here, in one place, rather than being duplicated in the modeler.

This intentionally erases the distinction between "the caller supplied no band
metadata" and "the caller supplied bands". That is consistent with
`CoverageInput`'s role as the *post-resolution* representation: all precedence
and enrichment (explicit `bands` > per-attribute kwargs > the reader's own
`band_names`) is resolved upstream in the converters (Section 5), which still
hold the raw reader info. The realistic consumers of "were bands supplied?" --
e.g., a strict mode that rejects placeholder parameters, or metadata enrichment
from a STAC item's `eo:bands` -- all live at that converter/endpoint layer,
where the signal is still available; none need it on `CoverageInput`. If such a
need ever does reach this layer, the clean fix is an explicit converter flag or
a `bands_supplied` field, not reconstructing intent from a resolved
`CoverageInput`.

### 3.1 Single-array, data-cube constraint

`CoverageInput.data` is a single masked array with a leading band axis:
bands share one shape, one dtype, and one spatial/temporal footprint. This
follows the data-cube model and lets endpoint code pass `ImageData.array`
through as-is while keeping the modeler's range-creation path shape-driven
rather than per-variable.

It is a deliberate narrowing relative to a per-variable container where each
band could carry its own shape, dtype, and axis names. Consequences to be
accepted as constraints for later stories:

- **Story 9 (time series across STAC items)** must align all selected
  assets onto the same cube. Items contributing different shapes, dtypes,
  or temporal coverages have to be resampled, padded, or rejected at the
  endpoint -- they cannot be packed into one `CoverageInput`. Mixed-dtype
  responses (e.g., `displacement: float32` + `coherence: uint8`) require
  either an upcast or splitting into multiple coverages.
- **Story 11 (router integration)** inherits the same shape: one `Coverage`
  per response covers one cube; heterogeneous variables go into a
  `CoverageCollection`, one coverage per dtype/shape group.

If either case starts demanding genuinely heterogeneous bands, the natural
follow-up is to let `CoverageInput` hold a `Sequence[BandData]` (each owning
its own array and dtype) rather than a single `(bands, ...)` array. Defer
this until a concrete endpoint requires it; the Section 7 union refactor is
independent and addresses domain shape, not band heterogeneity.

## 4. Modeler

The modeler is a set of **stateless module-level functions** in `modeler.py`,
not a class. The conversion holds no state and depends only on the neutral
`CoverageInput`, so a class would add ceremony without benefit. (If
configuration ever needs to be threaded through -- e.g., a `TiledNdArray` size
threshold in Story 12 -- introduce a function argument or a small frozen config
object rather than reviving a stateful class.)

The public entry point is `to_coverage`:

```python
def to_coverage(coverage_input: CoverageInput) -> Coverage:
    match coverage_input:
        case GridInput():
            return Coverage(
                domain=_create_grid_domain(coverage_input),
                parameters=_create_parameters(coverage_input),
                ranges=_create_grid_ranges(coverage_input),
            )
        case _:
            assert_never(coverage_input)
```

**Current status (Grid only).** The per-domain input union (Section 7) has
landed for the Grid variant (#22): `to_coverage` dispatches on the variant via
`match` with `assert_never` (from the standard library `typing`), and
`GridInput` enforces its own 3-D shape
contract at construction, so the old `geometry`/`ndim` `NotImplementedError`
guards are gone. Point and PointSeries variants follow in #23, and
`to_coverage_collection` in #24; until #23, the alias `CoverageInput =
GridInput` has a single member.

`to_coverage_collection` is its planned sibling for multi-result responses (**not
yet implemented**): build one coverage per input, then hoist the shared
`parameters` and `referencing` to the collection level and clear them on the
member coverages.

```python
def to_coverage_collection(inputs: list[CoverageInput]) -> CoverageCollection:
    parameters = _create_parameters(inputs[0])
    references = _get_references(inputs[0])
    coverages = []
    for coverage_input in inputs:
        cov = to_coverage(coverage_input)
        cov.parameters = {}  # Hoisted to collection level
        coverages.append(cov)
    return CoverageCollection(
        coverages=coverages, parameters=parameters, referencing=references
    )
```

### 4.1 Domain Type Detection

> **Status**: superseded and partially implemented. As of #22 the modeler
> dispatches on explicit per-domain variants (Section 7), not on inferred domain
> type; `_get_domain_type()` is not implemented and will not be. The sketch
> below is retained only for the design rationale (and the Polygon discussion
> that follows).

```python
def _get_domain_type(self, input: CoverageInput) -> DomainType:
    has_time = input.timestamps is not None and len(input.timestamps) > 0
    if input.geometry is None:
        return DomainType.grid  # Raster data -> Grid
    geom_type = input.geometry.geom_type
    mapping = {
        "Point":      (DomainType.point_series, DomainType.point),
        "Polygon":    (DomainType.polygon_series, DomainType.polygon),
        "MultiPoint": (DomainType.multi_point_series, DomainType.multi_point),
        "LineString": (DomainType.trajectory, DomainType.trajectory),
    }
    if geom_type in mapping:
        return mapping[geom_type][0] if has_time else mapping[geom_type][1]
    raise ValueError(f"Unsupported geometry type: {geom_type}")
```

> **NOTE on the former Polygon workaround (resolved)**: the no-timestamps
> `"Polygon" -> polygon` mapping above was not implementable against
> `covjson-pydantic` 0.7.0 -- that release (with the CoverageJSON spec)
> required a `t` axis on PolygonSeries, the only polygon domain it offered, so
> a polygon without time had nowhere valid to go. The standalone Polygon domain
> type landed upstream in
> [KNMI/covjson-pydantic#30](https://github.com/KNMI/covjson-pydantic/pull/30),
> released in `covjson-pydantic` 0.8.0; the dependency pin is now
> `covjson-pydantic>=0.8.0`, so the mapping is simply
> `"Polygon": (polygon_series, polygon)` -- no workaround, no
> `NotImplementedError`. (`MultiPolygon`, `MultiPolygonSeries`, and `Section`
> remain absent from the upstream `DomainType` enum.) Polygon support is only
> needed by Story 5 (`format=aggregated`) and Story 9, so this never blocked
> the modeler (Story 3) or the first endpoints.

### 4.2 Axis Creation

| Domain Type | Axes Produced |
| --- | --- |
| Grid | `x`/`y` `CompactAxis` of cell *centers*, inset half a cell from the bounds edges: `x` runs `west + dx/2 .. east - dx/2` (`dx = (east-west)/w`), `y` runs `north + dy/2 .. south - dy/2` (`dy = (south-north)/h`) |
| Point / PointSeries | `x: ValuesAxis[float]`, `y: ValuesAxis[float]`, optionally `z`, optionally `t` |
| MultiPoint | `composite: ValuesAxis[Tuple]` |
| Polygon / PolygonSeries | `composite: ValuesAxis` with polygon rings, optionally `t` |
| Trajectory | `composite: ValuesAxis[Tuple]` with sampled `[lon, lat]` |

## 5. TiTiler Integration Points

### 5.1 Converting rio-tiler ImageData to CoverageInput

> **Status (#22).** The converter now returns `GridInput` and no longer accepts
> `geometry` or `timestamps` (Section 7.5). The signature sketch and the
> `geometry` rationale below describe the original design; treat them as history.

Implemented in `src/titiler_covjson/input.py` (Story 2):

```python
def imagedata_to_coverage_input(
    img: ImageData,
    *,
    bands: Sequence[BandInfo] | None = None,
    band_names: Sequence[str] | None = None,
    band_descriptions: Sequence[str] | None = None,
    band_units: Sequence[str] | None = None,
    crs: rasterio.CRS | None = None,
    geometry: BaseGeometry | None = None,
    timestamps: Sequence[str] | None = None,
    collection_id: str | None = None,
    item_ids: Sequence[str] | None = None,
) -> CoverageInput: ...
```

Key behaviors (which differ from earlier drafts of this document, written
against a pre-7.x rio-tiler API):

- **Mask propagation is a pass-through.** rio-tiler ≥ 4 stores
  `ImageData.array` as a 3-D `(bands, height, width)` masked array with
  nodata already encoded in the mask; no mask inversion or `np.ma.array`
  reconstruction is needed.
- **CRS comes from the image, not a hardcoded default.** `img.crs` is used,
  overridable via the `crs=` kwarg; if neither is set, `ValueError` is
  raised rather than silently assuming WGS84 (incorrect-CRS output is a key
  risk in the roadmap).
- **Band metadata precedence**: explicit `bands=` list > per-attribute
  kwargs (`band_names`, `band_descriptions`, `band_units`) > the image's own
  `band_names` (rio-tiler defaults these to `["b1", ...]`).
- **Reader-level metadata** (descriptions, units, nodata values) lives on
  `Reader.info()`, not `ImageData`; use the companion helper:
  `imagedata_to_coverage_input(img, bands=band_info_from_reader_info(reader.info()))`.

#### Why the signature grew beyond the original sketch

Earlier drafts of this section proposed only `band_names`,
`band_descriptions`, `band_units`, `timestamps`, and `collection_id`. The
implementation added four parameters, each for a distinct reason:

- **`bands`**: the per-attribute string lists provide no route for
  reader-level metadata -- in particular, `BandInfo.dtype` cannot be
  expressed through them at all. Accepting complete `BandInfo` sequences is
  what lets `band_info_from_reader_info(reader.info())` compose with the
  converter, fulfilling the Story 2 task "band metadata extraction from
  reader info".
- **`crs`**: the sketch hardcoded EPSG:4326 and so needed no parameter; that
  contradicted both the Story 2 task "handle CRS extraction" and the
  roadmap's incorrect-CRS risk. With CRS extracted from `img.crs` and a
  `ValueError` raised when absent, the `crs=` kwarg is the explicit escape
  hatch for images that legitimately lack one.
- **`item_ids`**: an oversight in the sketch -- Section 3 declares both
  provenance fields (`collection_id` and `item_ids`) but the sketch's
  converter passed through only the first.
- **`geometry`**: completeness, so every `CoverageInput` field is reachable
  through the converter rather than requiring direct dataclass construction
  (e.g., a time-series flow that reads an `ImageData` but aggregates over a
  polygon).

### 5.2 Point Value Extraction

```python
def point_to_coverage_input(url: str, lon: float, lat: float, ...) -> CoverageInput:
    """Extract point value and wrap as CoverageInput with Point geometry."""
```

### 5.3 Transect / Line Profile Extraction

```python
def transect_to_coverage_input(url: str, line: LineString, resolution: float, ...) -> CoverageInput:
    """Sample values along a line at given resolution."""
```

## 6. Conversion Logic Summary

| Input | Domain Type | Axes | Range Shape | Notes |
| --- | --- | --- | --- | --- |
| Raster tile/bbox | Grid | x(start,stop,num), y(start,stop,num) | [height, width] | Most common case |
| Point value | Point | x(values), y(values) | [1] | Single pixel lookup |
| Point + timestamps | PointSeries | x, y, t(values) | [n_times] | Time series at a location |
| Bbox aggregated | PolygonSeries | composite(polygon) | [1] | Mean/median over area |
| Bbox + timestamps | PolygonSeries | composite(polygon), t(values) | [n_times] | Aggregated time series |
| Line profile | Trajectory | composite(tuple) | [n_samples] | Sampled along transect |
| Multi-item collection | Grid + t | x, y, t(values) | [n_times, height, width] | Temporal stack |

## 7. Planned Evolution: Per-Domain Input Union

> **Status**: in progress. The union has landed for the Grid variant (#22);
> Point/PointSeries join in #23 and `to_coverage_collection` in #24. Only the
> variants needed so far are built (Grid now; Point/PointSeries next) -- the
> remaining variants in the Section 7.2 sketch (GridSeries, MultiPoint,
> Trajectory, PolygonSeries) stay a forward design until their stories land,
> when `assert_never` plus mypy will force each to be handled.

### 7.1 Motivation

This refactor addresses **domain shape** (Grid vs Point vs Trajectory vs
...); it is orthogonal to the single-array, data-cube constraint in Section 3.1.
The two can be decided independently: per-domain variants do not change band
semantics, and moving to a `Sequence[BandData]` later would not affect the
variant split.

The single `CoverageInput` class (Section 3) works well for the first endpoints but
has structural problems that grow with each domain type:

1. **Required-but-meaningless fields.** `bounds` is mandatory, yet a Point
   query has no extent -- Story 4 would have to use degenerate bounds to
   to satisfy the constructor. Conversely, `geometry` is dead weight
   for grids.
2. **One shape rule cannot cover the design's own cases.** Section 6 includes the
   multi-item temporal stack: "Grid + t" with range shape
   `[n_times, height, width]`, i.e., data shaped
   `(bands, n_times, height, width)`. `CoverageInput.__post_init__` rejects
   4-D arrays, and loosening it to "2-D, 3-D, or 4-D depending on context"
   means the invariant is no longer checkable where the data lives.
3. **Domain inference is lossy and error-prone.** `_get_domain_type()`
   (Section 4.1) guesses the domain from `geometry` + `timestamps` + `shape`, so a
   caller who accidentally attaches a geometry silently flips a Grid coverage
   into a Point coverage -- producing structurally valid but wrong CovJSON that
   only a client discovers.
4. **No exhaustiveness checking.** Adding a domain type to a single-class
   design means finding every `if`/`elif` in the modeler by hand.

A discriminated union solves all four: each variant declares exactly the
fields its domain needs, validates its own shape contract locally in
`__post_init__`, is selected *explicitly* by the endpoint (no inference),
and lets mypy enforce exhaustive handling in the modeler.

### 7.2 Sketch

Shared fields move to a base; `kw_only=True` lets subclasses add required fields
after inherited defaults:

```python
from dataclasses import dataclass
from shapely.geometry import LineString, MultiPoint, Point, Polygon

@dataclass(frozen=True, eq=False, kw_only=True)
class _CoverageInputBase:
    data: np.ma.MaskedArray  # leading axis is always bands
    crs: rasterio.CRS
    bands: tuple[BandInfo, ...] = ()
    collection_id: str | None = None
    item_ids: tuple[str, ...] | None = None

@dataclass(frozen=True, eq=False, kw_only=True)
class GridInput(_CoverageInputBase):
    """Grid domain. data: (bands, height, width)."""
    bounds: tuple[float, float, float, float]

@dataclass(frozen=True, eq=False, kw_only=True)
class GridSeriesInput(_CoverageInputBase):
    """Grid + t domain (temporal stack). data: (bands, n_times, height, width)."""
    bounds: tuple[float, float, float, float]
    timestamps: tuple[str, ...]  # len == data.shape[1]

@dataclass(frozen=True, eq=False, kw_only=True)
class PointInput(_CoverageInputBase):
    """Point domain. data: (bands, 1)."""
    geometry: Point

@dataclass(frozen=True, eq=False, kw_only=True)
class PointSeriesInput(_CoverageInputBase):
    """PointSeries domain. data: (bands, n_times)."""
    geometry: Point
    timestamps: tuple[str, ...]  # len == data.shape[1]

@dataclass(frozen=True, eq=False, kw_only=True)
class MultiPointInput(_CoverageInputBase):
    """MultiPoint(Series) domain. data: (bands, n_points) or (bands, n_times)."""
    geometry: MultiPoint
    timestamps: tuple[str, ...] | None = None

@dataclass(frozen=True, eq=False, kw_only=True)
class TrajectoryInput(_CoverageInputBase):
    """Trajectory domain. data: (bands, n_samples), one sample per line vertex."""
    geometry: LineString
    timestamps: tuple[str, ...] | None = None

@dataclass(frozen=True, eq=False, kw_only=True)
class PolygonSeriesInput(_CoverageInputBase):
    """PolygonSeries domain (also aggregated bbox without t). data: (bands, n)."""
    geometry: Polygon
    timestamps: tuple[str, ...] | None = None

CoverageInput = (
    GridInput
    | GridSeriesInput
    | PointInput
    | PointSeriesInput
    | MultiPointInput
    | TrajectoryInput
    | PolygonSeriesInput
)
```

Each variant's `__post_init__` enforces its own contract (e.g.
`PointSeriesInput`: `data.ndim == 2 and data.shape[1] == len(timestamps)`;
`TrajectoryInput`: `data.shape[1] == len(geometry.coords)`), so an invalid
combination fails at construction -- at the read site -- instead of deep in
the modeler.

### 7.3 Modeler dispatch

`_get_domain_type()` disappears; the modeler dispatches on the variant with
exhaustiveness checking:

```python
from typing import assert_never

def to_coverage(coverage_input: CoverageInput) -> Coverage:
    match coverage_input:
        case GridInput():
            ...
        case GridSeriesInput():
            ...
        case PointInput() | PointSeriesInput():
            ...
        case MultiPointInput():
            ...
        case TrajectoryInput():
            ...
        case PolygonSeriesInput():
            ...
        case _:
            assert_never(input)
```

Adding a future domain type then produces mypy errors at every dispatch site
that does not yet handle it.

### 7.4 Migration cost and trigger

The refactor is cheap because endpoints call converters, not constructors:
`imagedata_to_coverage_input()` changes its return type to `GridInput`, each
endpoint's converter (point, transect, timeseries) returns its own variant,
and only `input.py` plus modeler internals are touched.

**Trigger**: implement Story 3 against the current single `CoverageInput`
starting with Grid. Split into this union when adding the second or third
domain type -- i.e., as soon as `_get_domain_type()` / shape validation starts
accumulating domain-conditional branches -- with the real axis-creation
requirements in view rather than guessed in advance.

### 7.5 Implementation status and decisions

The union is delivered in three stacked PRs under #18:

- **#22 (this):** `_CoverageInputBase` + `GridInput`, `CoverageInput` as a
  one-member alias, `match` dispatch with `assert_never` exhaustiveness
  (from the standard library `typing`), and
  `imagedata_to_coverage_input()` returning `GridInput`. The converter's
  `geometry` and `timestamps` parameters are removed -- on the grid path a
  geometry only ever reached `NotImplementedError`, and timestamps would be a
  GridSeries (not built). This removes the "accidental geometry silently flips
  Grid to Point" footgun from Section 7.1.
- **#23:** `PointInput` and `PointSeriesInput`. Point/PointSeries axes are
  `x`/`y` value axes with an optional `z` derived from a 3-D shapely `Point`
  (`geometry.has_z`) and, for PointSeries, a `t` axis with temporal referencing.
  No vertical referencing system is created for `z`.
- **#24:** `to_coverage_collection(inputs)`, which hoists shared parameters and
  referencing to the collection level and **validates** that every input yields
  equal parameters and referencing, raising `ValueError` on mismatch.

Scope decision: only the three variants above are built now. GridSeries,
MultiPoint, Trajectory, and PolygonSeries remain the forward design in Section
7.2 and arrive with the stories that need them.
