# titiler-covjson

CoverageJSON output format and API extension for
[TiTiler](https://developmentseed.org/titiler/).

## Overview

`titiler-covjson` adds
[CoverageJSON](https://github.com/opengeospatial/CoverageJSON) (an Open
Geospatial Consortium (OGC) Community Standard, 21-069r2) as an output format
for TiTiler, so a TiTiler deployment can serve raster data as
standards-compliant, interoperable CoverageJSON. It is a FastAPI router
extension (a `titiler.core` factory), not a standalone service.

## Status

Early alpha. The first end-to-end slice is implemented: a single bounding-box
endpoint returning a 2-D Grid-domain coverage from one dataset.

- **Available:** `GET {prefix}/bbox/{minx},{miny},{maxx},{maxy}`, returning a
  Grid coverage with band selection (`bidx` / `expression` / the OGC API -
  Environmental Data Retrieval (EDR) `parameter-name` alias), a single `crs`
  knob, output sizing, and a cell-count ceiling.
- **Planned:** point and transect queries, temporal (time series) extraction,
  further coverage domains, and multi-dataset / SpatioTemporal Asset Catalog
  (STAC) sources.

## Installation

```bash
pip install titiler-covjson
```

Requires Python >= 3.11.

## Quick start

Mount the factory's router on a FastAPI application, and install TiTiler's
exception handlers so that reader and dataset errors render as JSON responses
with the correct status codes:

```python
from fastapi import FastAPI
from titiler.core.errors import DEFAULT_STATUS_CODES, add_exception_handlers

from titiler_covjson import CovJSONFactory

app = FastAPI()
app.include_router(CovJSONFactory().router)
add_exception_handlers(app, DEFAULT_STATUS_CODES)
```

Then request a coverage for a bounding box (the four bounds are one
comma-delimited path segment, interpreted in CRS84 by default):

```bash
curl "http://localhost:8000/bbox/-10,-5,10,5?url=/path/to/cog.tif"
```

## Dependencies

- [TiTiler](https://developmentseed.org/titiler/) (`titiler.core`) >= 2.0, < 3.0
- [rio-tiler](https://cogeotiff.github.io/rio-tiler/) >= 9.0, < 10.0
- [covjson-pydantic](https://github.com/KNMI/covjson-pydantic) >= 0.8.0, < 1.0
  (Pydantic v2 CoverageJSON models by KNMI)
- [Shapely](https://shapely.readthedocs.io/) >= 2.0
- [NumPy](https://numpy.org/) >= 2.2.6
- [ucumvert](https://github.com/dalito/ucumvert) >= 0.2.2 (UCUM (Unified Code
  for Units of Measure) parsing)
- [pyproj](https://pyproj4.github.io/pyproj/) >= 3.0, < 4.0

## Documentation

See the [`docs/`](docs/) directory. The current-direction documents are the
bounding-box endpoint spec and the architecture decision records; earlier design
documents predate the implementation and carry a superseded note where relevant.

- [Bounding-box endpoint spec](docs/08-bbox-endpoint-spec.md): the implemented
  `/bbox` Grid endpoint
- [Architecture decision records](docs/adr/): cross-cutting decisions
- Background and earlier design exploration:
  [design overview](docs/01-design-overview.md),
  [API definition](docs/02-api-definition.md),
  [data model reference](docs/03-data-model-reference.md),
  [modeler/converter design](docs/04-modeler-converter-design.md),
  [implementation roadmap](docs/05-implementation-roadmap.md),
  [libraries analysis](docs/06-existing-libraries-analysis.md),
  [API design alternatives](docs/07-api-design-alternatives.md)

## Project structure

```text
titiler-covjson/
├── src/titiler_covjson/
│   ├── __init__.py       # public API: CovJSONFactory, CovJSONResponse, media type
│   ├── factory.py        # CovJSONFactory(BaseFactory): the /bbox route
│   ├── dependencies.py   # CovJSONBandParams, validate_covjson_format
│   ├── responses.py      # CovJSONResponse + COVJSON_MEDIA_TYPE
│   ├── input.py          # CoverageInput intermediate representation
│   ├── modeler.py        # to_coverage: CoverageInput -> covjson-pydantic models
│   └── helpers.py        # CRS, unit, and dtype mapping utilities
├── tests/
├── docs/
├── pyproject.toml
├── LICENSE               # MIT
└── README.md
```

## License

MIT
