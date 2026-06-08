"""Round-trip tests for CoverageJSON playground examples.

Each test class corresponds to a file from the CoverageJSON playground at
https://covjson.org/playground/ and verifies two things:

1. The example JSON parses into the correct pydantic model.
2. The parsed model round-trips stably: serialise → parse → re-serialise
   produces identical JSON (i.e. the representation is canonical).

Where a playground example could not be used verbatim, the adaptation is
documented in the test-class docstring.
"""

# ---------------------------------------------------------------------------
# Playground gaps — files that cannot currently be tested
# ---------------------------------------------------------------------------
#
# grid-tiled.covjson is blocked by two independent issues:
#
#   (1) Year-only t values ("2010", "2011") are silently coerced to Unix
#       timestamps (~1970-01-01T00:33:30Z) by pydantic's non-strict
#       ValuesAxis[AwareDatetime] — the same data-corruption hazard
#       documented in TestPlaygroundPolygonSeries.
#   (2) The range uses TiledNdArray with dataType "integer"; the model only
#       provides TiledNdArrayFloat (dataType "float").  Integer and string
#       tiled arrays are noted as TODO in covjson-pydantic/ndarray.py.
#       Upstream issue: https://github.com/KNMI/covjson-pydantic/issues/31
#
# multipolygon.covjson is blocked because "MultiPolygon" is absent from the
# DomainType enum.  The following spec types share this limitation and also
# have no playground examples: Section (§9.10.8), Polygon (§9.10.9),
# MultiPolygonSeries (§9.10.12).

from __future__ import annotations

from typing import Any

import pytest
from conftest import assert_schema_valid, parse, roundtrip, roundtrip_is_stable
from covjson_pydantic.coverage import Coverage, CoverageCollection
from covjson_pydantic.domain import CompactAxis, Domain, ValuesAxis


class TestPlaygroundGrid:
    """Playground grid.covjson: Coverage / Grid / ICEC float with a null value."""

    EXAMPLE: dict[str, Any] = {
        "type": "Coverage",
        "domain": {
            "type": "Domain",
            "domainType": "Grid",
            "axes": {
                "x": {"values": [-10, -5, 0]},
                "y": {"values": [40, 50]},
                "z": {"values": [5]},
                "t": {"values": ["2010-01-01T00:12:20Z"]},
            },
            "referencing": [
                {
                    "coordinates": ["y", "x", "z"],
                    "system": {
                        "type": "GeographicCRS",
                        "id": "http://www.opengis.net/def/crs/EPSG/0/4979",
                    },
                },
                {
                    "coordinates": ["t"],
                    "system": {"type": "TemporalRS", "calendar": "Gregorian"},
                },
            ],
        },
        "parameters": {
            "ICEC": {
                "type": "Parameter",
                "description": {"en": "Sea Ice concentration (ice=1;no ice=0)"},
                "unit": {
                    "label": {"en": "Ratio"},
                    "symbol": {
                        "value": "1",
                        "type": "http://www.opengis.net/def/uom/UCUM/",
                    },
                },
                "observedProperty": {
                    "id": "http://vocab.nerc.ac.uk/standard_name/sea_ice_area_fraction/",
                    "label": {"en": "Sea Ice Concentration"},
                },
            }
        },
        "ranges": {
            "ICEC": {
                "type": "NdArray",
                "dataType": "float",
                "axisNames": ["t", "z", "y", "x"],
                "shape": [1, 1, 2, 3],
                "values": [0.5, 0.6, 0.4, 0.6, 0.2, None],
            }
        },
    }

    def test_parses(self) -> None:
        """Playground grid.covjson parses as Coverage with Grid domainType."""
        cov = parse(Coverage, self.EXAMPLE)
        assert cov.domain.domainType is not None
        assert cov.domain.domainType.value == "Grid"

    def test_icec_unit_symbol(self) -> None:
        """ICEC parameter unit symbol is UCUM '1' (dimensionless ratio)."""
        cov = parse(Coverage, self.EXAMPLE)
        assert cov.parameters is not None
        param = cov.parameters["ICEC"]
        assert param.unit is not None
        assert not isinstance(param.unit.symbol, str)
        assert param.unit.symbol is not None
        assert param.unit.symbol.value == "1"

    def test_null_range_value_preserved(self) -> None:
        """Null value in ICEC range survives round-trip serialisation."""
        result = roundtrip(Coverage, self.EXAMPLE)
        assert result["ranges"]["ICEC"]["values"][5] is None

    def test_roundtrip_stable(self) -> None:
        """Playground grid.covjson Coverage round-trips to identical JSON."""
        assert roundtrip_is_stable(Coverage, self.EXAMPLE)

    def test_schema_valid(self) -> None:
        """Playground grid.covjson Coverage validates against the schema."""
        assert_schema_valid(parse(Coverage, self.EXAMPLE))


