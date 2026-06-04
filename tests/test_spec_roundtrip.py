"""Round-trip tests for CoverageJSON spec section 9 examples.

Each test class targets a specific subsection of OGC Community Standard 21-069r2,
section 9 ("CoverageJSON Object Types and Examples"). Tests check the following:

1. Spec example JSON parses into the correct pydantic model.
2. The parsed model round-trips stably: serialise -> parse -> re-serialise produces
   identical JSON (i.e. the representation is canonical).

Where a helper function produces the relevant object, a companion test verifies that
its output is structurally consistent with the spec and also round-trips stably.
"""

# ---------------------------------------------------------------------------
# Spec model gaps — features that cannot currently be tested
# ---------------------------------------------------------------------------
#
# Domain types absent from the DomainType enum (spec OGC 21-069r2 §9.10):
#
#   - Section            (§9.10.8)
#   - Polygon            (§9.10.9 -— standalone single-polygon, distinct from
#                                    PolygonSeries)
#   - MultiPolygon       (§9.10.11)
#   - MultiPolygonSeries (§9.10.12)
#
#   All four are accepted by the spec but raise a ValidationError from the
#   DomainType enum validator, so no roundtrip test is possible until they
#   are added to the enum.
#
# NdArray / TiledNdArray gaps:
#   TiledNdArrayInt / TiledNdArrayStr —- the spec allows integer and string
#   dataType on TiledNdArray objects, but no model class exists for them.
#   Only TiledNdArrayFloat is implemented (see coverage.py NdArrayTypes and
#   ndarray.py). Upstream issue: https://github.com/KNMI/covjson-pydantic/issues/31

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest
import rasterio
from conftest import assert_schema_valid, parse, roundtrip, roundtrip_is_stable
from covjson_pydantic.domain import Domain, ValuesAxis
from covjson_pydantic.ndarray import (
    NdArrayFloat,
    NdArrayInt,
    NdArrayStr,
    TiledNdArrayFloat,
)
from covjson_pydantic.parameter import Parameter, ParameterGroup
from covjson_pydantic.reference_system import (
    ReferenceSystem,
    ReferenceSystemConnectionObject,
)
from covjson_pydantic.unit import Symbol, Unit

from titiler_covjson.helpers import (
    create_spatial_2d_reference,
    create_temporal_reference,
    create_unit,
    numpy_dtype_to_ndarray,
)


class TestSection93Units:
    """Spec section 9.3: Unit objects extracted from the Parameter examples."""

    # Unit from the SST parameter example (degrees Celsius).
    SPEC_UNIT_CELSIUS: dict[str, Any] = {
        "label": {"en": "Degree Celsius"},
        "symbol": {
            "value": "Cel",
            "type": "http://www.opengis.net/def/uom/UCUM/",
        },
    }

    # Supplementary unit example (Kelvin) – not a direct spec quote, constructed
    # to exercise a second UCUM symbol and the label field independently.
    SPEC_UNIT_KELVIN: dict[str, Any] = {
        "label": {"en": "Kelvin"},
        "symbol": {
            "value": "K",
            "type": "http://www.opengis.net/def/uom/UCUM/",
        },
    }

    def test_spec_unit_celsius_parses(self) -> None:
        """Spec 9.3 Celsius unit example parses with correct symbol."""
        unit = parse(Unit, self.SPEC_UNIT_CELSIUS)
        assert unit.symbol == Symbol(
            value="Cel", type="http://www.opengis.net/def/uom/UCUM/"
        )

    def test_spec_unit_celsius_roundtrip_stable(self) -> None:
        """Spec Celsius unit example round-trips to identical JSON."""
        assert roundtrip_is_stable(Unit, self.SPEC_UNIT_CELSIUS)

    def test_spec_unit_kelvin_roundtrip_stable(self) -> None:
        """Spec Kelvin unit example round-trips to identical JSON."""
        assert roundtrip_is_stable(Unit, self.SPEC_UNIT_KELVIN)

    def test_helper_unit_celsius_symbol_matches_spec(self) -> None:
        """create_unit('Cel') produces a UCUM symbol matching the spec 9.3 example."""
        unit = create_unit("Cel")
        assert unit is not None
        assert unit.symbol == Symbol(
            value="Cel", type="http://www.opengis.net/def/uom/UCUM/"
        )

    def test_helper_unit_kelvin_label_and_symbol_match_spec(self) -> None:
        """create_unit('K') produces the correct Kelvin label and UCUM symbol."""
        unit = create_unit("K")
        assert unit is not None
        assert unit.label == {"en": "Kelvin"}
        assert unit.symbol is not None
        assert not isinstance(unit.symbol, str)
        assert unit.symbol.value == "K"

    def test_helper_unit_celsius_roundtrip_stable(self) -> None:
        """create_unit('Cel') output round-trips to identical JSON."""
        unit = create_unit("Cel")
        assert unit is not None
        first = unit.model_dump()
        second = parse(Unit, first).model_dump()
        assert first == second


