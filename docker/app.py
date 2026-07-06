"""Demo application serving the /bbox CoverageJSON endpoint from one dataset.

Mounts the CoverageJSON factory and titiler's exception handlers so a raster can
be read as a CoverageJSON Grid over HTTP. This exists only to exercise the
endpoint by hand and in continuous integration.

WARNING: strictly for local testing and confirmation. The endpoint opens
whatever ``url`` query value it is given (rio-tiler reads arbitrary paths and
URLs), so this app is an unauthenticated arbitrary-file-read and server-side
request forgery surface. Never expose it publicly or on a shared network.
"""

from fastapi import FastAPI
from titiler.core.errors import DEFAULT_STATUS_CODES, add_exception_handlers

from titiler_covjson.factory import CovJSONFactory

app = FastAPI(
    title="titiler-covjson demo",
    description="Local demo: GET /bbox -> CoverageJSON. Not for public use.",
)

# Mount the CovJSON factory's routes (GET /bbox/{minx},{miny},{maxx},{maxy}).
# NOTE: this accepts an arbitrary ?url= -- local demo only (see module docstring).
app.include_router(CovJSONFactory().router)

# titiler's handlers render rio-tiler, rasterio, and BadRequestError failures as
# JSON responses with the correct status codes (e.g., 400 for bad input).
add_exception_handlers(app, DEFAULT_STATUS_CODES)
