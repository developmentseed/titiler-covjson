"""HTTP response class and media type for CoverageJSON output."""

from __future__ import annotations

from starlette.responses import Response

COVJSON_MEDIA_TYPE = "application/prs.coverage+json"
"""Registered IANA media type for CoverageJSON (OGC Community Standard 21-069r2)."""


class CovJSONResponse(Response):
    """A ``Response`` whose media type is ``application/prs.coverage+json``."""

    media_type = COVJSON_MEDIA_TYPE
