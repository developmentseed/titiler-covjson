"""Minimal FastAPI app for locally exercising the CoverageJSON endpoints.

Run it with uvicorn from the repository root, with hot reload on a fixed dev
port:

    uv run --with uvicorn uvicorn examples.dev_app:app --reload --port 8137

For a zero-setup request, point ``url`` at the bundled sample COG. It is a
relative path, resolved against the working directory the server runs from
(the repository root, as above), here against the /bbox endpoint:

    curl "http://127.0.0.1:8137/bbox/-10,-5,10,5?url=docker/data/sample.tif"

Or point ``url`` at any rio-tiler-readable dataset of your own: a local COG, or
an ``http(s)://`` / ``s3://`` asset href (e.g., one pulled from a STAC item):

    curl "http://127.0.0.1:8137/bbox/-10,-5,10,5?url=/path/to/your.tif&width=256&height=256"
"""

from fastapi import FastAPI
from titiler.core.errors import DEFAULT_STATUS_CODES, add_exception_handlers

from titiler_covjson import CovJSONFactory

# A containerized build of this same endpoint (a fixed, bundled sample dataset,
# exercised by the CI smoke test) lives in docker/app.py. Use that for a
# reproducible demo; use this for a local hot-reload dev loop against an
# arbitrary dataset. A VS Code launch configuration in .vscode/launch.json runs
# this app.
app = FastAPI(title="titiler-covjson dev")
app.include_router(CovJSONFactory().router)
add_exception_handlers(app, DEFAULT_STATUS_CODES)


@app.get("/")
def index() -> dict[str, str]:
    """Point at /docs for the full interactive API listing.

    Returns:
        dict[str, str]: A pointer to the docs plus one sample endpoint call.
    """
    return {
        "docs": "/docs",
        "example": "/bbox/-10,-5,10,5?url=<path-or-href>&width=256&height=256",
    }
