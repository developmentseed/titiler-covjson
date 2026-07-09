# syntax=docker/dockerfile:1

# ---- builder: install the package + server into a venv ----
# The uv image is itself based on python:3.13-slim-bookworm, so the venv it
# builds is binary-compatible with the runtime stage below (same interpreter
# path), letting the runtime copy the venv wholesale.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

# copy link mode avoids hardlink-across-layer issues; bytecode trims cold start.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

# Dependency layer first (cached across source edits): lock + manifest only, no
# project install yet.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --group server --no-install-project

# Then the package sources + metadata files hatchling needs, and install the
# project itself. --no-editable: install into site-packages so the runtime stage
# needs only the venv, not src/ (an editable .pth -> /app/src would break at
# runtime).
COPY README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable --group server

# ---- runtime: minimal image with just the venv + app + sample COG ----
# Same Python minor AND Debian release (bookworm) as the builder base, so the
# copied venv's compiled extensions load.
#
# Base-image CVEs: this base carries some Critical/High CVEs in bookworm packages
# (perl, sqlite3, ncurses, and zlib's MiniZip zip writer) that Debian marks
# will_not_fix / fix_deferred, so no patched package exists: a rebuild or
# `apt upgrade` cannot clear them. They fall outside this demo's code path (a
# read-only raster -> CoverageJSON server never runs perl, opens SQLite, uses a
# terminal, or writes zip archives), and this container is local-demo-only, so
# they are accepted rather than chased.
FROM python:3.13-slim-bookworm AS runtime

# rasterio's wheels bundle GDAL but still dlopen a few base system libraries;
# libexpat1 is the one the slim image lacks (imports fail without it). This is a
# tiny runtime lib, not a full GDAL apt install.
# Not version-pinned (hadolint DL3008): libexpat1 is security-sensitive, so we
# take the latest patched build at image-build time rather than freeze a version
# (which Debian also drops from the repo over time, breaking future builds).
# hadolint ignore=DL3008
RUN <<EOF
set -e
apt-get update
apt-get install -y --no-install-recommends libexpat1
rm -rf /var/lib/apt/lists/*
EOF

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# app.py lands at /app/app.py and is served as "app:app" from WORKDIR /app (the
# CMD below); the sample COG is baked at /data/sample.tif.
COPY docker/app.py /app/app.py
COPY docker/data/sample.tif /data/sample.tif

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

# Exec form so uvicorn is PID 1 and receives SIGTERM on `docker stop`.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
