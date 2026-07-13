"""Minimal FastAPI app for locally exercising the CoverageJSON endpoints.

Mounts the whole ``CovJSONFactory`` router, so it serves every endpoint the
factory registers (``/bbox``, ``/position``, and any added later) — nothing here
is per-endpoint. Run it with uvicorn from the repository root, with hot reload on
a fixed dev port:

    uv run --with uvicorn uvicorn examples.dev_app:app --reload --port 8137

For a zero-setup request, point ``url`` at the bundled sample COG — the same
dataset the demo container serves, so a local dev loop and the container behave
alike. It is a relative path, resolved against the working directory the server
runs from (the repository root, as above):

    curl "http://127.0.0.1:8137/bbox/-10,-5,10,5?url=docker/data/sample.tif"
    curl "http://127.0.0.1:8137/position?coords=POINT(0%200)&url=docker/data/sample.tif"

Or point ``url`` at any rio-tiler-readable dataset of your own: a local COG, or
an ``http(s)://`` / ``s3://`` asset href (e.g., one pulled from a STAC item):

    curl "http://127.0.0.1:8137/bbox/-10,-5,10,5?url=/path/to/your.tif&width=256&height=256"

Hitting ``GET /`` lists the currently registered endpoints (see :func:`index`),
so newly added routes show up without editing this file; ``/docs`` has the full
interactive forms.

WARNING: strictly for local testing. The endpoint opens whatever ``url`` it is
given (rio-tiler reads arbitrary local paths and remote URLs), making this an
unauthenticated arbitrary-file-read and server-side request forgery surface.
Never expose it publicly or on a shared network.
"""

from fastapi import FastAPI
from fastapi.routing import APIRoute
from titiler.core.errors import DEFAULT_STATUS_CODES, add_exception_handlers

from titiler_covjson import CovJSONFactory

# The demo container (docker/app.py) mounts this same factory router against a
# fixed, bundled sample dataset and is exercised by the CI smoke test. Use that
# for a reproducible demo; use this for a local hot-reload dev loop against an
# arbitrary dataset. Both default to docker/data/sample.tif so they line up. A
# VS Code launch configuration in .vscode/launch.json runs this app.
app = FastAPI(title="titiler-covjson dev")
app.include_router(CovJSONFactory().router)
add_exception_handlers(app, DEFAULT_STATUS_CODES)

# The bundled sample the demo container serves; the ready-to-run default `url`.
SAMPLE_URL = "docker/data/sample.tif"


@app.get("/")
def index() -> dict[str, object]:
    """List the registered endpoints and a ready-to-run sample dataset.

    Derives the endpoint list from the app's own routes rather than hardcoding
    it, so any endpoint the factory adds appears here automatically. ``/docs``
    has the full interactive request forms.

    Returns:
        dict[str, object]: A pointer to ``/docs``, the registered endpoint path
            templates, and the bundled sample COG usable as ``url``.
    """
    endpoints = sorted(
        route.path
        for route in app.routes
        if isinstance(route, APIRoute) and "GET" in route.methods and route.path != "/"
    )

    return {"docs": "/docs", "endpoints": endpoints, "sample_url": SAMPLE_URL}
