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

Early alpha. Three value-returning endpoints are implemented over a single
dataset, each returning a CoverageJSON coverage in its own domain.

- **Available:**
  - `GET {prefix}/bbox/{minx},{miny},{maxx},{maxy}`: a 2-D Grid coverage for a
    bounding box, with output sizing and a cell-count ceiling.
  - `GET {prefix}/position?coords=POINT(x y)` (or
    `MULTIPOINT((x y), ...)`): a Point coverage sampling a single location, or a
    MultiPoint coverage sampling each position (a position outside the dataset
    becomes `null`, not an error). The number of positions is capped by
    `max_samples`.
  - `GET {prefix}/area?coords=POLYGON((...))`: a Polygon coverage reducing the
    dataset over a polygon to one value per band by a `stat` (default `mean`).
    The reduction is an unweighted, all-touched pixel statistic: a pixel the
    polygon boundary merely grazes counts the same as one wholly inside, and
    `std` is the population standard deviation. Expect results to diverge from
    an area-weighted zonal statistic for polygons only a few pixels across.

  All three support band selection (`bidx` / `expression` / the OGC API -
  Environmental Data Retrieval (EDR) `parameter-name` alias) and a single `crs`
  knob.
- **Planned:** transect (trajectory), temporal (time series) extraction, further
  coverage domains, and multi-dataset / STAC sources.

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

Then request a coverage. A bounding box returns a Grid (the four bounds are one
comma-delimited path segment, interpreted in CRS84 by default), a `POINT`
returns a Point and a `MULTIPOINT` a MultiPoint, and a polygon returns a Polygon
reduced to one value per band (WKT whitespace is percent-encoded as `%20`):

```bash
curl "http://localhost:8000/bbox/-10,-5,10,5?url=/path/to/cog.tif"
curl "http://localhost:8000/position?coords=POINT(0%200)&url=/path/to/cog.tif"
curl "http://localhost:8000/position?coords=MULTIPOINT((0%200),(1%201))&url=/path/to/cog.tif"
curl "http://localhost:8000/area?coords=POLYGON((-10%20-5,10%20-5,10%205,-10%205,-10%20-5))&url=/path/to/cog.tif"
curl "http://localhost:8000/area?coords=POLYGON((-10%20-5,10%20-5,10%205,-10%205,-10%20-5))&url=/path/to/cog.tif&stat=std"
```

## Overriding the dataset error status

A failure to open or read the dataset `url` (a missing or unreadable file, an
unreachable or forbidden remote) surfaces as HTTP `500` by default. That is the
conservative choice for an untrusted, multi-tenant deployment: it never blames
the caller for a server-side outage, and it never reveals whether an internal
path the server can reach (but the caller cannot) exists. See
[ADR-0003](docs/adr/0003-dataset-open-read-error-status.md) for the full
rationale.

A single-tenant or otherwise trusted deployment (where the caller's access
scope matches the server's, so that existence leak is moot) can remap the
failure to a client error by extending TiTiler's status-code map:

```python
from rasterio.errors import RasterioIOError
from starlette import status
from titiler.core.errors import DEFAULT_STATUS_CODES, add_exception_handlers

add_exception_handlers(
    app,
    {**DEFAULT_STATUS_CODES, RasterioIOError: status.HTTP_400_BAD_REQUEST},
)
```

## Run it with Docker

A small demo container serves the `/bbox`, `/position`, and `/area` endpoints
against a bundled sample Cloud-Optimized GeoTIFF (COG), so you can try the
format end to end without building your own application.

> **Local demo only.** This container serves an open API that reads whatever
> `url` you pass it, with no authentication. Run it on your own machine for
> testing and confirmation; do not expose it publicly or on a shared network.

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
curl "http://localhost:8000/position?coords=POINT(0%200)&url=/data/sample.tif"
curl "http://localhost:8000/position?coords=MULTIPOINT((0%200),(1%201))&url=/data/sample.tif"
curl "http://localhost:8000/area?coords=POLYGON((-10%20-5,10%20-5,10%205,-10%205,-10%20-5))&url=/data/sample.tif"
curl "http://localhost:8000/area?coords=POLYGON((-10%20-5,10%20-5,10%205,-10%205,-10%20-5))&url=/data/sample.tif&stat=std"
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
  [implementation roadmap](docs/05-implementation-roadmap.md) (retired),
  [libraries analysis](docs/06-existing-libraries-analysis.md),
  [API design alternatives](docs/07-api-design-alternatives.md)

## Project structure

```text
titiler-covjson/
├── src/titiler_covjson/
│   ├── __init__.py       # public API: CovJSONFactory, CovJSONResponse, media type
│   ├── factory.py        # CovJSONFactory(BaseFactory): /bbox, /position, /area
│   ├── dependencies.py   # CovJSONBandParams, validate_covjson_format, area_stat
│   ├── responses.py      # CovJSONResponse + COVJSON_MEDIA_TYPE
│   ├── input.py          # CoverageInput intermediate representation
│   ├── modeler.py        # to_coverage: CoverageInput -> covjson-pydantic models
│   ├── reduce.py         # Stat + reduce_bands: per-band zonal reduction
│   └── helpers.py        # CRS, unit, and dtype mapping utilities
├── tests/
├── docs/
├── pyproject.toml
├── LICENSE               # MIT
└── README.md
```

## License

MIT
