import pytest
from titiler.core.errors import BadRequestError

from titiler_covjson.dependencies import CovJSONBandParams, to_kwargs


def test_parameter_name_resolves_to_indexes() -> None:
    params = CovJSONBandParams(parameter_name="b1,b3")
    assert to_kwargs(params) == {"indexes": (1, 3)}


def test_bidx_passthrough() -> None:
    params = CovJSONBandParams(indexes=(2,))
    assert to_kwargs(params) == {"indexes": (2,)}


def test_expression_passthrough() -> None:
    params = CovJSONBandParams(expression="b1/b2")
    assert to_kwargs(params) == {"expression": "b1/b2"}


def test_no_selector_yields_empty_kwargs() -> None:
    # No selector means "all bands": nothing to pass to Reader.part().
    assert to_kwargs(CovJSONBandParams()) == {}


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
