from pathlib import Path

import pytest
import rasterio
from conftest import validate_covjson
from fastapi.testclient import TestClient
from rio_tiler.io import Reader

from titiler_covjson.factory import CovJSONFactory, _resolve_grid_dimensions
from titiler_covjson.responses import COVJSON_MEDIA_TYPE


def test_bbox_returns_schema_valid_grid_coverage(
    client: TestClient, tiny_cog_path: str
) -> None:
    # Default request (no crs): the endpoint reads the 2x2 EPSG:4326 source
    # natively and labels the output CRS84. The grid is small enough to assert
    # the entire coverage document: separable x/y axes (centers -5/5 and
    # 2.5/-2.5), CRS84 x-before-y referencing, band descriptions as parameter
    # labels, and band 2's top-left nodata surfacing as a leading null.
    response = client.get("/bbox/-10,-5,10,5", params={"url": tiny_cog_path})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(COVJSON_MEDIA_TYPE)
    assert response.headers["content-crs"] == (
        "<http://www.opengis.net/def/crs/OGC/1.3/CRS84>"
    )

    body = response.json()
    validate_covjson(body)

    assert body == {
        "type": "Coverage",
        "domain": {
            "type": "Domain",
            "domainType": "Grid",
            "axes": {
                "x": {"start": -5.0, "stop": 5.0, "num": 2},
                "y": {"start": 2.5, "stop": -2.5, "num": 2},
            },
            "referencing": [
                {
                    "coordinates": ["x", "y"],
                    "system": {
                        "type": "GeographicCRS",
                        "id": "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
                    },
                }
            ],
        },
        "parameters": {
            "b1": {
                "type": "Parameter",
                "observedProperty": {"label": {"en": "red"}},
            },
            "b2": {
                "type": "Parameter",
                "observedProperty": {"label": {"en": "nir"}},
            },
        },
        "ranges": {
            "b1": {
                "type": "NdArray",
                "dataType": "float",
                "axisNames": ["y", "x"],
                "shape": [2, 2],
                "values": [0.0, 1.0, 2.0, 3.0],
            },
            "b2": {
                "type": "NdArray",
                "dataType": "float",
                "axisNames": ["y", "x"],
                "shape": [2, 2],
                "values": [None, 1.0, 2.0, 3.0],
            },
        },
    }


def test_bbox_honors_explicit_crs(client: TestClient, cog_path: str) -> None:
    # An explicit crs is read losslessly in EPSG:4326 but labeled as requested,
    # so the coverage advertises the EPSG:4326 URI, not the CRS84 default.
    response = client.get(
        "/bbox/-10,-5,10,5", params={"url": cog_path, "crs": "epsg:4326"}
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-crs"] == (
        "<http://www.opengis.net/def/crs/EPSG/0/4326>"
    )
    assert response.json()["domain"]["domainType"] == "Grid"


def test_bbox_selects_single_band_by_index(client: TestClient, cog_path: str) -> None:
    response = client.get("/bbox/-10,-5,10,5", params={"url": cog_path, "bidx": 1})
    assert response.status_code == 200, response.text
    assert set(response.json()["parameters"]) == {"b1"}


def test_bbox_bidx_aligns_selected_band_metadata_and_values(
    client: TestClient, tiny_cog_path: str
) -> None:
    # Selecting band 2 must carry band 2's own metadata and values, not band 1's:
    # the key is `b2`, its label is the band-2 description ("nir"), and its values
    # are band 2's (with the top-left nodata null). This is what a key-set-only
    # assertion cannot catch. It proves the info()-to-image band alignment.
    response = client.get("/bbox/-10,-5,10,5", params={"url": tiny_cog_path, "bidx": 2})
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body["parameters"]) == {"b2"}
    assert body["parameters"]["b2"]["observedProperty"]["label"] == {"en": "nir"}
    assert body["ranges"]["b2"]["values"] == [None, 1.0, 2.0, 3.0]


def test_bbox_integer_band_serializes_as_integer(
    client: TestClient, scaled_int_cog_path: str
) -> None:
    # An integer source read without unscaling keeps its integer storage dtype,
    # so the range is typed `integer` and carries the raw stored values.
    response = client.get(
        "/bbox/-10,-5,10,5",
        params={"url": scaled_int_cog_path, "width": 4, "height": 4},
    )
    assert response.status_code == 200, response.text
    band = response.json()["ranges"]["b1"]
    assert band["dataType"] == "integer"
    assert band["values"][:4] == [2550, 2551, 2552, 2553]


