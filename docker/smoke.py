"""End-to-end smoke check for the /bbox CoverageJSON container.

Waits for the demo server, requests a CoverageJSON Grid for the bundled sample
Cloud-Optimized GeoTIFF (COG), and asserts the response is a valid, non-empty
CoverageJSON document. Runs on the host (via ``uv run``) so the image carries no
test dependencies. Exits non-zero on any failure.

Run against a container published on ``localhost:8000``:
``uv run python docker/smoke.py``.
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPResponse
from math import prod
from pathlib import Path
from typing import Any, cast

import jsonschema

BASE_URL = "http://localhost:8000"
COG_URL = "/data/sample.tif"  # the sample COG baked into the image
BBOX = "-10,-5,10,5"
MEDIA_TYPE = "application/prs.coverage+json"
READINESS_TIMEOUT_S = 30.0
# The vendored CoverageJSON JSON Schema, read by path: this script deliberately
# does not import test modules or covjson-pydantic; it validates raw JSON exactly
# as the test suite does.
SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "schemas"
    / "coveragejson.json"
)


def main() -> int:
    """Request the demo endpoint and assert a valid, non-empty CoverageJSON.

    Returns:
        Process exit code: 0 when every check passes, 1 otherwise.
    """
    query = urllib.parse.urlencode({"url": COG_URL, "f": "CoverageJSON"})
    url = f"{BASE_URL}/bbox/{BBOX}?{query}"
    response = _get_when_ready(url, READINESS_TIMEOUT_S)
    content_type = response.headers.get_content_type()
    body = json.loads(response.read())

    if problems := [
        *_envelope_problems(response.status, content_type),
        *_content_problems(body),
    ]:
        print("smoke check FAILED:", file=sys.stderr)

        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)

        return 1

    print("smoke check passed: valid, non-empty CoverageJSON Grid")

    return 0


def _get_when_ready(
    url: str, timeout_s: float
) -> HTTPResponse | urllib.error.HTTPError:
    """GET ``url``, retrying while the server is still coming up.

    A transport error (connection refused, reset, or timed out) means the
    container is still booting, so retry until ``timeout_s`` elapses. An HTTP
    error status means the server answered, so it is returned for assertion
    rather than retried.

    Args:
        url: The absolute request URL.
        timeout_s: Total seconds to keep retrying connection failures.

    Returns:
        The HTTP response, or an ``HTTPError`` (which also carries ``status`` and
        ``headers``) to assert on.

    Raises:
        SystemExit: If the server never accepts a connection before the timeout.
    """
    deadline = time.monotonic() + timeout_s
    last_error: OSError | None = None

    while time.monotonic() < deadline:
        try:
            return cast(HTTPResponse, urllib.request.urlopen(url))
        except urllib.error.HTTPError as http_error:
            return http_error
        except OSError as transport_error:
            # refused / reset / timed out: the server is still coming up.
            last_error = transport_error
            time.sleep(0.5)

    print(f"server not ready after {timeout_s}s: {last_error}", file=sys.stderr)
    raise SystemExit(1)


def _envelope_problems(status: int | None, content_type: str) -> list[str]:
    """Check the HTTP status and content type.

    Args:
        status: The response status code (``None`` if the response exposes none).
        content_type: The response content type (no charset parameter).

    Returns:
        Mismatch descriptions; empty when both are correct.
    """
    problems: list[str] = []

    if status != 200:
        problems.append(f"status {status} != 200")

    if content_type != MEDIA_TYPE:
        problems.append(f"content-type {content_type!r} != {MEDIA_TYPE!r}")

    return problems


def _content_problems(body: dict[str, Any]) -> list[str]:
    """Check that real data flowed (a schema-valid response can still be hollow).

    Args:
        body: The parsed CoverageJSON response.

    Returns:
        Mismatch descriptions; empty when the document is valid and non-empty.
    """
    problems: list[str] = []

    try:
        jsonschema.validate(body, json.loads(SCHEMA_PATH.read_text()))
    except jsonschema.ValidationError as error:
        problems.append(f"schema invalid: {error.message}")

        return problems

    if body.get("type") != "Coverage":
        problems.append(f"type {body.get('type')!r} != 'Coverage'")

    if body.get("domain", {}).get("domainType") != "Grid":
        problems.append("domain.domainType != 'Grid'")

    ranges = body.get("ranges", {})

    if not ranges:
        problems.append("ranges is empty")

        return problems

    first = next(iter(ranges.values()))
    values = first.get("values", [])

    if all(value is None for value in values):
        problems.append("no non-null values (all-nodata or empty)")

    expected = prod(first.get("shape", [])) if first.get("shape") else -1

    if len(values) != expected:
        problems.append(f"values count {len(values)} != prod(shape) {expected}")

    return problems


if __name__ == "__main__":
    sys.exit(main())
