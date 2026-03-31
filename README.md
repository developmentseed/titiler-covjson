# titiler-covjson

CoverageJSON output format and API extension for [TiTiler](https://developmentseed.org/titiler/).

## Overview

`titiler-covjson` adds [CoverageJSON](https://github.com/opengeospatial/CoverageJSON) (OGC Community Standard) as a new output format to TiTiler, enabling any TiTiler deployment to serve geospatial raster and time series data in a standards-compliant, interoperable JSON format.

## Features

- **Point query** - coverage values at a coordinate
- **Bounding box query** - full raster grid or aggregated statistics over an extent
- **Transect / line profile** - values sampled along a polyline
- **Tile query** - map tiles as CovJSON (alternative to image tiles)
- **Time series** - temporal extraction from STAC collections
- **Coverage metadata** - domain and parameter info without data
- **Overview** - low-resolution downsampled grids via COG overviews

## Installation

```bash
pip install titiler-covjson
```

## Dependencies

- [TiTiler](https://developmentseed.org/titiler/) >= 0.18.0
- [covjson-pydantic](https://github.com/KNMI/covjson-pydantic) >= 0.7.0 (Pydantic v2 CovJSON models by KNMI)
- [rio-tiler](https://cogeotiff.github.io/rio-tiler/) >= 7.0.0
- [Shapely](https://shapely.readthedocs.io/) >= 2.0

## Quick Start

```python
from fastapi import FastAPI
from titiler_covjson.router import covjson_router

app = FastAPI()
app.include_router(covjson_router, prefix="/covjson")
```

## Documentation

See the [`docs/`](docs/) directory:

1. [Design Overview](docs/01-design-overview.md) - Architecture and key decisions
2. [API Definition](docs/02-api-definition.md) - Endpoint specifications with examples
3. [Data Model Reference](docs/03-data-model-reference.md) - CovJSON models via covjson-pydantic
4. [Modeler/Converter Design](docs/04-modeler-converter-design.md) - Raster to CovJSON conversion
5. [Implementation Roadmap](docs/05-implementation-roadmap.md) - EPIC with 13 stories
6. [Libraries Analysis](docs/06-existing-libraries-analysis.md) - covjson-pydantic & covjson-validator

## Project Structure

```
titiler-covjson/
├── src/titiler_covjson/
│   ├── __init__.py       # Package init
│   ├── helpers.py        # CRS, unit, dtype mapping utilities
│   ├── input.py          # CoverageInput intermediate representation
│   ├── modeler.py        # RasterCovJSONModeler (data -> CovJSON)
│   ├── routes.py         # FastAPI endpoint definitions
│   └── router.py         # TiTiler router extension
├── tests/
├── docs/
├── pyproject.toml
├── LICENSE               # MIT
└── README.md
```

## License

MIT