class TestPlaygroundGridCategorical:
    """Playground grid-categorical.covjson: Coverage / Grid / categorical LC.

    Adaptation: ``preferredColor`` removed from category objects — the
    ``Category`` model has no ``extra="allow"``.
    """

    EXAMPLE: dict[str, Any] = {
        "type": "Coverage",
        "domain": {
            "type": "Domain",
            "domainType": "Grid",
            "axes": {
                "x": {"values": [-10, -5, 0]},
                "y": {"values": [40, 50]},
                "t": {"values": ["2010-01-01T00:12:20Z"]},
            },
            "referencing": [
                {
                    "coordinates": ["x", "y"],
                    "system": {
                        "type": "GeographicCRS",
                        "id": "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
                    },
                },
                {
                    "coordinates": ["t"],
                    "system": {"type": "TemporalRS", "calendar": "Gregorian"},
                },
            ],
        },
        "parameters": {
            "LC": {
                "type": "Parameter",
                "description": {"en": "Land Cover according to xyz classification"},
                "observedProperty": {
                    "id": "http://example.com/landcover",
                    "label": {"en": "XYZ Land Cover"},
                    "categories": [
                        {
                            "id": "http://example.com/landcover/categories/grass",
                            "label": {"en": "Grass"},
                            "description": {"en": "Very green grass."},
                        },
                        {
                            "id": "http://example.com/landcover/categories/rocks",
                            "label": {"en": "Rock"},
                            "description": {"en": "Just rocks."},
                        },
                    ],
                },
                "categoryEncoding": {
                    "http://example.com/landcover/categories/grass": 1,
                    "http://example.com/landcover/categories/rocks": 2,
                },
            }
        },
        "ranges": {
            "LC": {
                "type": "NdArray",
                "dataType": "integer",
                "axisNames": ["t", "y", "x"],
                "shape": [1, 2, 3],
                "values": [1, 1, None, 2, 1, 2],
            }
        },
    }

    def test_parses(self) -> None:
        """Playground grid-categorical.covjson parses with two categories."""
        cov = parse(Coverage, self.EXAMPLE)
        assert cov.parameters is not None
        param = cov.parameters["LC"]
        assert param.observedProperty.categories is not None
        assert len(param.observedProperty.categories) == 2

    def test_category_encoding(self) -> None:
        """LC categoryEncoding maps category URIs to integers."""
        cov = parse(Coverage, self.EXAMPLE)
        assert cov.parameters is not None
        enc = cov.parameters["LC"].categoryEncoding
        assert enc is not None
        assert enc["http://example.com/landcover/categories/grass"] == 1
        assert enc["http://example.com/landcover/categories/rocks"] == 2

    def test_null_range_value_preserved(self) -> None:
        """Null value (index 2) in LC integer range survives round-trip."""
        result = roundtrip(Coverage, self.EXAMPLE)
        assert result["ranges"]["LC"]["values"][2] is None

    def test_roundtrip_stable(self) -> None:
        """Playground grid-categorical.covjson Coverage round-trips stably."""
        assert roundtrip_is_stable(Coverage, self.EXAMPLE)

    def test_schema_valid(self) -> None:
        """Playground grid-categorical.covjson Coverage validates against the schema."""
        assert_schema_valid(parse(Coverage, self.EXAMPLE))


class TestPlaygroundGridDomainBng:
    """Playground grid-domain-bng.covjson: Domain-only, CompactAxis, ProjectedCRS."""

    EXAMPLE: dict[str, Any] = {
        "type": "Domain",
        "domainType": "Grid",
        "axes": {
            "x": {"start": 185106, "stop": 657784, "num": 100},
            "y": {"start": 15407, "stop": 619322, "num": 100},
        },
        "referencing": [
            {
                "coordinates": ["x", "y"],
                "system": {
                    "type": "ProjectedCRS",
                    "id": "http://www.opengis.net/def/crs/EPSG/0/27700",
                },
            }
        ],
    }

    def test_parses(self) -> None:
        """Playground BNG domain parses as Domain with CompactAxis x and y."""
        domain = parse(Domain, self.EXAMPLE)
        assert domain.domainType is not None
        assert domain.domainType.value == "Grid"
        assert isinstance(domain.axes.x, CompactAxis)
        assert isinstance(domain.axes.y, CompactAxis)

    def test_compact_axis_values(self) -> None:
        """BNG x/y CompactAxis start, stop and num fields are preserved."""
        domain = parse(Domain, self.EXAMPLE)
        assert isinstance(domain.axes.x, CompactAxis)
        assert domain.axes.x.start == pytest.approx(185106)
        assert domain.axes.x.stop == pytest.approx(657784)
        assert domain.axes.x.num == 100

    def test_projected_crs_epsg27700(self) -> None:
        """BNG referencing carries ProjectedCRS with EPSG:27700 URI."""
        domain = parse(Domain, self.EXAMPLE)
        assert domain.referencing is not None
        system = domain.referencing[0].system
        assert system.type == "ProjectedCRS"
        assert system.id == "http://www.opengis.net/def/crs/EPSG/0/27700"

    def test_roundtrip_stable(self) -> None:
        """Playground BNG domain round-trips to identical JSON."""
        assert roundtrip_is_stable(Domain, self.EXAMPLE)

    def test_schema_valid(self) -> None:
        """Playground BNG domain validates against the schema 'domain' def."""
        assert_schema_valid(parse(Domain, self.EXAMPLE), "domain")


