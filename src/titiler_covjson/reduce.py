"""Per-band statistical reduction of a masked array to one scalar per band.

Reduces a ``(bands, ...)`` masked array over its non-band axes to a 1-D
``(bands,)`` masked array, one summary statistic per band. This is the
zonal-reduction step behind an area query: a raster clipped to a polygon (its
outside-polygon and nodata pixels masked) is reduced to a single value per band.
A band with no valid pixels reduces to a masked scalar, which serializes as JSON
``null``; ``count`` instead reports ``0`` valid pixels.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, assert_never

import numpy as np


class Stat(Enum):
    """A supported zonal-reduction statistic.

    Each member's lowercase ``value`` is the wire form accepted from a request
    (e.g. ``Stat.MEAN.value == "mean"``), and it doubles as the human-readable
    ``label`` in a coverage parameter (``"mean of <band>"``). The exceptions are
    ``count`` and ``std``, whose bare values are ambiguous labels, so they read
    ``"valid pixel count"`` and ``"population standard deviation"``.
    ``MIN``/``MAX``/``SUM`` keep the source integer or float kind;
    ``MEAN``/``MEDIAN``/``STD`` promote to float; ``COUNT`` is an integer,
    dimensionless count of valid pixels (the one reduction that drops the unit).

    ``STD`` is deliberately the population standard deviation (normalized by
    ``N``) rather than the sample standard deviation (normalized by ``N - 1``):
    the pixels reduced are the whole population being described, not a sample
    drawn from a larger one. Note that this is numpy's default but not that of
    every statistical tool, so a cross-check against one that samples by default
    will differ, the more so the fewer pixels are reduced.
    """

    MIN = "min"
    MAX = "max"
    MEAN = "mean"
    MEDIAN = "median"
    STD = "std"
    SUM = "sum"
    COUNT = "count"

    @property
    def label(self) -> str:
        """A human-readable phrase naming the reduction, for a coverage parameter.

        The wire ``value`` doubles as the label for every reduction whose name
        reads well (``"mean"``, ``"sum"``, ...). Two are ambiguous on their own
        and spell themselves out instead: a bare ``"count"`` does not say what is
        counted, and a bare ``"std"`` does not say which convention it follows,
        when the population and sample standard deviations are different numbers.
        The label is the reduction's only description that travels with the
        coverage, so it carries what a reader of the response would otherwise
        have to guess.

        Returns:
            str: The reduction's human-readable name.

        Examples:
            >>> Stat.MEAN.label
            'mean'
            >>> Stat.COUNT.label
            'valid pixel count'
            >>> Stat.STD.label
            'population standard deviation'
        """
        if self is Stat.COUNT:
            return "valid pixel count"

        if self is Stat.STD:
            return "population standard deviation"

        # Enum.value is typed Any, so bind through an annotated local to keep the
        # declared return type.
        wire: str = self.value

        return wire

    @property
    def preserves_unit(self) -> bool:
        """Whether the reduction keeps the source band's unit.

        Every reduction is of a quantity and carries its unit, except ``count``,
        a dimensionless number of valid pixels.

        Returns:
            bool: ``True`` for every reduction except ``count``.
        """
        return self is not Stat.COUNT


def reduce_each_band(
    data: np.ma.MaskedArray[Any, np.dtype[Any]], stat: Stat
) -> np.ma.MaskedArray[Any, np.dtype[Any]]:
    """Reduce a multi-band masked array to one scalar per band.

    Reduces ``data`` (whose leading axis is bands) over all non-band axes by
    ``stat``, yielding a 1-D ``(bands,)`` masked array. A band with no valid
    (unmasked) pixels reduces to a masked entry for every statistic except
    ``count``, which reports ``0``. The result dtype follows ``stat``, not
    ``data``: ``mean``/``median``/``std`` promote to float, while ``min``/``max``
    and ``sum`` preserve the input kind and ``count`` is integer. (``sum`` over
    an integer raster promotes to a wide integer, so it does not overflow the
    source width.)

    Args:
        data: Values as a masked array whose leading axis is bands. Masked
            entries (nodata, or pixels outside a clip region) are excluded from
            the reduction.
        stat: The statistic to reduce each band by.

    Returns:
        np.ma.MaskedArray: One reduced scalar per band, shaped ``(bands,)``.

    Examples:
        >>> import numpy as np
        >>> data = np.ma.MaskedArray(
        ...     np.array([[[1.0, 2.0], [3.0, 4.0]]], dtype="float32")
        ... )
        >>> reduce_each_band(data, Stat.MEAN).tolist()
        [2.5]

        A band with no valid pixels reduces to masked for most statistics, but
        ``count`` reports zero:

        >>> masked = np.ma.MaskedArray(np.ones((1, 2, 2), dtype="float32"), mask=True)
        >>> reduce_each_band(masked, Stat.MEAN).tolist()
        [None]
        >>> reduce_each_band(masked, Stat.COUNT).tolist()
        [0]
    """
    # Flatten every non-band axis into one so a single axis=1 reduction serves
    # all statistics uniformly (np.ma.median in particular does not take a tuple
    # axis). Reshape preserves the mask.
    flat = data.reshape(data.shape[0], -1)

    # np.ma reductions return Any (numpy's masked-array stubs are untyped), so
    # bind through an annotated local to keep the declared return type.
    reduced: np.ma.MaskedArray[Any, np.dtype[Any]]

    match stat:
        case Stat.MIN:
            reduced = np.ma.min(flat, axis=1)
        case Stat.MAX:
            reduced = np.ma.max(flat, axis=1)
        case Stat.MEAN:
            reduced = np.ma.mean(flat, axis=1)
        case Stat.MEDIAN:
            reduced = np.ma.median(flat, axis=1)  # type: ignore[no-untyped-call]
        case Stat.STD:
            reduced = np.ma.std(flat, axis=1)
        case Stat.SUM:
            reduced = np.ma.sum(flat, axis=1)
        case Stat.COUNT:
            # count is never masked: an all-masked band reports 0 valid pixels
            # rather than null, so wrap the plain ndarray count as a MaskedArray.
            reduced = np.ma.MaskedArray(flat.count(axis=1))
        case _:  # pragma: no cover
            assert_never(stat)

    return reduced
