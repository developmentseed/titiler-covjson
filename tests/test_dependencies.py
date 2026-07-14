import pytest
from titiler.core.errors import BadRequestError

from titiler_covjson.dependencies import (
    CovJSONBandParams,
    area_stat,
    reject_vertical_selection,
    to_kwargs,
    validate_covjson_format,
)
from titiler_covjson.reduce import Stat


def test_area_stat_defaults_to_mean() -> None:
    """An absent `stat` selector defaults to the mean reduction."""
    assert area_stat() is Stat.MEAN


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("min", Stat.MIN),
        ("mean", Stat.MEAN),
        ("median", Stat.MEDIAN),
        ("count", Stat.COUNT),
        ("MEAN", Stat.MEAN),
    ],
    ids=["min", "mean", "median", "count", "case-insensitive"],
)
def test_area_stat_parses_known_values(value: str, expected: Stat) -> None:
    """Each supported statistic name resolves to its Stat member (case-folded)."""
    assert area_stat(value) is expected


def test_area_stat_rejects_unknown() -> None:
    """An unrecognized statistic is a 400, not a 422 or a silent default."""
    with pytest.raises(BadRequestError, match="Unsupported stat 'bogus'"):
        area_stat("bogus")


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
    # CoverageJSON in any case is accepted: the validator returns None and does
    # not raise. (Rejection of other values is covered by the dependencies.py
    # doctest and the route test in test_factory.py.)
    validate_covjson_format(value)


def test_reject_vertical_selection_rejects_present_z() -> None:
    with pytest.raises(BadRequestError, match="Vertical selection is not"):
        reject_vertical_selection("850")


@pytest.mark.parametrize("z", ["", None], ids=["empty", "absent"])
def test_reject_vertical_selection_accepts_absent_z(z: str | None) -> None:
    # Empty-is-absent: a valueless ?z= normalizes to "no vertical selection",
    # matching the other selectors' empty-is-absent handling. No exception means
    # accepted (the None return is covered by the dependencies.py doctest).
    reject_vertical_selection(z)
