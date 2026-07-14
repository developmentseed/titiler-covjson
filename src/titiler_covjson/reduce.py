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

    Each member selects the reduction :func:`reduce_bands` applies over a band's
    valid pixels. ``MIN``/``MAX``/``SUM`` keep the source integer or float kind;
    ``MEAN``/``MEDIAN``/``STD`` promote to float; ``COUNT`` is an integer count of
    valid pixels. Each member's lowercase ``value`` is the string form accepted
    from a request (e.g., ``Stat.MEAN.value == "mean"``).
    """

    MIN = "min"
    MAX = "max"
    MEAN = "mean"
    MEDIAN = "median"
    STD = "std"
    SUM = "sum"
    COUNT = "count"


def reduce_bands(
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
        >>> reduce_bands(data, Stat.MEAN).tolist()
        [2.5]

        A band with no valid pixels reduces to masked for most statistics, but
        ``count`` reports zero:

        >>> masked = np.ma.MaskedArray(np.ones((1, 2, 2), dtype="float32"), mask=True)
        >>> reduce_bands(masked, Stat.MEAN).tolist()
        [None]
        >>> reduce_bands(masked, Stat.COUNT).tolist()
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