class TestSection93Parameter:
    """Spec section 9.3: full Parameter object examples."""

    # Continuous scalar parameter (sea surface temperature).
    SPEC_SST: dict[str, Any] = {
        "type": "Parameter",
        "description": {"en": "The sea surface temperature in degrees Celsius."},
        "observedProperty": {
            "id": "http://vocab.nerc.ac.uk/standard_name/sea_surface_temperature/",
            "label": {"en": "Sea Surface Temperature"},
            "description": {
                "en": (
                    "The temperature of sea water near the surface"
                    " (including the part under sea-ice, if any),"
                    " and not the skin temperature."
                )
            },
        },
        "unit": {
            "label": {"en": "Degree Celsius"},
            "symbol": {
                "value": "Cel",
                "type": "http://www.opengis.net/def/uom/UCUM/",
            },
        },
    }

    # Categorical parameter (land cover) with categoryEncoding.
    SPEC_LAND_COVER: dict[str, Any] = {
        "type": "Parameter",
        "description": {"en": "The land cover category."},
        "observedProperty": {
            "id": "http://example.com/land_cover",
            "label": {"en": "Land Cover"},
            "description": {"en": "longer description..."},
            "categories": [
                {
                    "id": "http://example.com/land_cover/categories/grass",
                    "label": {"en": "Grass"},
                    "description": {"en": "Very green grass."},
                },
                {
                    "id": "http://example.com/land_cover/categories/forest",
                    "label": {"en": "Forest"},
                },
            ],
        },
        "categoryEncoding": {
            "http://example.com/land_cover/categories/grass": 1,
            "http://example.com/land_cover/categories/forest": [2, 3],
        },
    }

    def test_spec_sst_parameter_parses(self) -> None:
        """Spec 9.3 continuous SST parameter parses with correct unit symbol."""
        p = parse(Parameter, self.SPEC_SST)
        assert p.unit is not None
        assert not isinstance(p.unit.symbol, str)
        assert p.unit.symbol is not None
        assert p.unit.symbol.value == "Cel"
        assert p.observedProperty.categories is None

    def test_spec_sst_parameter_roundtrip_stable(self) -> None:
        """Spec 9.3 continuous SST parameter round-trips to identical JSON."""
        assert roundtrip_is_stable(Parameter, self.SPEC_SST)

    def test_spec_sst_parameter_schema_valid(self) -> None:
        """Spec 9.3 SST parameter validates against the schema 'parameter' def."""
        assert_schema_valid(parse(Parameter, self.SPEC_SST), "parameter")

    def test_spec_land_cover_parses(self) -> None:
        """Spec 9.3 categorical land cover parameter parses correctly."""
        p = parse(Parameter, self.SPEC_LAND_COVER)
        assert p.unit is None
        assert p.observedProperty.categories is not None
        assert len(p.observedProperty.categories) == 2
        assert p.observedProperty.categories[0].id == (
            "http://example.com/land_cover/categories/grass"
        )
        assert p.categoryEncoding is not None
        assert p.categoryEncoding["http://example.com/land_cover/categories/grass"] == 1
        assert p.categoryEncoding[
            "http://example.com/land_cover/categories/forest"
        ] == [2, 3]

    def test_spec_land_cover_roundtrip_stable(self) -> None:
        """Spec 9.3 categorical land cover parameter round-trips stably."""
        assert roundtrip_is_stable(Parameter, self.SPEC_LAND_COVER)

    def test_spec_land_cover_schema_valid(self) -> None:
        """Spec 9.3 land cover parameter validates against the 'parameter' def."""
        assert_schema_valid(parse(Parameter, self.SPEC_LAND_COVER), "parameter")

    def test_spec_land_cover_model_dump_roundtrip(self) -> None:
        """Land cover categoryEncoding survives model_dump() round-trip."""
        p = parse(Parameter, self.SPEC_LAND_COVER)
        first = p.model_dump()
        second = parse(Parameter, first).model_dump()
        assert first == second
        assert first["categoryEncoding"][
            "http://example.com/land_cover/categories/forest"
        ] == [2, 3]


# ---------------------------------------------------------------------------
# Section 9.4 – ParameterGroup objects
# ---------------------------------------------------------------------------


