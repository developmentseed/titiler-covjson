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
from collections.abc import Iterator
from http.client import HTTPResponse
from math import prod
from pathlib import Path
from typing import Any, cast

import jsonschema

BASE_URL = "http://localhost:8000"
COG_URL = "/data/sample.tif"  # the sample COG baked into the image
BBOX = "-10,-5,10,5"
BASE_BBOX_URL = f"{BASE_URL}/bbox/{BBOX}"
MEDIA_TYPE = "application/prs.coverage+json"
READINESS_TIMEOUT_S = 30.0
REQUEST_TIMEOUT_S = 10.0  # per-request socket timeout, so a hung server fails fast
# The baked sample is a fixed 24x24 two-band ramp; see docker/make_sample_cog.py.
N_SAMPLE_CELLS = 24 * 24
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

    Also sends one deliberately invalid request (an unsupported ``f``) and
    requires a 400, proving the container maps bad input through its mounted
    exception handlers instead of letting it surface as a 500.

    Returns:
        Process exit code: 0 when every check passes, 1 otherwise.
    """
    good = _get_when_ready(_query_url(BASE_BBOX_URL, "CoverageJSON"))
    bad = _get(_query_url(BASE_BBOX_URL, "png"))

    return _report(*_response_problems(good), *_bad_input_problems(bad))


def _query_url(base: str, output_format: str) -> str:
    """Build a /bbox request URL for ``base`` in the given output format.

    Args:
        base: The ``/bbox/{bbox}`` URL prefix.
        output_format: The value for the ``f`` query parameter.

    Returns:
        The absolute request URL carrying the ``url`` and ``f`` query parameters.
    """
    query = urllib.parse.urlencode({"url": COG_URL, "f": output_format})

    return f"{base}?{query}"


def _get_when_ready(url: str) -> HTTPResponse | urllib.error.HTTPError:
    """GET ``url``, retrying while the server is still coming up.

    A transport error (connection refused, reset, or timed out) means the
    container is still booting, so retry until the readiness timeout elapses. An
    HTTP error status means the server answered, so it is returned for assertion
    rather than retried.

    Args:
        url: The absolute request URL.

    Returns:
        The HTTP response, or an ``HTTPError`` (which also carries ``status`` and
        ``headers``) to assert on.

    Raises:
        SystemExit: If the server never accepts a connection before the timeout.
    """
    deadline = time.monotonic() + READINESS_TIMEOUT_S
    last_error: OSError | None = None

    while time.monotonic() < deadline:
        try:
            return _get(url)
        except OSError as transport_error:
            # refused / reset / timed out: the server is still coming up.
            last_error = transport_error
            time.sleep(0.5)

    print(
        f"server not ready after {READINESS_TIMEOUT_S}s: {last_error}", file=sys.stderr
    )
    raise SystemExit(1)


def _get(url: str) -> HTTPResponse | urllib.error.HTTPError:
    """GET ``url`` once, bounding the request with a socket timeout.

    Args:
        url: The absolute request URL.

    Returns:
        The HTTP response, or an ``HTTPError`` when the server answers with an
        error status (it too carries ``status`` and ``headers``).
    """
    try:
        return cast(
            HTTPResponse, urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_S)
        )
    except urllib.error.HTTPError as http_error:
        return http_error


def _response_problems(
    response: HTTPResponse | urllib.error.HTTPError,
) -> Iterator[str]:
    """Yield every envelope and content problem for the primary response.

    Reads the response once (the body is a single-use stream) and dispatches to
    the pure envelope and content checks.

    Args:
        response: The response to the good CoverageJSON request.

    Yields:
        A description of each envelope or content mismatch.
    """
    body = json.loads(response.read())

    yield from _envelope_problems(response.status, response.headers.get_content_type())
    yield from _content_problems(body)


def _envelope_problems(status: int | None, content_type: str) -> Iterator[str]:
    """Yield any HTTP status or content-type mismatch.

    Args:
        status: The response status code (``None`` if the response exposes none).
        content_type: The response content type (no charset parameter).

    Yields:
        A description of each envelope mismatch; nothing when both are correct.
    """
    if status != 200:
        yield f"status {status} != 200"

    if content_type != MEDIA_TYPE:
        yield f"content-type {content_type!r} != {MEDIA_TYPE!r}"


def _content_problems(body: dict[str, Any]) -> Iterator[str]:
    """Yield any problem with the document's structure and values.

    A schema-invalid body short-circuits: its structure is unreliable, so the
    later structural and value checks would only add noise.

    Args:
        body: The parsed CoverageJSON response.

    Yields:
        A description of each mismatch; nothing when the document is valid and
        carries the expected values.
    """
    try:
        jsonschema.validate(body, json.loads(SCHEMA_PATH.read_text()))
    except jsonschema.ValidationError as error:
        yield f"schema invalid: {error.message}"

        return

    if (type_ := body.get("type")) != "Coverage":
        yield f"type {type_!r} != 'Coverage'"

    if (domain_type := body.get("domain", {}).get("domainType")) != "Grid":
        yield f"domain.domainType {domain_type!r} != 'Grid'"

    ranges = body.get("ranges", {})

    if not ranges:
        yield "ranges is empty"

        return

    yield from _count_problems(ranges)
    yield from _nodata_problems(ranges)
    yield from _ramp_problems(ranges)


def _count_problems(ranges: dict[str, Any]) -> Iterator[str]:
    """Yield a mismatch for any range whose value count is not the grid size.

    Args:
        ranges: The response ``ranges`` mapping of band name to NdArray.

    Yields:
        A description for each range whose length is not both its own shape
        product and the expected cell count.
    """
    sized = {
        name: (len(band.get("values", [])), prod(band.get("shape") or [0]))
        for name, band in ranges.items()
    }

    yield from (
        f"range {name!r}: {n_values} values, shape product {n_cells}, "
        f"expected {N_SAMPLE_CELLS}"
        for name, (n_values, n_cells) in sized.items()
        if n_values != n_cells or n_cells != N_SAMPLE_CELLS
    )


def _nodata_problems(ranges: dict[str, Any]) -> Iterator[str]:
    """Yield a mismatch unless exactly one null sits at the nodata pixel.

    Args:
        ranges: The response ``ranges`` mapping of band name to NdArray.

    Yields:
        One description when the nulls across all bands are not exactly the
        single top-left (flat index 0) nodata pixel.
    """
    null_indexes = sorted(
        index
        for band in ranges.values()
        for index, value in enumerate(band.get("values", []))
        if value is None
    )

    if null_indexes != [0]:
        yield f"nodata: expected one null at flat index 0, found {null_indexes}"


def _ramp_problems(ranges: dict[str, Any]) -> Iterator[str]:
    """Yield a mismatch unless a band holds the intact 0 .. 575 ramp.

    Args:
        ranges: The response ``ranges`` mapping of band name to NdArray.

    Yields:
        One description when no null-free band runs from 0.0 to 575.0 across its
        first and last cells.
    """
    ramp = next(
        (
            band.get("values", [])
            for band in ranges.values()
            if None not in band.get("values", [])
        ),
        None,
    )

    if not ramp or ramp[0] != 0.0 or ramp[-1] != float(N_SAMPLE_CELLS - 1):
        yield f"ramp band absent or not 0.0 .. {float(N_SAMPLE_CELLS - 1)} at its ends"


def _bad_input_problems(
    response: HTTPResponse | urllib.error.HTTPError,
) -> Iterator[str]:
    """Yield a mismatch unless a deliberately invalid request was a 400.

    Args:
        response: The response to the deliberately invalid request.

    Yields:
        One description when the status is not 400.
    """
    if response.status != 400:
        yield (
            f"bad f= returned {response.status}, expected 400 via the mounted handlers"
        )


def _report(*problems: str) -> int:
    """Print any smoke failures and return the process exit code.

    Args:
        problems: Mismatch descriptions from the checks; empty on success.

    Returns:
        0 when there are no problems, 1 otherwise.
    """
    collected = list(problems)

    if not collected:
        print("smoke check passed: CoverageJSON Grid served; bad input rejected as 400")

        return 0

    listing = "\n".join(f"  - {problem}" for problem in collected)
    print(f"smoke check FAILED:\n{listing}", file=sys.stderr)

    return 1


if __name__ == "__main__":
    sys.exit(main())
