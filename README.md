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

## Run it with Docker

A small demo container serves the `/bbox` endpoint against a bundled sample
Cloud-Optimized GeoTIFF (COG), so you can try the format end to end without
building your own application.

> **Local demo only.** This container serves an open `/bbox` endpoint that reads
> whatever `url` you pass it, with no authentication. Run it on your own machine
> for testing and confirmation; do not expose it publicly or on a shared
> network.

Build the image from the repository root (the trailing `.` build context is
required, because the image installs the package from `src/`):

```bash
docker build -f docker/Dockerfile -t titiler-covjson-demo .
```

Run it:

```bash
docker run --rm -p 127.0.0.1:8000:8000 titiler-covjson-demo
```

Request a coverage for the bundled sample COG (the response is
`application/prs.coverage+json`; pipe to `jq` for readable output):

```bash
curl "http://localhost:8000/bbox/-10,-5,10,5?url=/data/sample.tif&f=CoverageJSON"
```

### Use your own COG

Mount the directory holding your raster read-only, at a path that does not
shadow the bundled `/data`, then point `url` at it. Keep the mount as narrow as
possible: the endpoint reads any path under it, so do not mount your home
directory or any tree that holds secrets.

```bash
docker run --rm -p 127.0.0.1:8000:8000 -v "/path/to/rasters:/host:ro" titiler-covjson-demo
curl "http://localhost:8000/bbox/<minx>,<miny>,<maxx>,<maxy>?url=/host/your.tif&f=CoverageJSON"
```

### The bundled sample

`docker/data/sample.tif` is a small, committed COG generated deterministically
by `docker/make_sample_cog.py`; reproduce it with:

```bash
uv run python docker/make_sample_cog.py
```

`docker/check_sample.py` guards the committed file against drifting from its
generator.

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
