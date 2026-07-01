import pytest
from titiler.core.errors import BadRequestError

from titiler_covjson.dependencies import (
    CovJSONBandParams,
    to_kwargs,
    validate_covjson_format,
)


@pytest.mark.parametrize(
    ("parameter_name", "indexes", "expression"),
    [
        ("", None, None),
        (None, (), None),
        (None, None, ""),
    ],
    ids=["empty-parameter-name", "empty-indexes", "empty-expression"],
)
def test_empty_selectors_yield_empty_kwargs(
    parameter_name: str | None,
    indexes: tuple[int, ...] | None,
    expression: str | None,
) -> None:
    # An empty-but-present selector (e.g., ?expression=) must normalize to
    # "absent", not leak into Reader.part() kwargs.
    params = CovJSONBandParams(
        parameter_name=parameter_name,
        indexes=indexes,
        expression=expression,
    )
    assert to_kwargs(params) == {}


@pytest.mark.parametrize(
    ("parameter_name", "indexes", "expression"),
    [
        ("b1", (2,), None),
        ("b1", None, "b1/b2"),
        (None, (2,), "b1/b2"),
        ("b1", (2,), "b1/b2"),
    ],
    ids=[
        "parameter-name+bidx",
        "parameter-name+expression",
        "bidx+expression",
        "all-three",
    ],
)
def test_mutually_exclusive_selectors_rejected(
    parameter_name: str | None,
    indexes: tuple[int, ...] | None,
    expression: str | None,
) -> None:
    with pytest.raises(BadRequestError, match="Supply only one"):
        CovJSONBandParams(
            parameter_name=parameter_name,
            indexes=indexes,
            expression=expression,
        )


def test_malformed_parameter_name_rejected() -> None:
    with pytest.raises(BadRequestError, match="Invalid parameter-name"):
        CovJSONBandParams(parameter_name="temperature")


@pytest.mark.parametrize("value", ["CoverageJSON", "coveragejson", "COVERAGEJSON"])
def test_format_coveragejson_accepted_case_insensitive(value: str) -> None:
    # CoverageJSON in any case is accepted: no exception raised.
    validate_covjson_format(value)
