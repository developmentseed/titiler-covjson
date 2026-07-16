"""Tests for the shared schema-validation helpers in conftest."""

from __future__ import annotations

from typing import Any

import jsonschema
import pytest
from conftest import validate_covjson

# A Grid domain missing the mandatory `y` axis. The CoverageJSON schema keeps
# every per-domainType rule (the axes each domain requires, and their shapes)
# under `domainBase.dependencies`, so this instance is well-formed as a bare
# domain and invalid only once the Grid rule applies.
_GRID_DOMAIN_MISSING_Y: dict[str, Any] = {
    "type": "Domain",
    "domainType": "Grid",
    "axes": {"x": {"start": 0.0, "stop": 1.0, "num": 2}},
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


class TestValidateCovjson:
    """Test the schema-validation helper's handling of named definitions."""

    def test_named_definition_enforces_per_domain_type_rules(self) -> None:
        """Test that a named definition applies the rules for its domainType.

        The helper validates a named definition against a wrapper schema rather
        than the root, so the wrapper must say which JSON Schema version it is
        written for. Left unsaid, it is read as the newest (2020-12) rather than
        the draft-07 the CoverageJSON schema targets, and 2020-12 does not assert
        on `dependencies` -- the keyword every per-domainType rule sits under.
        The rules then pass by being ignored, not by holding.
        """
        with pytest.raises(jsonschema.ValidationError, match="'y' is a required"):
            validate_covjson(_GRID_DOMAIN_MISSING_Y, "domain")
