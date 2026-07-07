"""Minimal FastAPI app for locally exercising the CoverageJSON /bbox endpoint.

Run it with uvicorn (see .vscode/launch.json for the VS Code entry point):

    uv run --with uvicorn uvicorn examples.dev_app:app --reload --port 8137

Then point ``url`` at any rio-tiler-readable dataset (a local COG, or an
``http(s)://`` / ``s3://`` asset href, e.g. one pulled from a STAC item):

    curl "http://127.0.0.1:8137/bbox/-10,-5,10,5?url=/path/to/your.tif&width=256&height=256"
"""

from fastapi import FastAPI
from titiler.core.errors import DEFAULT_STATUS_CODES, add_exception_handlers

from titiler_covjson import CovJSONFactory

app = FastAPI(title="titiler-covjson dev")
app.include_router(CovJSONFactory().router)
add_exception_handlers(app, DEFAULT_STATUS_CODES)


@app.get("/")
def index() -> dict[str, str]:
    """Point at /docs for the interactive API, or call /bbox with a ``url``.

    Returns:
        dict[str, str]: Hints for reaching the docs and the /bbox endpoint.
    """
    return {
        "docs": "/docs",
        "example": "/bbox/-10,-5,10,5?url=<path-or-href>&width=256&height=256",
    }
