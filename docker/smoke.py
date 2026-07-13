"""End-to-end smoke check for the CoverageJSON container.

Waits for the demo server, requests a CoverageJSON Grid (``/bbox``) and Point
(``/position``) for the bundled sample Cloud-Optimized GeoTIFF (COG), and asserts
each response is a valid, non-empty CoverageJSON document. Runs on the host (via
``uv run``) so the image carries no test dependencies. Exits non-zero on any
failure.

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
BASE_POSITION_URL = f"{BASE_URL}/position"
MEDIA_TYPE = "application/prs.coverage+json"
READINESS_TIMEOUT_S = 30.0
REQUEST_TIMEOUT_S = 10.0  # per-request socket timeout, so a hung server fails fast
# The baked sample is a fixed 24x24 two-band ramp; see docker/make_sample_cog.py.
N_SAMPLE_CELLS = 24 * 24
# POINT(0 0) samples an interior cell of the ramp, so both bands read the same
# real ramp value and neither is the top-left nodata pixel. The exact cell is a
# floor of a pixel-boundary coordinate over a non-integer pixel size, so it is
# platform dependent (local and Linux round it differently); the smoke asserts a
# valid ramp sample rather than a fixed value. The unit tests pin exact values on
# an exact-pixel-size fixture instead.
POSITION_COORDS = "POINT(0 0)"
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
    """Request the demo endpoints and assert valid, non-empty CoverageJSON.

    Exercises both routes -- a ``/bbox`` Grid coverage and a ``/position`` Point
    coverage -- and sends one deliberately invalid request (an unsupported ``f``)
    that must be a 400, proving the container maps bad input through its mounted
    exception handlers instead of letting it surface as a 500.

    Returns:
        Process exit code: 0 when every check passes, 1 otherwise.
    """
    good_bbox = _get_when_ready(_bbox_url(BASE_BBOX_URL, "CoverageJSON"))
    bad_bbox = _get(_bbox_url(BASE_BBOX_URL, "png"))
    position = _get(_position_url(POSITION_COORDS))

    return _report(
        *_bbox_problems(good_bbox),
        *_position_problems(position),
        *_bad_input_problems(bad_bbox),
    )


def _bbox_url(base: str, output_format: str) -> str:
    """Build a /bbox request URL for ``base`` in the given output format.

    Args:
        base: The ``/bbox/{bbox}`` URL prefix.
        output_format: The value for the ``f`` query parameter.

    Returns:
        The absolute request URL carrying the ``url`` and ``f`` query parameters.
    """
    query = urllib.parse.urlencode({"url": COG_URL, "f": output_format})

    return f"{base}?{query}"


def _position_url(coords: str) -> str:
    """Build a /position request URL for the given WKT coordinates.

    Args:
        coords: The WKT position for the ``coords`` query parameter.

    Returns:
        The absolute request URL carrying the ``url`` and ``coords`` parameters.
    """
    query = urllib.parse.urlencode({"url": COG_URL, "coords": coords})

    return f"{BASE_POSITION_URL}?{query}"


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


def _bbox_problems(
    response: HTTPResponse | urllib.error.HTTPError,
) -> Iterator[str]:
    """Yield every envelope and content problem for the /bbox response.

    Reads the response once (the body is a single-use stream) and dispatches to
    the shared envelope check and the Grid-specific content check.

    Args:
        response: The response to the /bbox CoverageJSON request.

    Yields:
        A description of each envelope or content mismatch.
    """
    body = json.loads(response.read())

    yield from _envelope_problems(response.status, response.headers.get_content_type())
    yield from _bbox_content_problems(body)


def _position_problems(
    response: HTTPResponse | urllib.error.HTTPError,
) -> Iterator[str]:
    """Yield every envelope and content problem for the /position response.

    Reads the response once (the body is a single-use stream) and dispatches to
    the shared envelope check and the Point-specific content check.

    Args:
        response: The response to the /position CoverageJSON request.

    Yields:
        A description of each envelope or content mismatch.
    """
    body = json.loads(response.read())

    yield from _envelope_problems(response.status, response.headers.get_content_type())
    yield from _position_content_problems(body)


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


def _bbox_content_problems(body: dict[str, Any]) -> Iterator[str]:
    """Yield any problem with the Grid document's structure and values.

    A schema-invalid body short-circuits: its structure is unreliable, so the
    later structural and value checks would only add noise.

    Args:
        body: The parsed CoverageJSON /bbox response.

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


def _position_content_problems(body: dict[str, Any]) -> Iterator[str]:
    """Yield any problem with the Point document's structure and value.

    A schema-invalid body short-circuits: its structure is unreliable, so the
    later structural and value checks would only add noise.

    Args:
        body: The parsed CoverageJSON /position response.

    Yields:
        A description of each mismatch; nothing when the document is a valid
        Point coverage carrying the expected scalar value.
    """
    try:
        jsonschema.validate(body, json.loads(SCHEMA_PATH.read_text()))
    except jsonschema.ValidationError as error:
        yield f"position schema invalid: {error.message}"

        return

    if (type_ := body.get("type")) != "Coverage":
        yield f"position type {type_!r} != 'Coverage'"

    if (domain_type := body.get("domain", {}).get("domainType")) != "Point":
        yield f"position domain.domainType {domain_type!r} != 'Point'"

    ranges = body.get("ranges", {})

    if not ranges:
        yield "position ranges is empty"

        return

    yield from _scalar_value_problems(ranges)


def _scalar_value_problems(ranges: dict[str, Any]) -> Iterator[str]:
    """Yield a mismatch for any range that is not a valid point scalar.

    Each band of a Point coverage is a 0-D scalar: an empty ``shape`` with a
    single value. POINT(0 0) samples an interior cell, so every band must carry
    the same integer ramp value in ``0 .. N_SAMPLE_CELLS - 1`` (and none is the
    top-left nodata pixel, which would serialize as ``null``).

    Args:
        ranges: The response ``ranges`` mapping of band name to NdArray.

    Yields:
        A description for each range that is not a single ramp-valued scalar,
        then one more if the bands do not all sample the same value.
    """
    sampled = []

    for name, band in ranges.items():
        values = band.get("values", [])

        if band.get("shape") != [] or len(values) != 1:
            yield (
                f"position range {name!r}: expected one scalar value (empty shape), "
                f"got shape {band.get('shape')!r}, {len(values)} value(s)"
            )
        elif not _is_ramp_value(values[0]):
            yield (
                f"position range {name!r}: value {values[0]!r} is not a ramp sample "
                f"in 0 .. {N_SAMPLE_CELLS - 1}"
            )
        else:
            sampled.append(values[0])

    if len(set(sampled)) > 1:
        yield f"position bands sample different cells: {sorted(set(sampled))}"


def _is_ramp_value(value: Any) -> bool:
    """Return whether ``value`` is an integer ramp sample in the sample's range.

    Args:
        value: A range scalar from the response.

    Returns:
        True when ``value`` is a whole number within ``0 .. N_SAMPLE_CELLS - 1``.
    """
    return (
        isinstance(value, (int, float))
        and float(value).is_integer()
        and 0 <= value <= N_SAMPLE_CELLS - 1
    )


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
        print(
            "smoke check passed: CoverageJSON Grid and Point served; "
            "bad input rejected as 400"
        )

        return 0

    listing = "\n".join(f"  - {problem}" for problem in collected)
    print(f"smoke check FAILED:\n{listing}", file=sys.stderr)

    return 1


if __name__ == "__main__":
    sys.exit(main())
