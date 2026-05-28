"""titiler-covjson: CoverageJSON output format extension for TiTiler."""

from titiler_covjson.helpers import (
    create_spatial_2d_reference,
    create_temporal_reference,
    create_unit,
    crs_to_ogc_uri,
    numpy_dtype_to_ndarray,
    numpy_to_covjson_dtype,
)

__version__ = "0.1.0"

__all__ = [
    "create_spatial_2d_reference",
    "create_temporal_reference",
    "crs_to_ogc_uri",
    "numpy_dtype_to_ndarray",
    "numpy_to_covjson_dtype",
    "create_unit",
]
