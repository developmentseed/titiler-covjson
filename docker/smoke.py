"""End-to-end smoke check for the CoverageJSON container.

Waits for the demo server, requests a CoverageJSON Grid (``/bbox``), Point and
MultiPoint (``/position``), and Polygon (``/area``) coverage for the bundled
sample Cloud-Optimized GeoTIFF (COG), and asserts each response is a valid,
non-empty CoverageJSON document. Narrates each request, its status, and the
coverage it returned, so a passing run shows what was served, not just a
summary line. Runs on the host (via ``uv run``) so the image carries no test
dependencies. Exits non-zero on any failure.

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
from typing import Any, NamedTuple, cast

import jsonschema

BASE_URL = "http://localhost:8000"
COG_URL = "/data/sample.tif"  # the sample COG baked into the image
BBOX = "-10,-5,10,5"
BASE_BBOX_URL = f"{BASE_URL}/bbox/{BBOX}"
BASE_POSITION_URL = f"{BASE_URL}/position"
BASE_AREA_URL = f"{BASE_URL}/area"
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
# Two interior positions sampled together on the same /position route return a
# MultiPoint coverage: a composite tuple axis of the positions and a 1-D range
# per band over it. The smoke asserts that shape, not exact values (the sampled
# cells are platform dependent, as for POINT above).
MULTIPOINT_COORDS = "MULTIPOINT((-5 -2), (5 2))"
N_MULTIPOINT_POSITIONS = 2
# A polygon over the whole sample extent reduces each band to one scalar. The
# default statistic is the mean, which for the 0 .. 575 ramp lands inside the
# ramp's range; the smoke asserts a finite in-range scalar rather than an exact
# mean, since the cutline's edge-cell inclusion is not worth pinning here.
AREA_COORDS = "POLYGON((-10 -5, 10 -5, 10 5, -10 5, -10 -5))"
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


class _Response(NamedTuple):
    """A response read once into the parts the checks and narration both need.

    The response body is a single-use stream, so it is read exactly once (into
    ``body``) and shared, rather than each consumer re-reading it.

    Attributes:
        status: The HTTP status code, or ``None`` if the response exposes none.
        content_type: The response content type (no charset parameter).
        body: The parsed JSON body, or ``None`` when the body was not JSON.
    """

    status: int | None
    content_type: str
    body: Any


def main() -> int:
    """Request the demo endpoints and assert valid, non-empty CoverageJSON.

    Exercises every route (a ``/bbox`` Grid coverage, a ``/position`` Point and
    MultiPoint coverage, and an ``/area`` Polygon coverage), and sends one
    deliberately invalid request (an unsupported ``f``) that must be a 400,
    proving the container maps bad input through its mounted exception handlers
    instead of letting it surface as a 500. Narrates each request and its
    outcome along the way, so a passing run still shows what was served.

    Returns:
        Process exit code: 0 when every check passes, 1 otherwise.
    """
    good_bbox = _read(_get_when_ready(_bbox_url(BASE_BBOX_URL, "CoverageJSON")))
    bad_bbox = _read(_get(_bbox_url(BASE_BBOX_URL, "png")))
    position = _read(_get(_position_url(POSITION_COORDS)))
    multipoint = _read(_get(_position_url(MULTIPOINT_COORDS)))
    area = _read(_get(_area_url(AREA_COORDS)))

    narrated = (
        (f"GET /bbox/{BBOX}?f=CoverageJSON", good_bbox),
        (f"GET /position?coords={POSITION_COORDS}", position),
        (f"GET /position?coords={MULTIPOINT_COORDS}", multipoint),
        (f"GET /area?coords={AREA_COORDS}", area),
        (f"GET /bbox/{BBOX}?f=png", bad_bbox),
    )
    # Pad every label to the widest, so the "-> status" columns line up.
    width = max(len(label) for label, _ in narrated)

    for label, response in narrated:
        summary = _summary(response.body)
        print(f"{label:{width}} -> {response.status}  {summary}".rstrip())

    return _report(
        *_bbox_problems(good_bbox),
        *_position_problems(position),
        *_multipoint_problems(multipoint),
        *_area_problems(area),
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


def _area_url(coords: str) -> str:
    """Build an /area request URL for the given WKT polygon.

    Args:
        coords: The WKT polygon for the ``coords`` query parameter.

    Returns:
        The absolute request URL carrying the ``url`` and ``coords`` parameters.
    """
    query = urllib.parse.urlencode({"url": COG_URL, "coords": coords})

    return f"{BASE_AREA_URL}?{query}"


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


def _read(response: HTTPResponse | urllib.error.HTTPError) -> _Response:
    """Read a raw response once into its status, content type, and parsed body.

    The body is a single-use stream, so it is consumed here and the parsed value
    shared by both the narration and the checks. A body that is not JSON (there
    is none in a passing run) yields a ``None`` body rather than raising.

    Args:
        response: The raw HTTP response (or ``HTTPError``, which also carries
            ``status`` and ``headers``).

    Returns:
        _Response: The status, content type, and parsed JSON body.
    """
    raw = response.read()

    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        body = None

    return _Response(response.status, response.headers.get_content_type(), body)


def _summary(body: Any) -> str:
    """Summarize a response body for the narration: its coverage or its error.

    Args:
        body: A parsed response body (a coverage, an error object, or ``None``).

    Returns:
        str: for a coverage, its domain and size (e.g. ``"Grid (2 bands)"`` or
            ``"MultiPoint (2 positions, 2 bands)"``); for an error, its ``detail``
            message; empty for anything else.
    """
    if not isinstance(body, dict):
        return ""

    if body.get("type") != "Coverage":
        return str(body.get("detail", ""))

    domain = body.get("domain", {})
    domain_type = domain.get("domainType", "?")
    n_bands = len(body.get("ranges", {}))
    size = [f"{n_bands} band{'s' if n_bands != 1 else ''}"]

    if domain_type == "MultiPoint":
        positions = domain.get("axes", {}).get("composite", {}).get("values", [])
        size.insert(0, f"{len(positions)} position{'s' if len(positions) != 1 else ''}")

    return f"{domain_type} ({', '.join(size)})"


def _bbox_problems(response: _Response) -> Iterator[str]:
    """Yield every envelope and content problem for the /bbox response.

    Dispatches to the shared envelope check and the Grid-specific content check.

    Args:
        response: The read /bbox CoverageJSON response.

    Yields:
        A description of each envelope or content mismatch.
    """
    yield from _envelope_problems(response.status, response.content_type)
    yield from _bbox_content_problems(response.body)


def _position_problems(response: _Response) -> Iterator[str]:
    """Yield every envelope and content problem for the /position response.

    Dispatches to the shared envelope check and the Point-specific content check.

    Args:
        response: The read /position CoverageJSON response.

    Yields:
        A description of each envelope or content mismatch.
    """
    yield from _envelope_problems(response.status, response.content_type)
    yield from _position_content_problems(response.body)


def _multipoint_problems(response: _Response) -> Iterator[str]:
    """Yield every envelope and content problem for the MULTIPOINT response.

    Dispatches to the shared envelope check and the MultiPoint content check.

    Args:
        response: The read /position MULTIPOINT CoverageJSON response.

    Yields:
        A description of each envelope or content mismatch.
    """
    yield from _envelope_problems(response.status, response.content_type)
    yield from _multipoint_content_problems(response.body)


def _area_problems(response: _Response) -> Iterator[str]:
    """Yield every envelope and content problem for the /area response.

    Dispatches to the shared envelope check and the Polygon-specific content
    check.

    Args:
        response: The read /area CoverageJSON response.

    Yields:
        A description of each envelope or content mismatch.
    """
    yield from _envelope_problems(response.status, response.content_type)
    yield from _area_content_problems(response.body)


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


def _multipoint_content_problems(body: dict[str, Any]) -> Iterator[str]:
    """Yield any problem with the MultiPoint document's structure.

    A schema-invalid body short-circuits: its structure is unreliable, so the
    later structural checks would only add noise.

    Args:
        body: The parsed CoverageJSON MULTIPOINT /position response.

    Yields:
        A description of each mismatch; nothing when the document is a valid
        MultiPoint coverage over the sampled positions.
    """
    try:
        jsonschema.validate(body, json.loads(SCHEMA_PATH.read_text()))
    except jsonschema.ValidationError as error:
        yield f"multipoint schema invalid: {error.message}"

        return

    if (type_ := body.get("type")) != "Coverage":
        yield f"multipoint type {type_!r} != 'Coverage'"

    if (domain_type := body.get("domain", {}).get("domainType")) != "MultiPoint":
        yield f"multipoint domain.domainType {domain_type!r} != 'MultiPoint'"

    composite = body.get("domain", {}).get("axes", {}).get("composite", {})

    if (data_type := composite.get("dataType")) != "tuple":
        yield f"multipoint composite.dataType {data_type!r} != 'tuple'"

    if (n := len(composite.get("values", []))) != N_MULTIPOINT_POSITIONS:
        yield (
            f"multipoint composite has {n} positions, expected {N_MULTIPOINT_POSITIONS}"
        )

    if ranges := body.get("ranges", {}):
        yield from _composite_shape_problems(ranges)
    else:
        yield "multipoint ranges is empty"


def _composite_shape_problems(ranges: dict[str, Any]) -> Iterator[str]:
    """Yield a mismatch for any range that is not 1-D over the composite axis.

    Each band of a MultiPoint coverage runs 1-D over ``composite``: one value per
    position, so ``axisNames`` is ``["composite"]`` and ``shape`` is
    ``[N_MULTIPOINT_POSITIONS]``.

    Args:
        ranges: The response ``ranges`` mapping of band name to NdArray.

    Yields:
        A description for each range whose axis labels or shape do not match.
    """
    for name, band in ranges.items():
        if (axis_names := band.get("axisNames")) != ["composite"]:
            yield (
                f"multipoint range {name!r}: axisNames {axis_names!r} != ['composite']"
            )

        if (shape := band.get("shape")) != [N_MULTIPOINT_POSITIONS]:
            yield (
                f"multipoint range {name!r}: shape {shape!r} != "
                f"[{N_MULTIPOINT_POSITIONS}]"
            )


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


def _area_content_problems(body: dict[str, Any]) -> Iterator[str]:
    """Yield any problem with the Polygon document's structure and value.

    A schema-invalid body short-circuits: its structure is unreliable, so the
    later structural and value checks would only add noise.

    Args:
        body: The parsed CoverageJSON /area response.

    Yields:
        A description of each mismatch; nothing when the document is a valid
        Polygon coverage carrying a sane reduced value per band.
    """
    try:
        jsonschema.validate(body, json.loads(SCHEMA_PATH.read_text()))
    except jsonschema.ValidationError as error:
        yield f"area schema invalid: {error.message}"

        return

    if (type_ := body.get("type")) != "Coverage":
        yield f"area type {type_!r} != 'Coverage'"

    if (domain_type := body.get("domain", {}).get("domainType")) != "Polygon":
        yield f"area domain.domainType {domain_type!r} != 'Polygon'"

    ranges = body.get("ranges", {})

    if not ranges:
        yield "area ranges is empty"

        return

    yield from _area_scalar_problems(ranges)


def _area_scalar_problems(ranges: dict[str, Any]) -> Iterator[str]:
    """Yield a mismatch for any range that is not a sane reduced scalar.

    Each band of a Polygon coverage is a 0-D scalar: an empty ``shape`` with a
    single value. A polygon over the whole sample reduces the 0 .. 575 ramp by
    the mean, so each band's value must be a finite number inside the ramp's
    range (never null, which would mean no valid pixels were reduced).

    Args:
        ranges: The response ``ranges`` mapping of band name to NdArray.

    Yields:
        A description for each range that is not a single in-range scalar.
    """
    for name, band in ranges.items():
        values = band.get("values", [])

        if band.get("shape") != [] or len(values) != 1:
            yield (
                f"area range {name!r}: expected one scalar value (empty shape), "
                f"got shape {band.get('shape')!r}, {len(values)} value(s)"
            )
        elif not _is_reduced_value(values[0]):
            yield (
                f"area range {name!r}: value {values[0]!r} is not a finite "
                f"reduction in 0 .. {N_SAMPLE_CELLS - 1}"
            )


def _is_reduced_value(value: Any) -> bool:
    """Return whether ``value`` is a finite reduced scalar in the ramp's range.

    Unlike a point sample, a reduced statistic (e.g., the mean) need not be a
    whole number, so this accepts any number in range; ``None`` (no valid pixels)
    and ``inf``/``nan`` (out of range) are rejected.

    Args:
        value: A range scalar from the response.

    Returns:
        True when ``value`` is a number within ``0 .. N_SAMPLE_CELLS - 1``.
    """
    return isinstance(value, (int, float)) and 0.0 <= value <= N_SAMPLE_CELLS - 1


def _bad_input_problems(response: _Response) -> Iterator[str]:
    """Yield a mismatch unless a deliberately invalid request was a 400.

    Args:
        response: The read response to the deliberately invalid request.

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
        print()
        print("✅ Smoke check PASSED!")

        return 0

    listing = "\n".join(f"  - {problem}" for problem in collected)
    print()
    print(f"❌ Smoke check FAILED:\n{listing}", file=sys.stderr)

    return 1


if __name__ == "__main__":
    sys.exit(main())