class TestSection94ParameterGroup:
    """Spec section 9.4: ParameterGroup objects."""

    # Vector quantity group – no id, no label, only observedProperty.
    SPEC_WIND_GROUP: dict[str, Any] = {
        "type": "ParameterGroup",
        "observedProperty": {
            "label": {"en": "Wind velocity"},
        },
        "members": ["WIND_SPEED", "WIND_DIR"],
    }

    # Uncertainty information group – has both label and observedProperty.
    SPEC_SST_UNCERTAINTY_GROUP: dict[str, Any] = {
        "type": "ParameterGroup",
        "label": {"en": "Daily sea surface temperature with uncertainty information"},
        "observedProperty": {
            "id": "http://vocab.nerc.ac.uk/standard_name/sea_surface_temperature/",
            "label": {"en": "Sea surface temperature"},
        },
        "members": ["SST_mean", "SST_stddev"],
    }

    def test_spec_wind_group_parses(self) -> None:
        """Spec 9.4 wind velocity group parses with correct members."""
        g = parse(ParameterGroup, self.SPEC_WIND_GROUP)
        assert g.type == "ParameterGroup"
        assert g.members == ["WIND_SPEED", "WIND_DIR"]
        assert g.observedProperty is not None
        assert g.label is None

    def test_spec_wind_group_roundtrip_stable(self) -> None:
        """Spec 9.4 wind velocity group round-trips to identical JSON."""
        assert roundtrip_is_stable(ParameterGroup, self.SPEC_WIND_GROUP)

    def test_spec_wind_group_schema_valid(self) -> None:
        """Spec 9.4 wind group validates against the 'parameterGroup' def."""
        assert_schema_valid(
            parse(ParameterGroup, self.SPEC_WIND_GROUP), "parameterGroup"
        )

    def test_spec_sst_uncertainty_group_parses(self) -> None:
        """Spec 9.4 SST uncertainty group parses with label and members."""
        g = parse(ParameterGroup, self.SPEC_SST_UNCERTAINTY_GROUP)
        assert g.type == "ParameterGroup"
        assert g.members == ["SST_mean", "SST_stddev"]
        assert g.label == {
            "en": "Daily sea surface temperature with uncertainty information"
        }
        assert g.observedProperty is not None

    def test_spec_sst_uncertainty_group_roundtrip_stable(self) -> None:
        """Spec 9.4 SST uncertainty group round-trips to identical JSON."""
        assert roundtrip_is_stable(ParameterGroup, self.SPEC_SST_UNCERTAINTY_GROUP)

    def test_spec_sst_uncertainty_group_schema_valid(self) -> None:
        """Spec 9.4 SST uncertainty group validates against the 'parameterGroup' def."""
        assert_schema_valid(
            parse(ParameterGroup, self.SPEC_SST_UNCERTAINTY_GROUP), "parameterGroup"
        )


# ---------------------------------------------------------------------------
# Section 9.5.1.1 – Geographic CRS
# ---------------------------------------------------------------------------


class TestSection951GeographicCRS:
    """Spec section 9.5.1.1: Geographic CRS."""

    # Spec example uses the specific /1.3/ versioned URI for OGC CRS84.
    # Both spec and helper use the OGC-versioned URI .../OGC/1.3/CRS84
    SPEC_GEO_CRS84: dict[str, Any] = {
        "type": "GeographicCRS",
        "id": "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
    }
    SPEC_GEO_EPSG4979: dict[str, Any] = {
        "type": "GeographicCRS",
        "id": "http://www.opengis.net/def/crs/EPSG/0/4979",
    }

    def test_spec_geographic_crs84_parses(self) -> None:
        """Spec 9.5.1.1 GeographicCRS (OGC CRS84) example parses correctly."""
        rs = parse(ReferenceSystem, self.SPEC_GEO_CRS84)
        assert rs.type == "GeographicCRS"
        assert rs.id == "http://www.opengis.net/def/crs/OGC/1.3/CRS84"

    def test_spec_geographic_crs84_connection_roundtrip_stable(self) -> None:
        """Spec GeographicCRS (OGC CRS84) connection object round-trips stably."""
        spec = {"coordinates": ["x", "y"], "system": self.SPEC_GEO_CRS84}
        assert roundtrip_is_stable(ReferenceSystemConnectionObject, spec)

    def test_spec_geographic_epsg4979_roundtrip_stable(self) -> None:
        """Spec GeographicCRS (EPSG:4979, 3-D) connection object round-trips stably."""
        spec = {"coordinates": ["y", "x", "z"], "system": self.SPEC_GEO_EPSG4979}
        assert roundtrip_is_stable(ReferenceSystemConnectionObject, spec)

    def test_helper_geographic_crs84_type_and_uri(self) -> None:
        """create_spatial_2d_reference(CRS84) produces GeographicCRS with spec URI."""
        ref = create_spatial_2d_reference(rasterio.CRS.from_string("OGC:CRS84"))
        assert ref.system.type == "GeographicCRS"
        assert ref.coordinates == ["x", "y"]
        assert ref.system.id == "http://www.opengis.net/def/crs/OGC/1.3/CRS84"

    def test_helper_geographic_epsg4326_uri(self) -> None:
        """create_spatial_2d_reference(EPSG:4326) produces the correct EPSG URI."""
        ref = create_spatial_2d_reference(rasterio.CRS.from_epsg(4326))
        assert ref.system.type == "GeographicCRS"
        assert ref.system.id == "http://www.opengis.net/def/crs/EPSG/0/4326"

    def test_helper_geographic_crs84_roundtrip_stable(self) -> None:
        """create_spatial_2d_reference(CRS84) output round-trips to identical JSON."""
        ref = create_spatial_2d_reference(rasterio.CRS.from_string("OGC:CRS84"))
        first = ref.model_dump()
        second = parse(ReferenceSystemConnectionObject, first).model_dump()
        assert first == second