def test_bbox_unscale_tracks_read_array_dtype(
    client: TestClient, scaled_int_cog_path: str
) -> None:
    # `unscale` casts the integer band to float when applying the scale, so the
    # range value type must follow the returned array (float), not the source
    # storage dtype (int); typing it `integer` would truncate 25.50 to 25.
    response = client.get(
        "/bbox/-10,-5,10,5",
        params={"url": scaled_int_cog_path, "width": 4, "height": 4, "unscale": True},
    )
    assert response.status_code == 200, response.text
    band = response.json()["ranges"]["b1"]
    assert band["dataType"] == "float"
    # float32 scaling is not exact (25.51 reads back as 25.5100002...), so compare
    # with tolerance; the point is that the physical value survives, not truncates.
    assert band["values"][:4] == pytest.approx([25.5, 25.51, 25.52, 25.53], abs=1e-4)


def test_bbox_selects_band_by_parameter_name(client: TestClient, cog_path: str) -> None:
    # The EDR parameter-name alias resolves to a band index end-to-end (the unit
    # tests cover the dependency in isolation; this proves the route wiring).
    response = client.get(
        "/bbox/-10,-5,10,5", params={"url": cog_path, "parameter-name": "b2"}
    )
    assert response.status_code == 200, response.text
    assert set(response.json()["parameters"]) == {"b2"}


def test_bbox_reprojects_to_projected_crs(client: TestClient, cog_path: str) -> None:
    # An explicit projected crs exercises the reproject read path. Under the
    # single-crs knob the bbox is in the requested crs, so this box is Web
    # Mercator meters inside the 4326 source's reprojected extent (reading these
    # coordinates as 4326 degrees would fall far outside the source, so a 200
    # already implies 3857 was used). With an explicit 4x4 grid the cell centers
    # are exact meter coordinates (x runs west->east, y north->south), which only
    # hold if the read reprojected to 3857 rather than relabeling degrees.
    response = client.get(
        "/bbox/-500000,-300000,500000,300000",
        params={"url": cog_path, "crs": "epsg:3857", "width": 4, "height": 4},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-crs"] == (
        "<http://www.opengis.net/def/crs/EPSG/0/3857>"
    )
    domain = response.json()["domain"]
    assert domain["referencing"][0]["system"]["type"] == "ProjectedCRS"
    assert domain["axes"]["x"] == {"start": -375000.0, "stop": 375000.0, "num": 4}
    assert domain["axes"]["y"] == {"start": 225000.0, "stop": -225000.0, "num": 4}


@pytest.mark.parametrize("bidx", [5, 0], ids=["above-range", "below-range"])
def test_bbox_rejects_out_of_range_band_index(
    client: TestClient, cog_path: str, bidx: int
) -> None:
    # The 2-band source rejects both an index above the count and a zero/negative
    # index (the `i < 1` lower bound), rather than letting rio-tiler 500.
    response = client.get("/bbox/-10,-5,10,5", params={"url": cog_path, "bidx": bidx})
    assert response.status_code == 400, response.text
    assert "out of range" in response.json()["detail"]


def test_bbox_parameter_carries_unit(
    client: TestClient, unit_tagged_cog_path: str
) -> None:
    # End-to-end unit path: a band `units` tag flows through BandInfo.unit and
    # create_unit (UCUM resolution) to the coverage Parameter.unit. Band 2 has no
    # unit tag, so its parameter carries no unit member.
    response = client.get("/bbox/-10,-5,10,5", params={"url": unit_tagged_cog_path})
    assert response.status_code == 200, response.text
    parameters = response.json()["parameters"]
    assert parameters["b1"]["unit"]["symbol"]["value"] == "mm"
    assert parameters["b1"]["unit"]["label"] == {"en": "millimeters"}
    assert "unit" not in parameters["b2"]


def test_bbox_rejects_conflicting_band_selectors(
    client: TestClient, cog_path: str
) -> None:
    # bidx and expression are mutually exclusive; CovJSONBandParams raises during
    # Depends resolution, which must map to 400 through the exception handlers.
    response = client.get(
        "/bbox/-10,-5,10,5",
        params={"url": cog_path, "bidx": 1, "expression": "b1+b2"},
    )
    assert response.status_code == 400, response.text
    assert "Supply only one" in response.json()["detail"]


