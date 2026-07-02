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


def test_bbox_selects_band_by_parameter_name(client: TestClient, cog_path: str) -> None:
    # The EDR parameter-name alias resolves to a band index end-to-end (the unit
    # tests cover the dependency in isolation; this proves the route wiring).
    response = client.get(
        "/bbox/-10,-5,10,5", params={"url": cog_path, "parameter-name": "b2"}
    )
    assert response.status_code == 200, response.text
    assert set(response.json()["parameters"]) == {"b2"}


def test_bbox_reprojects_to_projected_crs(client: TestClient, cog_path: str) -> None:
    # An explicit projected crs exercises the reproject read path (read_crs =
    # requested) and the projected-CRS referencing branch, distinct from the
    # WGS84/CRS84 default and the epsg:4326 no-reproject case.
    response = client.get(
        "/bbox/-10,-5,10,5", params={"url": cog_path, "crs": "epsg:3857"}
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-crs"] == (
        "<http://www.opengis.net/def/crs/EPSG/0/3857>"
    )
    referencing = response.json()["domain"]["referencing"]
    assert referencing[0]["system"]["type"] == "ProjectedCRS"


def test_bbox_rejects_out_of_range_band_index(
    client: TestClient, cog_path: str
) -> None:
    response = client.get("/bbox/-10,-5,10,5", params={"url": cog_path, "bidx": 5})
    assert response.status_code == 400, response.text
    assert "out of range" in response.json()["detail"]


def test_bbox_selects_bands_by_expression(client: TestClient, cog_path: str) -> None:
    # Each ;-separated sub-expression names a derived band (its CovJSON parameter
    # key), so the keys come from the expression, not the source band names.
    response = client.get(
        "/bbox/-10,-5,10,5", params={"url": cog_path, "expression": "b1;b1+b2"}
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
    # rejected before any array is allocated.
    response = client.get(
        "/bbox/-10,-5,10,5",
        params={"url": cog_path, "width": 2000, "height": 2000},
    )
    assert response.status_code == 400, response.text
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
    # max_cells (the DoS the pre-read guard closes). The request must not hang or
    # allocate; it returns 400 promptly.
    response = client.get(
        "/bbox/-10,-5,10,5", params={"url": cog_path, "width": 100000}
    )
    assert response.status_code == 400, response.text
    assert "exceeds limit" in response.json()["detail"]


def test_bbox_lone_height_derived_grid_hits_ceiling(
    client: TestClient, cog_path: str
) -> None:
    response = client.get(
        "/bbox/-10,-5,10,5", params={"url": cog_path, "height": 100000}
    )
    assert response.status_code == 400, response.text
    assert "exceeds limit" in response.json()["detail"]


@pytest.mark.parametrize(
    ("read_epsg", "width", "height"),
    [(4326, 40, None), (4326, None, 30), (3857, 40, None), (3857, None, 30)],
    ids=["4326-w", "4326-h", "3857-w", "3857-h"],
)
def test_resolve_grid_dimensions_matches_rio_tiler(
    cog_path: str, read_epsg: int, width: int | None, height: int | None
) -> None:
    # Lock-in: the pre-read dimension resolution must equal what Reader.part
    # actually produces, both when the read reprojects (3857) and when it does
    # not (4326). If it drifts, the cell-count ceiling would guard a different
    # grid than the one allocated, silently reopening the lone-dimension DoS --
    # so this fails loudly if rio-tiler ever changes its derivation.
    bounds = (-10.0, -5.0, 10.0, 5.0)
    read_crs = rasterio.CRS.from_epsg(read_epsg)

    with Reader(cog_path) as src:
        predicted = _resolve_grid_dimensions(
            src.dataset, bounds, read_crs=read_crs, width=width, height=height
        )
        image = src.part(
            bounds, dst_crs=read_crs, bounds_crs=read_crs, width=width, height=height
        )

    assert predicted == (image.width, image.height)


def test_bbox_rejects_oversized_output_grid(
    small_ceiling_client: TestClient, cog_path: str
) -> None:
    # max_size=8 -> an 8x8 = 64-cell output exceeds the factory's max_cells=16.
    # This exercises the post-read backstop (max_size bounds the read, so the
    # cell count is only known after reading), distinct from the pre-read guard
    # on explicit width/height.
    response = small_ceiling_client.get(
        "/bbox/-10,-5,10,5", params={"url": cog_path, "max_size": 8}
    )
    assert response.status_code == 400, response.text
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


def test_bbox_unreadable_url_is_server_error(client: TestClient) -> None:
    # A url GDAL cannot open raises RasterioIOError, which titiler maps to 500
    # (it does not distinguish "missing" from other open failures).
    response = client.get(
        "/bbox/-10,-5,10,5", params={"url": "/tmp/does-not-exist-xyz.tif"}
    )
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
    assert "crs" in response.json()["detail"].lower()