# ---------------------------------------------------------------------------
# Section 9.5.1.2 – Projected CRS
# ---------------------------------------------------------------------------


class TestSection951ProjectedCRS:
    """Spec section 9.5.1.2: Projected CRS."""

    SPEC_PROJ_27700: dict[str, Any] = {
        "type": "ProjectedCRS",
        "id": "http://www.opengis.net/def/crs/EPSG/0/27700",
    }

    def test_spec_projected_crs_parses(self) -> None:
        """Spec 9.5.1.2 ProjectedCRS (EPSG:27700) example parses correctly."""
        rs = parse(ReferenceSystem, self.SPEC_PROJ_27700)
        assert rs.type == "ProjectedCRS"
        assert rs.id == "http://www.opengis.net/def/crs/EPSG/0/27700"

    def test_spec_projected_crs_connection_roundtrip_stable(self) -> None:
        """Spec ProjectedCRS (EPSG:27700) connection object round-trips stably."""
        spec = {"coordinates": ["x", "y"], "system": self.SPEC_PROJ_27700}
        assert roundtrip_is_stable(ReferenceSystemConnectionObject, spec)

    def test_helper_projected_epsg27700_type_and_uri(self) -> None:
        """EPSG:27700 yields ProjectedCRS with URI matching spec 9.5.1.2."""
        ref = create_spatial_2d_reference(rasterio.CRS.from_epsg(27700))
        assert ref.system.type == "ProjectedCRS"
        assert ref.system.id == "http://www.opengis.net/def/crs/EPSG/0/27700"
        assert ref.coordinates == ["x", "y"]

    def test_helper_projected_epsg32637_roundtrip_stable(self) -> None:
        """EPSG:32637 output from create_spatial_2d_reference round-trips stably."""
        ref = create_spatial_2d_reference(rasterio.CRS.from_epsg(32637))
        first = ref.model_dump()
        second = parse(ReferenceSystemConnectionObject, first).model_dump()
        assert first == second
        assert first["system"]["type"] == "ProjectedCRS"
        assert first["system"]["id"] == "http://www.opengis.net/def/crs/EPSG/0/32637"


# ---------------------------------------------------------------------------
# Section 9.5.1.3 – Vertical CRS
# ---------------------------------------------------------------------------


class TestSection951VerticalCRS:
    """Spec section 9.5.1.3: Vertical CRS (id-based form)."""

    SPEC_VERT_EPSG5703: dict[str, Any] = {
        "type": "VerticalCRS",
        "id": "http://www.opengis.net/def/crs/EPSG/0/5703",
    }

    def test_spec_vertical_crs_parses(self) -> None:
        """Spec 9.5.1.3 VerticalCRS (EPSG:5703) example parses correctly."""
        rs = parse(ReferenceSystem, self.SPEC_VERT_EPSG5703)
        assert rs.type == "VerticalCRS"
        assert rs.id == "http://www.opengis.net/def/crs/EPSG/0/5703"

    def test_spec_vertical_crs_connection_roundtrip_stable(self) -> None:
        """Spec VerticalCRS (EPSG:5703) connection object round-trips stably."""
        spec = {"coordinates": ["z"], "system": self.SPEC_VERT_EPSG5703}
        assert roundtrip_is_stable(ReferenceSystemConnectionObject, spec)


# ---------------------------------------------------------------------------
# Section 9.5.2 – Temporal Reference System
# ---------------------------------------------------------------------------


