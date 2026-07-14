"""Endpoint dependencies specific to the CoverageJSON factory.

These layer the OGC API - Environmental Data Retrieval (EDR) ``parameter-name``
vocabulary and a CoverageJSON format guard on top of the ``titiler.core``
dependency-injectors that the factory otherwise reuses unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Query
from titiler.core.errors import BadRequestError

from titiler_covjson.reduce import Stat

_BAND_NAME = re.compile(r"^b(?P<idx>[1-9][0-9]*)$")


@dataclass
class CovJSONBandParams:
    """Band selection for CoverageJSON: ``parameter-name`` / ``bidx`` / ``expression``.

    The three are mutually exclusive. ``parameter-name`` and ``bidx`` are two
    spellings of band subsetting and ``expression`` is band math.
    ``parameter-name`` is the OGC API - Environmental Data Retrieval (EDR)
    vocabulary for selecting a coverage's parameters (its bands) by name;
    supporting it lets EDR-aligned clients use their native selection idiom,
    while ``bidx`` is the equivalent titiler-native spelling by 1-based index.
    ``parameter-name`` is resolved to those 1-based indexes so that
    :func:`to_kwargs` yields only rio-tiler ``part()`` keyword arguments.

    This is a plain dataclass rather than a ``titiler.core``
    ``DefaultDependency`` subclass: FastAPI dependency injection needs only a
    dataclass, and the one behavior that base provides (``as_dict``) is offered
    here as the free function :func:`to_kwargs` instead of via inheritance.

    Examples:
        ``parameter-name`` is folded into 1-based ``indexes`` at construction,
        and the original ``parameter-name`` is cleared:

        >>> params = CovJSONBandParams(parameter_name="b1,b3")
        >>> params.indexes
        (1, 3)
        >>> params.parameter_name is None
        True

        ``bidx`` (the ``indexes`` field) and ``expression`` are taken as given:

        >>> CovJSONBandParams(indexes=(2,)).indexes
        (2,)
        >>> CovJSONBandParams(expression="b1/b2").expression
        'b1/b2'

        Supplying more than one selector is rejected:

        >>> CovJSONBandParams(indexes=(2,), expression="b1/b2")
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: Supply only one of ...

        An empty-but-present selector (e.g., from ``?expression=``) normalizes
        to ``None`` rather than lingering as an empty value:

        >>> CovJSONBandParams(expression="").expression is None
        True
        >>> CovJSONBandParams(parameter_name="").parameter_name is None
        True
        >>> CovJSONBandParams(indexes=()).indexes is None
        True
    """

    # The HTTP query parameter is ``bidx`` (mapped in by the Query alias during
    # request parsing); the attribute is named ``indexes`` to match rio-tiler's
    # ``Reader.part(indexes=...)`` keyword, so ``to_kwargs`` splats it straight
    # into the read. ``parameter-name`` resolves into this same attribute below.
    indexes: Annotated[
        tuple[int, ...] | None,
        Query(alias="bidx", description="Band indexes (1-based)."),
    ] = None
    expression: Annotated[
        str | None,
        Query(description="rio-tiler band-math expression."),
    ] = None
    parameter_name: Annotated[
        str | None,
        Query(
            alias="parameter-name",
            description=(
                "OGC Environmental Data Retrieval (EDR) band selection by name, "
                "comma-delimited (e.g., 'b1,b3')."
            ),
        ),
    ] = None

    def __post_init__(self) -> None:
        """Enforce exclusivity and fold ``parameter-name`` into ``indexes``.

        Raises:
            BadRequestError: If more than one selector is supplied, or if a
                ``parameter-name`` entry is not a valid band identifier. The
                host application's titiler exception handlers render this as a
                400 response.
        """
        # Normalize empty-but-present selectors (e.g., ?expression= or
        # ?parameter-name=) to None, so the truthiness-based conflict check below
        # and to_kwargs' `is not None` filter agree. Otherwise an empty value
        # would survive as a stray keyword argument into Reader.part().
        self.indexes = self.indexes or None
        self.expression = self.expression or None
        self.parameter_name = self.parameter_name or None

        labels = ("parameter-name", "bidx", "expression")
        values = (self.parameter_name, self.indexes, self.expression)
        supplied = [label for label, value in zip(labels, values, strict=True) if value]

        if len(supplied) > 1:
            msg = (
                f"Supply only one of {', '.join(labels)} "
                f"(received {', '.join(supplied)})."
            )
            raise BadRequestError(msg)

        if self.parameter_name:
            names = self.parameter_name.split(",")
            self.indexes = tuple(map(_band_name_to_index, names))
            self.parameter_name = None


def to_kwargs(dep: object) -> dict[str, Any]:
    """Convert a dependency dataclass's non-``None`` fields to a shallow dict.

    This is the free-function equivalent of ``titiler.core``'s
    ``DefaultDependency.as_dict()`` in its default (``exclude_none=True``) form:
    it collects the set (non-``None``) fields of a request-dependency dataclass
    so they can be splatted straight into a rio-tiler reader call, e.g.,
    ``Reader.part(**to_kwargs(params))``. Values are
    copied shallowly (by reference), so reader-bound objects such as resampling
    enums pass through unchanged. It works on any dependency dataclass (our own
    :class:`CovJSONBandParams` and titiler's ``PartFeatureParams`` /
    ``DatasetParams`` alike), so the factory obtains this behavior without
    inheriting a base class.

    Args:
        dep: A dependency dataclass instance.

    Returns:
        dict[str, Any]: The instance's non-``None`` fields, keyed by field name.

    Examples:
        ``parameter-name`` is resolved by name into rio-tiler ``indexes``:

        >>> to_kwargs(CovJSONBandParams(parameter_name="b1,b3"))
        {'indexes': (1, 3)}

        ``bidx`` is already (an alias of) ``indexes``, so it passes through unchanged:

        >>> to_kwargs(CovJSONBandParams(indexes=(1, 2)))
        {'indexes': (1, 2)}

        ``expression`` is its own key, not folded into ``indexes``:

        >>> to_kwargs(CovJSONBandParams(expression="b1/b2"))
        {'expression': 'b1/b2'}

        With no selector the result is empty, so the reader returns all bands:

        >>> to_kwargs(CovJSONBandParams())
        {}
    """
    return {key: value for key, value in vars(dep).items() if value is not None}


def validate_covjson_format(
    f: Annotated[
        str | None,
        Query(description="Output format. Only 'CoverageJSON' is supported."),
    ] = None,
) -> None:
    """Validate the ``f`` output-format selector for the CoverageJSON endpoint.

    Only ``CoverageJSON`` (case-insensitive) is produced, so an absent or empty
    ``f`` defaults to it and any other explicit value is rejected. This runs as a
    FastAPI dependency for its side effect alone (it returns nothing): the host
    application's titiler exception handlers render the raised error as a 400
    response.

    Args:
        f: The requested output format, or ``None`` when unspecified.

    Raises:
        BadRequestError: If ``f`` is a non-empty value other than
            ``CoverageJSON`` (case-insensitive).

    Examples:
        An absent, empty, or ``CoverageJSON`` value is accepted (the validator
        returns ``None``):

        >>> validate_covjson_format() is None
        True
        >>> validate_covjson_format("") is None
        True
        >>> validate_covjson_format("coveragejson") is None
        True

        Any other format is rejected:

        >>> validate_covjson_format("png")
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: Unsupported format 'png': ...
    """
    if f and f.casefold() != "coveragejson":
        msg = f"Unsupported format {f!r}: only 'CoverageJSON' is supported."
        raise BadRequestError(msg)


def reject_vertical_selection(
    z: Annotated[
        str | None,
        Query(description="Vertical level. Not supported by this 2-D endpoint."),
    ] = None,
) -> None:
    """Reject a vertical (``z``) selection on a 2-D point endpoint.

    A single 2-D raster has no vertical dimension to sample, so honoring or
    silently dropping a requested vertical level would be dishonest. Point
    sampling therefore rejects a ``z`` selection outright. This runs as a FastAPI
    dependency for its side effect alone (it returns nothing): the host
    application's titiler exception handlers render the raised error as a 400
    response.

    Args:
        z: The requested vertical level, or ``None`` when unspecified.

    Raises:
        BadRequestError: If ``z`` is a non-empty value.

    Examples:
        An absent or empty ``z`` is accepted (the validator returns ``None``),
        matching the empty-is-absent convention of the other selectors:

        >>> reject_vertical_selection() is None
        True
        >>> reject_vertical_selection("") is None
        True

        A requested vertical level is rejected:

        >>> reject_vertical_selection("850")
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: Vertical selection is not ...
    """
    # Reject on truthiness, not `is not None`: a valueless `?z=` normalizes to
    # "no vertical selection" (accepted), matching validate_covjson_format's
    # `if f and ...` and CovJSONBandParams' `x or None`. The 2-D backing cannot
    # sample a vertical level; see docs/adr/0001-covjson-http-api-direction.md.
    if z:
        msg = (
            "Vertical selection is not supported by this endpoint: it samples a "
            "single 2-D raster, which has no vertical dimension. Remove the `z` "
            "parameter."
        )
        raise BadRequestError(msg)


def area_stat(
    stat: Annotated[
        str,
        Query(
            description=(
                "Zonal reduction statistic over the polygon: one of min, max, "
                "mean, median, std, sum, count. Defaults to mean."
            ),
        ),
    ] = "mean",
) -> Stat:
    """Parse and validate the ``stat`` zonal-reduction selector for ``/area``.

    Resolves the ``stat`` query value to a :class:`~titiler_covjson.reduce.Stat`
    member (case-insensitively), defaulting to the mean. An unrecognized value is
    rejected with ``BadRequestError`` (a 400, consistent with the other selector
    guards) rather than FastAPI's native 422 for an invalid enum.

    Args:
        stat: The requested statistic name, defaulting to ``"mean"``.

    Returns:
        Stat: The resolved reduction statistic.

    Raises:
        BadRequestError: If ``stat`` is not a recognized statistic. The host
            application's titiler exception handlers render this as a 400
            response.

    Examples:
        >>> area_stat().value
        'mean'
        >>> area_stat("MEDIAN").value
        'median'
        >>> area_stat("bogus")
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: Unsupported stat 'bogus': ...
    """
    try:
        return Stat(stat.casefold())
    except ValueError:
        allowed = ", ".join(member.value for member in Stat)
        msg = f"Unsupported stat {stat!r}: expected one of {allowed}."
        raise BadRequestError(msg) from None


def _band_name_to_index(name: str) -> int:
    """Map a rio-tiler band identifier (``b1``, ``b2``, ...) to a 1-based index.

    Args:
        name: A rio-tiler band identifier such as ``b1``.

    Returns:
        int: The 1-based band index.

    Raises:
        BadRequestError: If ``name`` is not a ``b<N>`` identifier. The host
            application's titiler exception handlers render this as a 400
            response.

    Examples:
        The digits after ``b`` become the 1-based index, and surrounding
        whitespace is ignored:

        >>> _band_name_to_index("b1")
        1
        >>> _band_name_to_index(" b12 ")
        12

        Anything that is not a ``b<N>`` identifier is rejected:

        >>> _band_name_to_index("temperature")
        Traceback (most recent call last):
            ...
        titiler.core.errors.BadRequestError: Invalid parameter-name 'temperature': ...
    """
    if (match := _BAND_NAME.match(name.strip())) is None:
        msg = (
            f"Invalid parameter-name {name!r}: expected a rio-tiler band "
            "identifier such as 'b1'."
        )
        raise BadRequestError(msg)

    return int(match["idx"])