@pytest.mark.parametrize(
    "params",
    [{"bidx": [1, 1]}, {"parameter-name": "b1,b1"}],
    ids=["duplicate-bidx", "duplicate-parameter-name"],
)
def test_bbox_rejects_duplicate_band_index(
    client: TestClient, cog_path: str, params: dict[str, str | list[int]]
) -> None:
    # Duplicate indexes yield duplicate band names, which CoverageInput's
    # uniqueness check would reject with a bare ValueError (500). The factory
    # pre-validates and returns an actionable 400 instead.
    response = client.get("/bbox/-10,-5,10,5", params={"url": cog_path, **params})
    assert response.status_code == 400, response.text
    assert "unique" in response.json()["detail"]


def test_bbox_selects_bands_by_expression(
    client: TestClient, tiny_cog_path: str
) -> None:
    # Each ;-separated sub-expression names a derived band (its CovJSON parameter
    # key), and its values are the computed band math. On the 2x2 fixture band 1
    # is the ramp 0..3 and band 2 copies it with a nodata top-left; rio-tiler
    # masks that pixel across every expression output, so the leading value is
    # null and the rest carry the math (b1 -> the ramp, b1+b2 -> the doubled ramp).
    response = client.get(
        "/bbox/-10,-5,10,5", params={"url": tiny_cog_path, "expression": "b1;b1+b2"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body["parameters"]) == {"b1", "b1+b2"}
    assert body["ranges"]["b1"]["values"] == [None, 1.0, 2.0, 3.0]
    assert body["ranges"]["b1+b2"]["values"] == [None, 2.0, 4.0, 6.0]


def test_bbox_expression_with_trailing_semicolon(
    client: TestClient, tiny_cog_path: str
) -> None:
    # A trailing ; leaves an empty sub-expression. rio-tiler drops it when reading,
    # so deriving names the same way keeps the parameter keys one-to-one with the
    # returned bands (a naive split would emit a stray empty name and 500).
    response = client.get(
        "/bbox/-10,-5,10,5", params={"url": tiny_cog_path, "expression": "b1;b1+b2;"}
    )
    assert response.status_code == 200, response.text
    assert set(response.json()["parameters"]) == {"b1", "b1+b2"}


def test_bbox_rejects_duplicate_expression(client: TestClient, cog_path: str) -> None:
    # Overlaps the _expression_band_names doctest by design: the doctest proves the
    # pure function raises, while this proves that raise (from *after* the read)
    # propagates through the route and exception handlers to a 400, not a 500.
    response = client.get(
        "/bbox/-10,-5,10,5", params={"url": cog_path, "expression": "b1;b1"}
    )
    assert response.status_code == 400, response.text
    assert "unique" in response.json()["detail"]


def test_bbox_rejects_oversized_explicit_grid(
    client: TestClient, cog_path: str
) -> None:
    # Both width and height explicit: the cell count is known pre-read and
    # rejected before any array is allocated ("Requested", not "Output").
    response = client.get(
        "/bbox/-10,-5,10,5",
        params={"url": cog_path, "width": 2000, "height": 2000},
    )
    assert response.status_code == 400, response.text
    assert "Requested" in response.json()["detail"]
    assert "exceeds limit" in response.json()["detail"]


def test_bbox_downsamples_to_default_max_size(
    small_default_client: TestClient, cog_path: str
) -> None:
    # No sizing requested: the 16x16 source is capped at the factory's
    # default_max_size (4), yielding a 4x4 grid.
    response = small_default_client.get("/bbox/-10,-5,10,5", params={"url": cog_path})
    assert response.status_code == 200, response.text
    axes = response.json()["domain"]["axes"]
    assert axes["x"]["num"] == 4
    assert axes["y"]["num"] == 4


def test_factory_rejects_max_cells_below_default_max_size_squared() -> None:
    with pytest.raises(ValueError, match="max_cells"):
        CovJSONFactory(default_max_size=4, max_cells=1)


@pytest.mark.parametrize(
    "params",
    [
        {"width": 0, "height": 0},
        {"width": 0},
        {"height": -5},
        {"width": -5, "height": 8},
    ],
    ids=["zero-both", "zero-lone", "negative-lone", "negative-with-valid"],
)
def test_bbox_rejects_non_positive_dimensions(
    client: TestClient, cog_path: str, params: dict[str, int]
) -> None:
    # PartFeatureParams does not constrain width/height to be positive, so a
    # zero or negative dimension would otherwise be silently mis-sized (0) or
    # crash rio-tiler with a 500 (negative). The factory rejects it with 400.
    response = client.get("/bbox/-10,-5,10,5", params={"url": cog_path, **params})
    assert response.status_code == 400, response.text
    assert "positive integer" in response.json()["detail"]


def test_bbox_lone_width_derives_height(client: TestClient, cog_path: str) -> None:
    # A lone width (no height) is allowed: rio-tiler derives the height from the
    # read-window aspect ratio. The 16x16 source over the square-pixel bounds
    # has a 1:1 window, so width=8 yields an 8x8 grid.
    response = client.get("/bbox/-10,-5,10,5", params={"url": cog_path, "width": 8})
    assert response.status_code == 200, response.text
    axes = response.json()["domain"]["axes"]
    assert (axes["x"]["num"], axes["y"]["num"]) == (8, 8)


def test_bbox_lone_width_derived_grid_hits_ceiling(
    client: TestClient, cog_path: str
) -> None:
    # A lone width still upsamples, so a huge one is rejected before the array is
    # read: the derived height is resolved pre-read and the cell count exceeds
    # max_cells (the DoS the pre-read guard closes). Asserting "Requested"
    # (the pre-read message, vs "Output" post-read) proves no array was allocated.
    response = client.get(
        "/bbox/-10,-5,10,5", params={"url": cog_path, "width": 100000}
    )
    assert response.status_code == 400, response.text
    assert "Requested" in response.json()["detail"]
    assert "exceeds limit" in response.json()["detail"]


def test_bbox_lone_height_derived_grid_hits_ceiling(
    client: TestClient, cog_path: str
) -> None:
    response = client.get(
        "/bbox/-10,-5,10,5", params={"url": cog_path, "height": 100000}
    )
    assert response.status_code == 400, response.text
    assert "Requested" in response.json()["detail"]
    assert "exceeds limit" in response.json()["detail"]


def test_bbox_huge_max_size_hits_ceiling_pre_read(
    client: TestClient, wide_cog_path: str
) -> None:
    # max_size caps the longest output axis at min(max_size, native). On a source
    # whose native resolution exceeds the default cap, a max_size at/above
    # default_max_size resolves to a grid over the default max_cells -- and it is
    # resolved pre-read, so it is rejected before allocation ("Requested"), not by
    # the post-read backstop after a large read (the max_size DoS this closes).
    response = client.get(
        "/bbox/-10,-5,10,5", params={"url": wide_cog_path, "max_size": 1500}
    )
    assert response.status_code == 400, response.text
    assert "Requested" in response.json()["detail"]
    assert "exceeds limit" in response.json()["detail"]


def test_bbox_rejects_subpixel_thin_bbox(
    client: TestClient, wide_cog_path: str
) -> None:
    # A box thinner than half a source pixel in one axis rounds that read-window
    # axis to 0. With a max_size cap and no explicit width/height, rio-tiler's own
    # max_size scaling would divide by zero (a 500); the pre-read guard turns this
    # unsamplable box into an actionable 400 instead.
    response = client.get("/bbox/0,-5,0.005,5", params={"url": wide_cog_path})
    assert response.status_code == 400, response.text
    assert "too thin to sample" in response.json()["detail"]


_FULL_BOUNDS = (-10.0, -5.0, 10.0, 5.0)  # 16x16 source -> square read window
_TALL_BOUNDS = (-10.0, -5.0, 0.0, 5.0)  # narrow in x, full in y -> taller window


@pytest.mark.parametrize(
    ("bounds", "read_epsg", "width", "height", "max_size"),
    [
        (_FULL_BOUNDS, 4326, 40, None, None),
        (_FULL_BOUNDS, 4326, None, 30, None),
        (_FULL_BOUNDS, 3857, 40, None, None),
        (_FULL_BOUNDS, 3857, None, 30, None),
        (_FULL_BOUNDS, 4326, None, None, 8),
        (_FULL_BOUNDS, 3857, None, None, 8),
        (_FULL_BOUNDS, 4326, None, None, 5000),
        (_FULL_BOUNDS, 4326, None, None, None),
        (_TALL_BOUNDS, 4326, None, None, 10),
    ],
    ids=[
        "4326-w",
        "4326-h",
        "3857-w",
        "3857-h",
        "4326-max_size",
        "3857-max_size",
        "max_size-clamps-to-native",
        "native",
        "max_size-tall-window",
    ],
)
def test_resolve_grid_dimensions_matches_rio_tiler(
    cog_path: str,
    bounds: tuple[float, float, float, float],
    read_epsg: int,
    width: int | None,
    height: int | None,
    max_size: int | None,
) -> None:
    # Lock-in: the pre-read dimension resolution must equal what Reader.part
    # actually produces -- across reproject (3857) and non-reproject (4326)
    # reads, a lone width/height, a max_size cap (wider and taller windows), a
    # clamp-to-native, and a native read. If it drifts, the cell-count ceiling
    # would guard a different grid than the one allocated, silently reopening the
    # DoS -- so this fails loudly if rio-tiler ever changes its derivation.
    read_crs = rasterio.CRS.from_epsg(read_epsg)

    with Reader(cog_path) as src:
        predicted = _resolve_grid_dimensions(
            src.dataset,
            bounds,
            read_crs=read_crs,
            width=width,
            height=height,
            max_size=max_size,
        )
        image = src.part(
            bounds,
            dst_crs=read_crs,
            bounds_crs=read_crs,
            width=width,
            height=height,
            max_size=max_size,
        )

    assert predicted == (image.width, image.height)


def test_bbox_rejects_oversized_output_grid(
    small_ceiling_client: TestClient, cog_path: str
) -> None:
    # max_size=8 -> an 8x8 = 64-cell output exceeds the factory's max_cells=16.
    # The max_size output dimensions are resolved pre-read, so this is rejected
    # before allocation ("Requested"), same as explicit width/height.
    response = small_ceiling_client.get(
        "/bbox/-10,-5,10,5", params={"url": cog_path, "max_size": 8}
    )
    assert response.status_code == 400, response.text
    assert "Requested" in response.json()["detail"]
    assert "exceeds limit" in response.json()["detail"]


def test_bbox_rejects_unsupported_format(client: TestClient, cog_path: str) -> None:
    response = client.get("/bbox/-10,-5,10,5", params={"url": cog_path, "f": "png"})
    assert response.status_code == 400, response.text


@pytest.mark.parametrize(
    "bbox",
    ["10,-5,-10,5", "-10,5,10,-5"],
    ids=["minx>=maxx", "miny>=maxy"],
)
def test_bbox_rejects_degenerate_bbox(
    client: TestClient, cog_path: str, bbox: str
) -> None:
    # Both degenerate halves: the ordered path params parse fine, so this is our
    # own 400 (not a 422 from parsing).
    response = client.get(f"/bbox/{bbox}", params={"url": cog_path})
    assert response.status_code == 400, response.text


def test_bbox_rejects_malformed_bbox_segment(client: TestClient, cog_path: str) -> None:
    # A non-numeric bbox segment fails FastAPI path-param parsing -> 422.
    response = client.get("/bbox/a,-5,10,5", params={"url": cog_path})
    assert response.status_code == 422, response.text


def test_bbox_requires_url(client: TestClient) -> None:
    # url is a required query param; its absence is a FastAPI validation error.
    response = client.get("/bbox/-10,-5,10,5")
    assert response.status_code == 422, response.text


def test_bbox_unreadable_url_is_server_error(
    client: TestClient, tmp_path: Path
) -> None:
    # A url GDAL cannot open raises RasterioIOError, which titiler maps to 500
    # (it does not distinguish "missing" from other open failures).
    missing = str(tmp_path / "does-not-exist.tif")
    response = client.get("/bbox/-10,-5,10,5", params={"url": missing})
    assert response.status_code == 500, response.text


def test_bbox_rejects_invalid_crs(client: TestClient, cog_path: str) -> None:
    # CRSParams validates crs as a Pydantic BeforeValidator, so a bad value is a
    # 422 raised during parameter parsing, before the handler runs.
    response = client.get(
        "/bbox/-10,-5,10,5", params={"url": cog_path, "crs": "not-a-crs"}
    )
    assert response.status_code == 422, response.text


def test_bbox_rejects_crs_without_ogc_authority(
    client: TestClient, cog_path: str
) -> None:
    # A parseable CRS with no OGC authority code (an ESRI code here) passes
    # CRSParams but cannot become a CoverageJSON CRS URI. The factory rejects it
    # with 400 rather than letting the URI lookup raise -> 500.
    response = client.get(
        "/bbox/-10,-5,10,5", params={"url": cog_path, "crs": "ESRI:54009"}
    )
    assert response.status_code == 400, response.text
    assert "no OGC authority code" in response.json()["detail"]