class TestSection952TemporalRS:
    """Spec section 9.5.2: Temporal Reference System."""

    # Just the system object from the spec.
    SPEC_TEMPORAL_RS: dict[str, Any] = {
        "type": "TemporalRS",
        "calendar": "Gregorian",
    }

    # Full connection object as it appears in domain referencing arrays.
    SPEC_TEMPORAL_CONNECTION: dict[str, Any] = {
        "coordinates": ["t"],
        "system": {"type": "TemporalRS", "calendar": "Gregorian"},
    }

    def test_spec_temporal_rs_parses(self) -> None:
        """Spec 9.5.2 TemporalRS example parses with correct type and calendar."""
        rs = parse(ReferenceSystem, self.SPEC_TEMPORAL_RS)
        assert rs.type == "TemporalRS"
        assert rs.calendar == "Gregorian"

    def test_spec_temporal_connection_roundtrip_stable(self) -> None:
        """Spec temporal reference connection object round-trips stably."""
        assert roundtrip_is_stable(
            ReferenceSystemConnectionObject, self.SPEC_TEMPORAL_CONNECTION
        )

    def test_helper_create_temporal_reference_type_and_calendar(self) -> None:
        """create_temporal_reference() yields ['t'] coords and Gregorian calendar."""
        ref = create_temporal_reference()
        assert ref.coordinates == ["t"]
        assert ref.system.type == "TemporalRS"
        assert ref.system.calendar == "Gregorian"

    def test_helper_temporal_reference_roundtrip_stable(self) -> None:
        """create_temporal_reference() output round-trips to identical JSON."""
        ref = create_temporal_reference()
        first = ref.model_dump()
        second = parse(ReferenceSystemConnectionObject, first).model_dump()
        assert first == second


# ---------------------------------------------------------------------------
# Section 9.5.3 – Identifier-based Reference System
# ---------------------------------------------------------------------------


class TestSection953IdentifierRS:
    """Spec section 9.5.3: Identifier-based Reference System (ISO 3166 example)."""

    SPEC_IDENTIFIER_RS: dict[str, Any] = {
        "type": "IdentifierRS",
        "id": "https://en.wikipedia.org/wiki/ISO_3166-1_alpha-2",
        "label": {"en": "ISO 3166-1 alpha-2 codes"},
        "targetConcept": {
            "id": "http://dbpedia.org/resource/Country",
            "label": {"en": "Country", "de": "Land"},
        },
        "identifiers": {
            "de": {
                "id": "http://dbpedia.org/resource/Germany",
                "label": {"de": "Deutschland", "en": "Germany"},
            },
            "gb": {
                "id": "http://dbpedia.org/resource/United_Kingdom",
                "label": {"de": "Vereinigtes Königreich", "en": "United Kingdom"},
            },
        },
    }

    def test_spec_identifier_rs_parses(self) -> None:
        """Spec 9.5.3 IdentifierRS example parses with correct fields."""
        rs = parse(ReferenceSystem, self.SPEC_IDENTIFIER_RS)
        assert rs.type == "IdentifierRS"
        assert rs.id == "https://en.wikipedia.org/wiki/ISO_3166-1_alpha-2"
        assert rs.label == {"en": "ISO 3166-1 alpha-2 codes"}
        assert rs.targetConcept is not None
        assert rs.targetConcept.label == {"en": "Country", "de": "Land"}
        assert rs.identifiers is not None
        assert rs.identifiers["de"].label == {"de": "Deutschland", "en": "Germany"}

    def test_spec_identifier_rs_roundtrip_stable(self) -> None:
        """Spec 9.5.3 IdentifierRS example round-trips to identical JSON."""
        assert roundtrip_is_stable(ReferenceSystem, self.SPEC_IDENTIFIER_RS)

    def test_spec_identifier_rs_multilingual_labels_preserved(self) -> None:
        """Both language tags survive round-trip serialisation."""
        result = roundtrip(ReferenceSystem, self.SPEC_IDENTIFIER_RS)
        assert result["identifiers"]["gb"]["label"]["de"] == "Vereinigtes Königreich"
        assert result["identifiers"]["gb"]["label"]["en"] == "United Kingdom"
        assert result["targetConcept"]["label"]["de"] == "Land"


# ---------------------------------------------------------------------------
# Section 9.6 – Domain objects
# ---------------------------------------------------------------------------