class TestPlaygroundGridDomain:
    """Playground grid-domain.covjson: Domain-only / Grid / CompactAxis / GeographicCRS.

    Complements TestPlaygroundGridDomainBng: exercises the same CompactAxis
    mechanism with a GeographicCRS (CRS84) instead of a ProjectedCRS, and
    with no t axis at all.
    """

    EXAMPLE: dict[str, Any] = {
        "type": "Domain",
        "domainType": "Grid",
        "axes": {
            "x": {"start": -10, "stop": 0, "num": 10},
            "y": {"start": 40, "stop": 50, "num": 20},
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
    }

    def test_parses(self) -> None:
        """Playground grid-domain.covjson parses as Grid domain with CompactAxes."""
        domain = parse(Domain, self.EXAMPLE)
        assert domain.domainType is not None
        assert domain.domainType.value == "Grid"
        assert isinstance(domain.axes.x, CompactAxis)
        assert isinstance(domain.axes.y, CompactAxis)
        assert domain.axes.t is None

    def test_compact_axis_values(self) -> None:
        """Grid-domain CompactAxis x and y carry correct start, stop and num."""
        domain = parse(Domain, self.EXAMPLE)
        assert isinstance(domain.axes.x, CompactAxis)
        assert domain.axes.x.start == pytest.approx(-10)
        assert domain.axes.x.stop == pytest.approx(0)
        assert domain.axes.x.num == 10
        assert isinstance(domain.axes.y, CompactAxis)
        assert domain.axes.y.num == 20

    def test_roundtrip_stable(self) -> None:
        """Playground grid-domain.covjson Domain round-trips to identical JSON."""
        assert roundtrip_is_stable(Domain, self.EXAMPLE)

    def test_schema_valid(self) -> None:
        """Playground grid-domain.covjson Domain validates against the 'domain' def."""
        assert_schema_valid(parse(Domain, self.EXAMPLE), "domain")


class TestPlaygroundPoint:
    """Playground point.covjson: Coverage / Point / POTM + categorical QC.

    Adaptation: date-only t value ``"2013-01-01"`` promoted to
    ``"2013-01-01T00:00:00Z"`` — ``ValuesAxis[AwareDatetime]`` requires a
    timezone-aware datetime.
    """

    EXAMPLE: dict[str, Any] = {
        "type": "Coverage",
        "domain": {
            "type": "Domain",
            "domainType": "Point",
            "axes": {
                "x": {"values": [-5.1]},
                "y": {"values": [-40.2]},
                "t": {"values": ["2013-01-01T00:00:00Z"]},
            },
            "referencing": [
                {
                    "coordinates": ["x", "y"],
                    "system": {
                        "type": "GeographicCRS",
                        "id": "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
                    },
                },
                {
                    "coordinates": ["t"],
                    "system": {"type": "TemporalRS", "calendar": "Gregorian"},
                },
            ],
        },
        "parameters": {
            "POTM": {
                "type": "Parameter",
                "description": {
                    "en": "The potential temperature, in degrees celcius,"
                    " of the sea water"
                },
                "unit": {
                    "label": {"en": "Degree Celsius"},
                    "symbol": {
                        "value": "Cel",
                        "type": "http://www.opengis.net/def/uom/UCUM/",
                    },
                },
                "observedProperty": {
                    "id": "http://vocab.nerc.ac.uk/standard_name/sea_water_potential_temperature/",
                    "label": {"en": "Sea Water Potential Temperature"},
                },
            },
            "QC": {
                "type": "Parameter",
                "observedProperty": {
                    "id": "http://mmisw.org/ont/argo/qualityFlag",
                    "label": {"en": "Argo Quality Control Flag"},
                    "categories": [
                        {
                            "id": "http://mmisw.org/ont/argo/qualityFlag/_0",
                            "label": {"en": "No QC was performed"},
                        },
                        {
                            "id": "http://mmisw.org/ont/argo/qualityFlag/_1",
                            "label": {"en": "Good data"},
                        },
                        {
                            "id": "http://mmisw.org/ont/argo/qualityFlag/_4",
                            "label": {"en": "Bad data"},
                        },
                    ],
                },
                "categoryEncoding": {
                    "http://mmisw.org/ont/argo/qualityFlag/_0": 0,
                    "http://mmisw.org/ont/argo/qualityFlag/_1": 1,
                    "http://mmisw.org/ont/argo/qualityFlag/_4": 4,
                },
            },
        },
        "ranges": {
            "POTM": {"type": "NdArray", "dataType": "float", "values": [23.8]},
            "QC": {"type": "NdArray", "dataType": "integer", "values": [1]},
        },
    }

    def test_parses(self) -> None:
        """Playground point.covjson parses as Coverage with Point domainType."""
        cov = parse(Coverage, self.EXAMPLE)
        assert cov.domain.domainType is not None
        assert cov.domain.domainType.value == "Point"
        assert cov.parameters is not None

    def test_qc_category_encoding(self) -> None:
        """QC parameter categoryEncoding maps Argo flag URIs to integers."""
        cov = parse(Coverage, self.EXAMPLE)
        assert cov.parameters is not None
        enc = cov.parameters["QC"].categoryEncoding
        assert enc is not None
        assert enc["http://mmisw.org/ont/argo/qualityFlag/_0"] == 0
        assert enc["http://mmisw.org/ont/argo/qualityFlag/_1"] == 1
        assert enc["http://mmisw.org/ont/argo/qualityFlag/_4"] == 4

    def test_scalar_ranges(self) -> None:
        """Scalar NdArrays (no axisNames/shape) parse and serialise cleanly."""
        result = roundtrip(Coverage, self.EXAMPLE)
        assert result["ranges"]["POTM"]["values"] == [pytest.approx(23.8)]
        assert result["ranges"]["QC"]["values"] == [1]

    def test_roundtrip_stable(self) -> None:
        """Playground point.covjson Coverage round-trips to identical JSON."""
        assert roundtrip_is_stable(Coverage, self.EXAMPLE)

    def test_schema_valid(self) -> None:
        """Playground point.covjson Coverage validates against the schema."""
        assert_schema_valid(parse(Coverage, self.EXAMPLE))


class TestPlaygroundPointSeries:
    """Playground pointseries.covjson: Coverage / PointSeries / PSAL + POTM.

    Adaptation: six date-only t values (``"2013-01-01"`` … ``"2013-01-06"``)
    promoted to ``T00:00:00Z`` AwareDatetime strings.
    """

    EXAMPLE: dict[str, Any] = {
        "type": "Coverage",
        "domain": {
            "type": "Domain",
            "domainType": "PointSeries",
            "axes": {
                "x": {"values": [-10.1]},
                "y": {"values": [-40.2]},
                "t": {
                    "values": [
                        "2013-01-01T00:00:00Z",
                        "2013-01-02T00:00:00Z",
                        "2013-01-03T00:00:00Z",
                        "2013-01-04T00:00:00Z",
                        "2013-01-05T00:00:00Z",
                        "2013-01-06T00:00:00Z",
                    ]
                },
            },
            "referencing": [
                {
                    "coordinates": ["x", "y"],
                    "system": {
                        "type": "GeographicCRS",
                        "id": "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
                    },
                },
                {
                    "coordinates": ["t"],
                    "system": {"type": "TemporalRS", "calendar": "Gregorian"},
                },
            ],
        },
        "parameters": {
            "PSAL": {
                "type": "Parameter",
                "description": {
                    "en": "The measured salinity, in practical salinity"
                    " units (psu) of the sea water "
                },
                "unit": {"symbol": "psu"},
                "observedProperty": {
                    "id": "http://vocab.nerc.ac.uk/standard_name/sea_water_salinity/",
                    "label": {"en": "Sea Water Salinity"},
                },
            },
            "POTM": {
                "type": "Parameter",
                "description": {
                    "en": "The potential temperature, in degrees celcius,"
                    " of the sea water"
                },
                "unit": {
                    "label": {"en": "Degree Celsius"},
                    "symbol": {
                        "value": "Cel",
                        "type": "http://www.opengis.net/def/uom/UCUM/",
                    },
                },
                "observedProperty": {
                    "id": "http://vocab.nerc.ac.uk/standard_name/sea_water_potential_temperature/",
                    "label": {"en": "Sea Water Potential Temperature"},
                },
            },
        },
        "ranges": {
            "PSAL": {
                "type": "NdArray",
                "dataType": "float",
                "axisNames": ["t"],
                "shape": [6],
                "values": [43.9599, 43.9599, 43.9640, 43.9640, 43.9679, 43.9879],
            },
            "POTM": {
                "type": "NdArray",
                "dataType": "float",
                "axisNames": ["t"],
                "shape": [6],
                "values": [23.8, 23.7, 23.9, 23.4, 23.2, 22.4],
            },
        },
    }

    def test_parses(self) -> None:
        """Playground pointseries.covjson parses as Coverage with PointSeries domain."""
        cov = parse(Coverage, self.EXAMPLE)
        assert cov.domain.domainType is not None
        assert cov.domain.domainType.value == "PointSeries"

    def test_t_axis_length(self) -> None:
        """PointSeries t axis has six date values."""
        cov = parse(Coverage, self.EXAMPLE)
        assert isinstance(cov.domain.axes.t, ValuesAxis)
        assert len(cov.domain.axes.t.values) == 6

    def test_psal_string_symbol_unit(self) -> None:
        """PSAL parameter unit with plain string symbol (not object) parses."""
        cov = parse(Coverage, self.EXAMPLE)
        assert cov.parameters is not None
        psal = cov.parameters["PSAL"]
        assert psal.unit is not None
        assert psal.unit.symbol == "psu"

    def test_roundtrip_stable(self) -> None:
        """Playground pointseries.covjson Coverage round-trips stably."""
        assert roundtrip_is_stable(Coverage, self.EXAMPLE)

    def test_schema_valid(self) -> None:
        """Playground pointseries.covjson Coverage validates against the schema."""
        assert_schema_valid(parse(Coverage, self.EXAMPLE))


class TestPlaygroundProfile:
    """Playground profile.covjson: Coverage / VerticalProfile / VerticalCRS."""

    EXAMPLE: dict[str, Any] = {
        "type": "Coverage",
        "domain": {
            "type": "Domain",
            "domainType": "VerticalProfile",
            "axes": {
                "x": {"values": [-10.1]},
                "y": {"values": [-40.2]},
                "z": {
                    "values": [
                        5.4562,
                        8.9282,
                        14.8802,
                        20.8320,
                        26.7836,
                        32.7350,
                        38.6863,
                        44.6374,
                        50.5883,
                        56.5391,
                        62.4897,
                        68.4401,
                        74.3903,
                        80.3404,
                        86.2902,
                        92.2400,
                        98.1895,
                        104.1389,
                        110.0881,
                        116.0371,
                        121.9859,
                    ]
                },
                "t": {"values": ["2013-01-13T11:12:20Z"]},
            },
            "referencing": [
                {
                    "coordinates": ["x", "y"],
                    "system": {
                        "type": "GeographicCRS",
                        "id": "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
                    },
                },
                {
                    "coordinates": ["z"],
                    "system": {
                        "type": "VerticalCRS",
                        "cs": {
                            "csAxes": [
                                {
                                    "name": {"en": "Pressure"},
                                    "direction": "down",
                                    "unit": {"symbol": "Pa"},
                                }
                            ]
                        },
                    },
                },
                {
                    "coordinates": ["t"],
                    "system": {"type": "TemporalRS", "calendar": "Gregorian"},
                },
            ],
        },
        "parameters": {
            "PSAL": {
                "type": "Parameter",
                "description": {
                    "en": "The measured salinity, in practical salinity"
                    " units (psu) of the sea water "
                },
                "unit": {"symbol": "psu"},
                "observedProperty": {
                    "id": "http://vocab.nerc.ac.uk/standard_name/sea_water_salinity/",
                    "label": {"en": "Sea Water Salinity"},
                },
            },
            "POTM": {
                "type": "Parameter",
                "description": {
                    "en": "The potential temperature, in degrees celsius,"
                    " of the sea water"
                },
                "unit": {
                    "label": {"en": "Degree Celsius"},
                    "symbol": {
                        "value": "Cel",
                        "type": "http://www.opengis.net/def/uom/UCUM/",
                    },
                },
                "observedProperty": {
                    "id": "http://vocab.nerc.ac.uk/standard_name/sea_water_potential_temperature/",
                    "label": {"en": "Sea Water Potential Temperature"},
                },
            },
        },
        "ranges": {
            "PSAL": {
                "type": "NdArray",
                "dataType": "float",
                "axisNames": ["z"],
                "shape": [21],
                "values": [
                    43.9599,
                    43.9599,
                    43.9640,
                    43.9640,
                    43.9679,
                    43.9879,
                    44.0040,
                    44.0120,
                    44.0120,
                    44.0159,
                    44.0320,
                    44.0320,
                    44.0480,
                    44.0559,
                    44.0559,
                    44.0579,
                    44.0680,
                    44.0740,
                    44.0779,
                    44.0880,
                    44.0940,
                ],
            },
            "POTM": {
                "type": "NdArray",
                "dataType": "float",
                "axisNames": ["z"],
                "shape": [21],
                "values": [
                    23.8,
                    23.7,
                    23.5,
                    23.4,
                    23.2,
                    22.4,
                    21.8,
                    21.7,
                    21.5,
                    21.3,
                    21.0,
                    20.6,
                    20.1,
                    19.7,
                    19.4,
                    19.1,
                    18.9,
                    18.8,
                    18.7,
                    18.6,
                    18.5,
                ],
            },
        },
    }

    def test_parses(self) -> None:
        """Playground profile.covjson parses as Coverage with VerticalProfile domain."""
        cov = parse(Coverage, self.EXAMPLE)
        assert cov.domain.domainType is not None
        assert cov.domain.domainType.value == "VerticalProfile"

    def test_z_axis_length(self) -> None:
        """VerticalProfile z axis carries 21 pressure-level values."""
        cov = parse(Coverage, self.EXAMPLE)
        assert isinstance(cov.domain.axes.z, ValuesAxis)
        assert len(cov.domain.axes.z.values) == 21

    def test_vertical_crs_preserved(self) -> None:
        """VerticalCRS with cs/csAxes content survives round-trip via extra fields."""
        result = roundtrip(Coverage, self.EXAMPLE)
        vert_system = next(
            ref["system"]
            for ref in result["domain"]["referencing"]
            if ref["system"]["type"] == "VerticalCRS"
        )
        assert vert_system["cs"]["csAxes"][0]["direction"] == "down"
        assert vert_system["cs"]["csAxes"][0]["unit"]["symbol"] == "Pa"

    def test_psal_z_shape(self) -> None:
        """PSAL NdArray has z-axis shape [21] after round-trip."""
        result = roundtrip(Coverage, self.EXAMPLE)
        assert result["ranges"]["PSAL"]["shape"] == [21]
        assert result["ranges"]["PSAL"]["axisNames"] == ["z"]

    def test_roundtrip_stable(self) -> None:
        """Playground profile.covjson Coverage round-trips stably."""
        assert roundtrip_is_stable(Coverage, self.EXAMPLE)

    def test_schema_valid(self) -> None:
        """Playground profile.covjson Coverage validates against the schema."""
        assert_schema_valid(parse(Coverage, self.EXAMPLE))


class TestPlaygroundTrajectory:
    """Playground trajectory.covjson: Coverage / Trajectory / 4-coord composite."""

    EXAMPLE: dict[str, Any] = {
        "type": "Coverage",
        "domain": {
            "type": "Domain",
            "domainType": "Trajectory",
            "axes": {
                "composite": {
                    "dataType": "tuple",
                    "coordinates": ["t", "x", "y", "z"],
                    "values": [
                        ["2010-01-01T00:12:20Z", -10, 40, 5],
                        ["2010-01-01T00:14:20Z", -5, 50, 4],
                        ["2010-01-01T00:16:20Z", -6, 50, 5],
                    ],
                }
            },
            "referencing": [
                {
                    "coordinates": ["t"],
                    "system": {"type": "TemporalRS", "calendar": "Gregorian"},
                },
                {
                    "coordinates": ["y", "x", "z"],
                    "system": {
                        "type": "GeographicCRS",
                        "id": "http://www.opengis.net/def/crs/EPSG/0/4979",
                    },
                },
            ],
        },
        "parameters": {
            "ICEC": {
                "type": "Parameter",
                "description": {"en": "Sea Ice concentration (ice=1;no ice=0)"},
                "unit": {
                    "label": {"en": "Ratio"},
                    "symbol": {
                        "value": "1",
                        "type": "http://www.opengis.net/def/uom/UCUM/",
                    },
                },
                "observedProperty": {
                    "id": "http://vocab.nerc.ac.uk/standard_name/sea_ice_area_fraction/",
                    "label": {"en": "Sea Ice Concentration"},
                },
            }
        },
        "ranges": {
            "ICEC": {
                "type": "NdArray",
                "dataType": "float",
                "axisNames": ["composite"],
                "shape": [3],
                "values": [0.1, 0.2, 0.1],
            }
        },
    }

    def test_parses(self) -> None:
        """Playground trajectory.covjson parses with Trajectory domainType."""
        cov = parse(Coverage, self.EXAMPLE)
        assert cov.domain.domainType is not None
        assert cov.domain.domainType.value == "Trajectory"

    def test_composite_axis_four_coordinates(self) -> None:
        """Trajectory composite axis carries four coordinates (t, x, y, z)."""
        cov = parse(Coverage, self.EXAMPLE)
        composite = cov.domain.axes.composite
        assert composite is not None
        assert composite.coordinates == ["t", "x", "y", "z"]
        assert len(composite.values) == 3

    def test_composite_axis_values_preserved(self) -> None:
        """Trajectory composite axis tuple values survive round-trip."""
        result = roundtrip(Coverage, self.EXAMPLE)
        composite = result["domain"]["axes"]["composite"]
        assert composite["dataType"] == "tuple"
        assert composite["coordinates"] == ["t", "x", "y", "z"]
        assert len(composite["values"]) == 3
        assert composite["values"][0][0] == "2010-01-01T00:12:20Z"

    def test_roundtrip_stable(self) -> None:
        """Playground trajectory.covjson Coverage round-trips stably."""
        assert roundtrip_is_stable(Coverage, self.EXAMPLE)

    def test_schema_valid(self) -> None:
        """Playground trajectory.covjson Coverage validates against the schema."""
        assert_schema_valid(parse(Coverage, self.EXAMPLE))


class TestPlaygroundPointCollection:
    """Playground point-collection.covjson: CoverageCollection with shared params.

    Adaptation: date-only t values ``"2013-01-01"`` promoted to
    ``"2013-01-01T00:00:00Z"`` — same reason as TestPlaygroundPoint.
    """

    EXAMPLE: dict[str, Any] = {
        "type": "CoverageCollection",
        "domainType": "Point",
        "parameters": {
            "POTM": {
                "type": "Parameter",
                "description": {
                    "en": "The potential temperature, in degrees celsius,"
                    " of the sea water"
                },
                "unit": {
                    "label": {"en": "Degree Celsius"},
                    "symbol": {
                        "value": "Cel",
                        "type": "http://www.opengis.net/def/uom/UCUM/",
                    },
                },
                "observedProperty": {
                    "id": "http://vocab.nerc.ac.uk/standard_name/sea_water_potential_temperature/",
                    "label": {"en": "Sea Water Potential Temperature"},
                },
            },
            "QC": {
                "type": "Parameter",
                "observedProperty": {
                    "id": "http://mmisw.org/ont/argo/qualityFlag",
                    "label": {"en": "Argo Quality Control Flag"},
                    "categories": [
                        {
                            "id": "http://mmisw.org/ont/argo/qualityFlag/_0",
                            "label": {"en": "No QC was performed"},
                        },
                        {
                            "id": "http://mmisw.org/ont/argo/qualityFlag/_1",
                            "label": {"en": "Good data"},
                        },
                        {
                            "id": "http://mmisw.org/ont/argo/qualityFlag/_4",
                            "label": {"en": "Bad data"},
                        },
                    ],
                },
                "categoryEncoding": {
                    "http://mmisw.org/ont/argo/qualityFlag/_0": 0,
                    "http://mmisw.org/ont/argo/qualityFlag/_1": 1,
                    "http://mmisw.org/ont/argo/qualityFlag/_4": 4,
                },
            },
        },
        "referencing": [
            {
                "coordinates": ["x", "y"],
                "system": {
                    "type": "GeographicCRS",
                    "id": "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
                },
            },
            {
                "coordinates": ["t"],
                "system": {"type": "TemporalRS", "calendar": "Gregorian"},
            },
        ],
        "coverages": [
            {
                "type": "Coverage",
                "domain": {
                    "type": "Domain",
                    "axes": {
                        "x": {"values": [-5.1]},
                        "y": {"values": [-40.2]},
                        "t": {"values": ["2013-01-01T00:00:00Z"]},
                    },
                },
                "ranges": {
                    "POTM": {"type": "NdArray", "dataType": "float", "values": [23.8]},
                    "QC": {"type": "NdArray", "dataType": "integer", "values": [1]},
                },
            },
            {
                "type": "Coverage",
                "domain": {
                    "type": "Domain",
                    "axes": {
                        "x": {"values": [-5.1]},
                        "y": {"values": [-39.2]},
                        "t": {"values": ["2013-01-01T00:00:00Z"]},
                    },
                },
                "ranges": {
                    "POTM": {"type": "NdArray", "dataType": "float", "values": [21.8]},
                    "QC": {"type": "NdArray", "dataType": "integer", "values": [0]},
                },
            },
        ],
    }

    def test_parses(self) -> None:
        """Playground point-collection.covjson parses as CoverageCollection."""
        col = parse(CoverageCollection, self.EXAMPLE)
        assert col.type == "CoverageCollection"
        assert col.domainType is not None
        assert col.domainType.value == "Point"

    def test_two_coverages(self) -> None:
        """CoverageCollection contains exactly two Coverage objects."""
        col = parse(CoverageCollection, self.EXAMPLE)
        assert len(col.coverages) == 2

    def test_shared_parameters(self) -> None:
        """Shared POTM and QC parameters are present at the collection level."""
        col = parse(CoverageCollection, self.EXAMPLE)
        assert col.parameters is not None
        assert "POTM" in col.parameters
        assert "QC" in col.parameters

    def test_coverage_ranges_distinct(self) -> None:
        """Each Coverage in the collection carries its own independent ranges."""
        col = parse(CoverageCollection, self.EXAMPLE)
        potm_0 = col.coverages[0].ranges["POTM"]
        potm_1 = col.coverages[1].ranges["POTM"]
        assert not isinstance(potm_0, str)
        assert not isinstance(potm_1, str)
        assert potm_0.values[0] == pytest.approx(23.8)  # type: ignore[union-attr]
        assert potm_1.values[0] == pytest.approx(21.8)  # type: ignore[union-attr]

    def test_roundtrip_stable(self) -> None:
        """Playground point-collection.covjson CoverageCollection round-trips stably."""
        assert roundtrip_is_stable(CoverageCollection, self.EXAMPLE)

    def test_schema_valid(self) -> None:
        """Playground point-collection.covjson validates against the schema."""
        assert_schema_valid(parse(CoverageCollection, self.EXAMPLE))


class TestPlaygroundProfileCollection:
    """Playground profile-collection.covjson: CoverageCollection / VerticalProfile."""

    EXAMPLE: dict[str, Any] = {
        "type": "CoverageCollection",
        "domainType": "VerticalProfile",
        "parameters": {
            "PSAL": {
                "type": "Parameter",
                "description": {
                    "en": "The measured salinity, in practical salinity"
                    " units (psu) of the sea water"
                },
                "unit": {"symbol": "psu"},
                "observedProperty": {
                    "id": "http://vocab.nerc.ac.uk/standard_name/sea_water_salinity/",
                    "label": {"en": "Sea Water Salinity"},
                },
            }
        },
        "referencing": [
            {
                "coordinates": ["x", "y"],
                "system": {
                    "type": "GeographicCRS",
                    "id": "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
                },
            },
            {
                "coordinates": ["z"],
                "system": {
                    "type": "VerticalCRS",
                    "cs": {
                        "csAxes": [
                            {
                                "name": {"en": "Pressure"},
                                "direction": "down",
                                "unit": {"symbol": "Pa"},
                            }
                        ]
                    },
                },
            },
            {
                "coordinates": ["t"],
                "system": {"type": "TemporalRS", "calendar": "Gregorian"},
            },
        ],
        "coverages": [
            {
                "type": "Coverage",
                "domain": {
                    "type": "Domain",
                    "axes": {
                        "x": {"values": [-10.1]},
                        "y": {"values": [-40.2]},
                        "z": {"values": [5, 8, 14]},
                        "t": {"values": ["2013-01-13T11:12:20Z"]},
                    },
                },
                "ranges": {
                    "PSAL": {
                        "type": "NdArray",
                        "dataType": "float",
                        "axisNames": ["z"],
                        "shape": [3],
                        "values": [43.7, 43.8, 43.9],
                    }
                },
            },
            {
                "type": "Coverage",
                "domain": {
                    "type": "Domain",
                    "axes": {
                        "x": {"values": [-11.1]},
                        "y": {"values": [-45.2]},
                        "z": {"values": [4, 7, 9]},
                        "t": {"values": ["2013-01-13T12:12:20Z"]},
                    },
                },
                "ranges": {
                    "PSAL": {
                        "type": "NdArray",
                        "dataType": "float",
                        "axisNames": ["z"],
                        "shape": [3],
                        "values": [42.7, 41.8, 40.9],
                    }
                },
            },
        ],
    }

    def test_parses(self) -> None:
        """Playground profile-collection.covjson parses as CoverageCollection."""
        col = parse(CoverageCollection, self.EXAMPLE)
        assert col.type == "CoverageCollection"
        assert col.domainType is not None
        assert col.domainType.value == "VerticalProfile"

    def test_two_profiles(self) -> None:
        """Profile collection contains exactly two Coverage objects."""
        col = parse(CoverageCollection, self.EXAMPLE)
        assert len(col.coverages) == 2

    def test_vertical_crs_in_shared_referencing(self) -> None:
        """Shared referencing includes VerticalCRS; cs/csAxes content preserved."""
        result = roundtrip(CoverageCollection, self.EXAMPLE)
        vert_system = next(
            ref["system"]
            for ref in result["referencing"]
            if ref["system"]["type"] == "VerticalCRS"
        )
        assert vert_system["cs"]["csAxes"][0]["direction"] == "down"
        assert vert_system["cs"]["csAxes"][0]["unit"]["symbol"] == "Pa"

    def test_profile_z_axes_distinct(self) -> None:
        """Each profile's z axis carries its own depth levels."""
        col = parse(CoverageCollection, self.EXAMPLE)
        z0 = col.coverages[0].domain.axes.z
        z1 = col.coverages[1].domain.axes.z
        assert isinstance(z0, ValuesAxis)
        assert isinstance(z1, ValuesAxis)
        assert z0.values == [5, 8, 14]
        assert z1.values == [4, 7, 9]

    def test_roundtrip_stable(self) -> None:
        """Playground profile-collection.covjson round-trips stably."""
        assert roundtrip_is_stable(CoverageCollection, self.EXAMPLE)

    def test_schema_valid(self) -> None:
        """Playground profile-collection.covjson validates against the schema."""
        assert_schema_valid(parse(CoverageCollection, self.EXAMPLE))


class TestPlaygroundPolygonSeries:
    """Playground polygonseries.covjson: Coverage / PolygonSeries / TEMP.

    Adaptation: year-only t values (``"2013"``, ``"2014"``, ``"2015"``) promoted
    to ``T00:00:00Z`` AwareDatetime strings.  Pydantic silently coerces bare
    year strings to Unix timestamps (``"1970-01-01T00:33:33Z"`` etc.) when
    ``ValuesAxis[AwareDatetime]`` is in non-strict mode, so the unadapted
    values must not be used.
    """

    EXAMPLE: dict[str, Any] = {
        "type": "Coverage",
        "domain": {
            "type": "Domain",
            "domainType": "PolygonSeries",
            "axes": {
                "composite": {
                    "dataType": "polygon",
                    "coordinates": ["x", "y"],
                    "values": [
                        [
                            [
                                [9.92, 54.98],
                                [9.93, 54.59],
                                [13.64, 54.07],
                                [15.01, 51.10],
                                [12.24, 50.26],
                                [12.52, 49.54],
                                [13.59, 48.87],
                                [12.88, 48.28],
                                [13.02, 47.63],
                                [9.59, 47.52],
                                [8.52, 47.83],
                                [7.46, 47.62],
                                [8.09, 49.01],
                                [6.18, 49.46],
                                [5.98, 51.85],
                                [6.84, 52.22],
                                [7.10, 53.69],
                                [8.80, 54.02],
                                [8.52, 54.96],
                                [9.92, 54.98],
                            ]
                        ]
                    ],
                },
                "t": {
                    "values": [
                        "2013-01-01T00:00:00Z",
                        "2014-01-01T00:00:00Z",
                        "2015-01-01T00:00:00Z",
                    ]
                },
            },
            "referencing": [
                {
                    "coordinates": ["x", "y"],
                    "system": {
                        "type": "GeographicCRS",
                        "id": "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
                    },
                },
                {
                    "coordinates": ["t"],
                    "system": {"type": "TemporalRS", "calendar": "Gregorian"},
                },
            ],
        },
        "parameters": {
            "TEMP": {
                "type": "Parameter",
                "unit": {
                    "label": {"en": "Degree Celsius"},
                    "symbol": {
                        "value": "Cel",
                        "type": "http://www.opengis.net/def/uom/UCUM/",
                    },
                },
                "observedProperty": {
                    "label": {"en": "Average air temperature"},
                },
            }
        },
        "ranges": {
            "TEMP": {
                "type": "NdArray",
                "dataType": "float",
                "axisNames": ["t"],
                "shape": [3],
                "values": [17.3, 18.8, 20.8],
            }
        },
    }

    def test_parses(self) -> None:
        """Playground polygonseries.covjson parses with PolygonSeries domainType."""
        cov = parse(Coverage, self.EXAMPLE)
        assert cov.domain.domainType is not None
        assert cov.domain.domainType.value == "PolygonSeries"

    def test_polygon_composite_axis(self) -> None:
        """PolygonSeries composite axis has dataType 'polygon' and one ring set."""
        cov = parse(Coverage, self.EXAMPLE)
        composite = cov.domain.axes.composite
        assert composite is not None
        assert composite.dataType == "polygon"
        assert len(composite.values) == 1

    def test_polygon_coordinates_preserved(self) -> None:
        """Polygon ring coordinates survive round-trip unchanged."""
        result = roundtrip(Coverage, self.EXAMPLE)
        ring = result["domain"]["axes"]["composite"]["values"][0][0]
        assert ring[0] == [9.92, 54.98]
        assert ring[-1] == [9.92, 54.98]
        assert len(ring) == 20

    def test_t_axis_length(self) -> None:
        """PolygonSeries t axis has three time steps."""
        cov = parse(Coverage, self.EXAMPLE)
        assert isinstance(cov.domain.axes.t, ValuesAxis)
        assert len(cov.domain.axes.t.values) == 3

    def test_roundtrip_stable(self) -> None:
        """Playground polygonseries.covjson Coverage round-trips stably."""
        assert roundtrip_is_stable(Coverage, self.EXAMPLE)

    def test_schema_valid(self) -> None:
        """Playground polygonseries.covjson Coverage validates against the schema."""
        assert_schema_valid(parse(Coverage, self.EXAMPLE))
