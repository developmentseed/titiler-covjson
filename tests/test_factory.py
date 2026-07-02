import pytest
from conftest import validate_covjson
from fastapi.testclient import TestClient

from titiler_covjson.factory import CovJSONFactory
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