class TestSection96GridDomain:
    """Spec section 9.6: Grid domain example."""

    # Spec example from the Grid Domain subsection.
    SPEC_GRID_DOMAIN: dict[str, Any] = {
        "type": "Domain",
        "domainType": "Grid",
        "axes": {
            "x": {"values": [1, 2, 3]},
            "y": {"values": [20, 21]},
            "z": {"values": [1]},
            "t": {"values": ["2008-01-01T04:00:00Z"]},
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
    }

    def test_spec_grid_domain_parses(self) -> None:
        """Spec 9.6 Grid Domain example parses correctly."""
        domain = parse(Domain, self.SPEC_GRID_DOMAIN)
        assert domain.domainType is not None
        assert domain.domainType.value == "Grid"
        assert domain.axes.x is not None
        assert domain.axes.y is not None
        assert domain.axes.z is not None
        assert domain.axes.t is not None
        assert domain.referencing is not None
        assert len(domain.referencing) == 2

    def test_spec_grid_domain_axis_values(self) -> None:
        """Spec 9.6 Grid Domain x, y, z axis values parse with correct lengths."""
        domain = parse(Domain, self.SPEC_GRID_DOMAIN)
        assert isinstance(domain.axes.x, ValuesAxis)
        assert len(domain.axes.x.values) == 3
        assert isinstance(domain.axes.y, ValuesAxis)
        assert len(domain.axes.y.values) == 2
        assert isinstance(domain.axes.z, ValuesAxis)
        assert len(domain.axes.z.values) == 1

    def test_spec_grid_domain_roundtrip_stable(self) -> None:
        """Spec 9.6 Grid Domain round-trips to identical JSON."""
        assert roundtrip_is_stable(Domain, self.SPEC_GRID_DOMAIN)

    def test_spec_grid_domain_schema_valid(self) -> None:
        """Spec 9.6 Grid Domain validates against the schema 'domain' def."""
        assert_schema_valid(parse(Domain, self.SPEC_GRID_DOMAIN), "domain")

    def test_spec_grid_domain_referencing_preserved(self) -> None:
        """Spec 9.6 Grid Domain referencing types survive round-trip."""
        result = roundtrip(Domain, self.SPEC_GRID_DOMAIN)
        assert result["referencing"] is not None
        system_types = {ref["system"]["type"] for ref in result["referencing"]}
        assert "TemporalRS" in system_types
        assert "GeographicCRS" in system_types


class TestSection96TrajectoryDomain:
    """Spec section 9.6: Trajectory domain example."""

    # Spec example from the Trajectory Domain subsection.
    SPEC_TRAJECTORY_DOMAIN: dict[str, Any] = {
        "type": "Domain",
        "domainType": "Trajectory",
        "axes": {
            "composite": {
                "dataType": "tuple",
                "coordinates": ["t", "x", "y"],
                "values": [
                    ["2008-01-01T04:00:00Z", 1, 20],
                    ["2008-01-01T04:30:00Z", 2, 21],
                ],
            }
        },
        "referencing": [
            {
                "coordinates": ["t"],
                "system": {"type": "TemporalRS", "calendar": "Gregorian"},
            },
            {
                "coordinates": ["x", "y"],
                "system": {
                    "type": "GeographicCRS",
                    "id": "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
                },
            },
        ],
    }

    def test_spec_trajectory_domain_parses(self) -> None:
        """Spec 9.6 Trajectory Domain example parses correctly."""
        domain = parse(Domain, self.SPEC_TRAJECTORY_DOMAIN)
        assert domain.domainType is not None
        assert domain.domainType.value == "Trajectory"
        assert domain.axes.composite is not None
        assert len(domain.axes.composite.values) == 2

    def test_spec_trajectory_domain_roundtrip_stable(self) -> None:
        """Spec 9.6 Trajectory Domain round-trips to identical JSON."""
        assert roundtrip_is_stable(Domain, self.SPEC_TRAJECTORY_DOMAIN)

    def test_spec_trajectory_domain_schema_valid(self) -> None:
        """Spec 9.6 Trajectory Domain validates against the schema 'domain' def."""
        assert_schema_valid(parse(Domain, self.SPEC_TRAJECTORY_DOMAIN), "domain")

    def test_spec_trajectory_composite_coordinates_preserved(self) -> None:
        """Spec 9.6 Trajectory composite axis coordinates survive round-trip."""
        result = roundtrip(Domain, self.SPEC_TRAJECTORY_DOMAIN)
        composite = result["axes"]["composite"]
        assert composite["dataType"] == "tuple"
        assert composite["coordinates"] == ["t", "x", "y"]
        assert len(composite["values"]) == 2


class TestSection96MultiPoint:
    """Spec section 9.6 / 9.10.6: MultiPoint domain (composite axis only)."""

    SPEC_MULTI_POINT: dict[str, Any] = {
        "type": "Domain",
        "domainType": "MultiPoint",
        "axes": {
            "composite": {
                "dataType": "tuple",
                "coordinates": ["x", "y"],
                "values": [[-10.0, 40.0], [-5.0, 50.0], [0.0, 45.0]],
            }
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

    def test_spec_multi_point_parses(self) -> None:
        """MultiPoint domain parses with composite axis and no t axis."""
        domain = parse(Domain, self.SPEC_MULTI_POINT)
        assert domain.domainType is not None
        assert domain.domainType.value == "MultiPoint"
        assert domain.axes.composite is not None
        assert domain.axes.t is None

    def test_spec_multi_point_composite_values(self) -> None:
        """MultiPoint composite axis carries three (x, y) coordinate pairs."""
        domain = parse(Domain, self.SPEC_MULTI_POINT)
        assert domain.axes.composite is not None
        assert domain.axes.composite.coordinates == ["x", "y"]
        assert len(domain.axes.composite.values) == 3

    def test_spec_multi_point_roundtrip_stable(self) -> None:
        """MultiPoint domain round-trips to identical JSON."""
        assert roundtrip_is_stable(Domain, self.SPEC_MULTI_POINT)

    def test_spec_multi_point_schema_valid(self) -> None:
        """MultiPoint domain validates against the schema 'domain' def."""
        assert_schema_valid(parse(Domain, self.SPEC_MULTI_POINT), "domain")


class TestSection96MultiPointSeries:
    """Spec section 9.6 / 9.10.5: MultiPointSeries domain (composite + t axes)."""

    SPEC_MULTI_POINT_SERIES: dict[str, Any] = {
        "type": "Domain",
        "domainType": "MultiPointSeries",
        "axes": {
            "composite": {
                "dataType": "tuple",
                "coordinates": ["x", "y"],
                "values": [[-10.0, 40.0], [-5.0, 50.0]],
            },
            "t": {
                "values": [
                    "2008-01-01T00:00:00Z",
                    "2008-01-02T00:00:00Z",
                    "2008-01-03T00:00:00Z",
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
    }

    def test_spec_multi_point_series_parses(self) -> None:
        """MultiPointSeries domain parses with both composite and t axes."""
        domain = parse(Domain, self.SPEC_MULTI_POINT_SERIES)
        assert domain.domainType is not None
        assert domain.domainType.value == "MultiPointSeries"
        assert domain.axes.composite is not None
        assert domain.axes.t is not None

    def test_spec_multi_point_series_axes(self) -> None:
        """MultiPointSeries has two points and three time steps."""
        domain = parse(Domain, self.SPEC_MULTI_POINT_SERIES)
        assert domain.axes.composite is not None
        assert len(domain.axes.composite.values) == 2
        assert isinstance(domain.axes.t, ValuesAxis)
        assert len(domain.axes.t.values) == 3

    def test_spec_multi_point_series_roundtrip_stable(self) -> None:
        """MultiPointSeries domain round-trips to identical JSON."""
        assert roundtrip_is_stable(Domain, self.SPEC_MULTI_POINT_SERIES)

    def test_spec_multi_point_series_schema_valid(self) -> None:
        """MultiPointSeries domain validates against the schema 'domain' def."""
        assert_schema_valid(parse(Domain, self.SPEC_MULTI_POINT_SERIES), "domain")


# ---------------------------------------------------------------------------
# Section 9.6.2 – NdArray objects
# ---------------------------------------------------------------------------


class TestSection962NdArrayRoundtrip:
    """Spec section 9.6.2: NdArray objects."""

    # Exact spec example – float array with two null (missing) values.
    SPEC_NDARRAY_FLOAT: dict[str, Any] = {
        "type": "NdArray",
        "dataType": "float",
        "shape": [4, 2],
        "axisNames": ["y", "x"],
        "values": [12.3, 12.5, 11.5, 23.1, None, None, 10.1, 9.1],
    }

    no_missing_values = [12.3, 12.5, 11.5, 23.1, 99.0, 99.0, 10.1, 9.1]
    mask = [False, False, False, False, True, True, False, False]

    def test_spec_ndarray_float_parses(self) -> None:
        """Spec 9.6.2 float NdArray example parses into NdArrayFloat."""
        nd = parse(NdArrayFloat, self.SPEC_NDARRAY_FLOAT)
        assert nd.dataType == "float"
        assert nd.shape == [4, 2]
        assert nd.axisNames == ["y", "x"]
        assert nd.values is not None
        assert nd.values[0] == pytest.approx(12.3)
        # Null values parse as Python None.
        assert nd.values[4] is None
        assert nd.values[5] is None

    def test_spec_ndarray_float_roundtrip_stable(self) -> None:
        """Spec 9.6.2 float NdArray round-trips to identical JSON."""
        assert roundtrip_is_stable(NdArrayFloat, self.SPEC_NDARRAY_FLOAT)

    def test_spec_ndarray_float_schema_valid(self) -> None:
        """Spec 9.6.2 float NdArray validates against the schema 'ndArray' def."""
        assert_schema_valid(parse(NdArrayFloat, self.SPEC_NDARRAY_FLOAT), "ndArray")

    def test_spec_ndarray_null_values_survive_roundtrip(self) -> None:
        """Null values in the spec example remain null after serialisation."""
        result = roundtrip(NdArrayFloat, self.SPEC_NDARRAY_FLOAT)
        assert result["values"][4] is None
        assert result["values"][5] is None

    def test_helper_masked_floats_are_nan(self) -> None:
        """numpy_dtype_to_ndarray masked floats are NaN in model_dump() output."""
        arr = np.ma.array(self.no_missing_values, mask=self.mask).reshape(4, 2)
        nd = numpy_dtype_to_ndarray(arr, np.float64, ["y", "x"])
        data = nd.model_dump()

        assert data["type"] == "NdArray"
        assert data["dataType"] == "float"
        assert data["shape"] == [4, 2]
        assert data["axisNames"] == ["y", "x"]
        assert math.isnan(data["values"][4])
        assert math.isnan(data["values"][5])
        assert data["values"][0] == pytest.approx(12.3)

    def test_helper_float_ndarray_roundtrip_stable(self) -> None:
        """Helper-produced NdArrayFloat round-trips to identical JSON."""
        arr = np.ma.array(self.no_missing_values, mask=self.mask).reshape(4, 2)
        nd = numpy_dtype_to_ndarray(arr, np.float64, ["y", "x"])
        json_str = nd.model_dump_json()
        assert json_str == NdArrayFloat.model_validate_json(json_str).model_dump_json()

    def test_helper_integer_ndarray_roundtrip_stable(self) -> None:
        """Helper-produced NdArrayInt with masked values round-trips stably."""
        arr = np.ma.array([10, 20, 30], mask=[False, True, False])
        nd = numpy_dtype_to_ndarray(arr, np.int32, ["values"])
        first = nd.model_dump()
        assert first["dataType"] == "integer"
        assert first["values"][1] is None
        second = parse(NdArrayInt, first).model_dump()
        assert first == second


class TestSection962NdArrayStr:
    """Spec section 9.6.2: NdArray with string dataType."""

    SPEC_NDARRAY_STR: dict[str, Any] = {
        "type": "NdArray",
        "dataType": "string",
        "shape": [3],
        "axisNames": ["x"],
        "values": ["clear", None, "cloudy"],
    }

    def test_spec_ndarray_string_parses(self) -> None:
        """String NdArray example parses with correct dataType and null handling."""
        nd = parse(NdArrayStr, self.SPEC_NDARRAY_STR)
        assert nd.dataType == "string"
        assert nd.shape == [3]
        assert nd.values is not None
        assert nd.values[0] == "clear"
        assert nd.values[1] is None
        assert nd.values[2] == "cloudy"

    def test_spec_ndarray_string_roundtrip_stable(self) -> None:
        """String NdArray round-trips to identical JSON."""
        assert roundtrip_is_stable(NdArrayStr, self.SPEC_NDARRAY_STR)

    def test_spec_ndarray_string_schema_valid(self) -> None:
        """String NdArray validates against the schema 'ndArray' def."""
        assert_schema_valid(parse(NdArrayStr, self.SPEC_NDARRAY_STR), "ndArray")

    def test_spec_ndarray_string_null_preserved(self) -> None:
        """Null element in string NdArray survives round-trip as null."""
        result = roundtrip(NdArrayStr, self.SPEC_NDARRAY_STR)
        assert result["values"][1] is None


class TestSection962TiledNdArrayFloat:
    """Spec section 9.6.3: TiledNdArray with float dataType and URL templates.

    The model only implements ``TiledNdArrayFloat``; integer and string tiled
    arrays (present in the playground ``grid-tiled.covjson``) cannot be parsed.
    Upstream issue (covjson-pydantic missing TiledNdArrayInt/Str):
    https://github.com/KNMI/covjson-pydantic/issues/31

    No ``test_schema_valid`` here: the vendored schema has no ``TiledNdArray``
    definition (only ``ndArray``), so there is nothing to validate against.
    """

    SPEC_TILED: dict[str, Any] = {
        "type": "TiledNdArray",
        "dataType": "float",
        "axisNames": ["t", "y", "x"],
        "shape": [2, 5, 10],
        "tileSets": [
            {
                "tileShape": [None, 2, 3],
                "urlTemplate": "https://example.com/tiles/{t}-{y}-{x}.covjson",
            },
            {
                "tileShape": [None, None, None],
                "urlTemplate": "https://example.com/tiles/all.covjson",
            },
        ],
    }

    def test_spec_tiled_ndarray_parses(self) -> None:
        """TiledNdArrayFloat parses with correct type, shape and axisNames."""
        nd = parse(TiledNdArrayFloat, self.SPEC_TILED)
        assert nd.type == "TiledNdArray"
        assert nd.dataType == "float"
        assert nd.shape == [2, 5, 10]
        assert nd.axisNames == ["t", "y", "x"]
        assert len(nd.tileSets) == 2

    def test_spec_tiled_ndarray_url_templates_preserved(self) -> None:
        """TiledNdArray urlTemplate values survive round-trip unchanged."""
        result = roundtrip(TiledNdArrayFloat, self.SPEC_TILED)
        assert result["tileSets"][0]["urlTemplate"] == (
            "https://example.com/tiles/{t}-{y}-{x}.covjson"
        )
        assert result["tileSets"][0]["tileShape"] == [None, 2, 3]

    def test_spec_tiled_ndarray_roundtrip_stable(self) -> None:
        """TiledNdArrayFloat round-trips to identical JSON."""
        assert roundtrip_is_stable(TiledNdArrayFloat, self.SPEC_TILED)
