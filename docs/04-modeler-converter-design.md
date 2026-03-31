# Modeler / Converter Design

## 1. Overview

The **Modeler** is the layer that converts raster data (rio-tiler ImageData, numpy arrays, STAC metadata) into CovJSON model objects (via covjson-pydantic). It follows a clean separation of concerns:

```
rio-tiler data  -->  CoverageInput (intermediate)  -->  RasterCovJSONModeler  -->  covjson-pydantic Coverage
```

---

## 2. Conversion Flow

```
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

---

## 3. CoverageInput Data Class

The intermediate representation between TiTiler's data access and CovJSON serialization:

```python
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
from rasterio.crs import CRS
from shapely.geometry import BaseGeometry

@dataclass
class BandInfo:
    """Metadata for a single band/variable."""
    name: str
    description: str = ""
    unit: str = ""
    dtype: np.dtype = np.float32
    nodata: Optional[float] = None

@dataclass
class CoverageInput:
    """Intermediate data structure for CovJSON conversion.

    Decouples the modeler from rio-tiler specifics.
    """
    # Data values: shape (bands, height, width) for raster, or (bands, n) for points
    data: np.ma.MaskedArray

    # Spatial info
    bounds: tuple[float, float, float, float]  # (west, south, east, north)
    crs: CRS
    geometry: Optional[BaseGeometry] = None  # For non-grid domains

    # Band/variable metadata
    bands: list[BandInfo] = field(default_factory=list)

    # Temporal info (optional)
    timestamps: Optional[list[str]] = None  # ISO 8601 strings

    # Source identification
    collection_id: Optional[str] = None
    item_ids: Optional[list[str]] = None
```

---

## 4. RasterCovJSONModeler

```python
class RasterCovJSONModeler:
    """Converts raster data to CovJSON Coverage objects."""

    def to_coverage(self, input: CoverageInput) -> Coverage:
        domain = self._create_domain(input)
        parameters = self._create_parameters(input)
        ranges = self._create_ranges(input, domain)
        return Coverage(domain=domain, parameters=parameters, ranges=ranges)

    def to_coverage_collection(self, inputs: list[CoverageInput]) -> CoverageCollection:
        parameters = self._create_parameters(inputs[0])
        references = self._get_references(inputs[0])
        coverages = []
        for inp in inputs:
            cov = self.to_coverage(inp)
            cov.parameters = {}  # Hoisted to collection level
            coverages.append(cov)
        return CoverageCollection(
            coverages=coverages, parameters=parameters, referencing=references
        )
```

### 4.1 Domain Type Detection

```python
def _get_domain_type(self, input: CoverageInput) -> DomainType:
    has_time = input.timestamps is not None and len(input.timestamps) > 0
    if input.geometry is None:
        return DomainType.grid  # Raster data -> Grid
    geom_type = input.geometry.geom_type
    mapping = {
        "Point":      (DomainType.point_series, DomainType.point),
        "Polygon":    (DomainType.polygon_series, DomainType.polygon_series),  # workaround
        "MultiPoint": (DomainType.multi_point_series, DomainType.multi_point),
        "LineString": (DomainType.trajectory, DomainType.trajectory),
    }
    if geom_type in mapping:
        return mapping[geom_type][0] if has_time else mapping[geom_type][1]
    raise ValueError(f"Unsupported geometry type: {geom_type}")
```

### 4.2 Axis Creation

| Domain Type | Axes Produced |
|---|---|
| Grid | `x: CompactAxis(start=west, stop=east, num=w)`, `y: CompactAxis(start=north, stop=south, num=h)` |
| Point / PointSeries | `x: ValuesAxis[float]`, `y: ValuesAxis[float]`, optionally `z`, optionally `t` |
| MultiPoint | `composite: ValuesAxis[Tuple]` |
| Polygon / PolygonSeries | `composite: ValuesAxis` with polygon rings, optionally `t` |
| Trajectory | `composite: ValuesAxis[Tuple]` with sampled `[lon, lat]` |

---

## 5. TiTiler Integration Points

### 5.1 Converting rio-tiler ImageData to CoverageInput

```python
from rio_tiler.models import ImageData

def imagedata_to_coverage_input(
    img: ImageData,
    band_names: list[str] | None = None,
    band_descriptions: list[str] | None = None,
    band_units: list[str] | None = None,
    timestamps: list[str] | None = None,
    collection_id: str | None = None,
) -> CoverageInput:
    """Convert TiTiler ImageData to CoverageInput."""
    n_bands = img.data.shape[0]
    names = band_names or [f"band_{i+1}" for i in range(n_bands)]
    descriptions = band_descriptions or [""] * n_bands
    units = band_units or [""] * n_bands

    bands = [
        BandInfo(name=names[i], description=descriptions[i], unit=units[i], dtype=img.data.dtype)
        for i in range(n_bands)
    ]

    masked_data = np.ma.array(img.data, mask=~img.mask.astype(bool))

    return CoverageInput(
        data=masked_data,
        bounds=img.bounds,
        crs=CRS.from_epsg(4326),  # TiTiler reprojects to 4326
        bands=bands,
        timestamps=timestamps,
        collection_id=collection_id,
    )
```

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

---

## 6. Conversion Logic Summary

| Input | Domain Type | Axes | Range Shape | Notes |
|---|---|---|---|---|
| Raster tile/bbox | Grid | x(start,stop,num), y(start,stop,num) | [height, width] | Most common case |
| Point value | Point | x(values), y(values) | [1] | Single pixel lookup |
| Point + timestamps | PointSeries | x, y, t(values) | [n_times] | Time series at a location |
| Bbox aggregated | PolygonSeries | composite(polygon) | [1] | Mean/median over area |
| Bbox + timestamps | PolygonSeries | composite(polygon), t(values) | [n_times] | Aggregated time series |
| Line profile | Trajectory | composite(tuple) | [n_samples] | Sampled along transect |
| Multi-item collection | Grid + t | x, y, t(values) | [n_times, height, width] | Temporal stack |
